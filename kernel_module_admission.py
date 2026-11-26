"""Kernel module admission: whitelist rules, signature gate, version floor, measurement."""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
import threading
from dataclasses import asdict, dataclass, replace


class KernelModuleError(ValueError):
    """Raised when a module admission operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class VersionRule:
    name: str
    min_version: str
    max_version: str
    allowed_keys: tuple[str, ...]


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    material: str
    not_before: int
    not_after: int
    revoked_at: int | None


@dataclass(frozen=True)
class ModuleRecord:
    name: str
    version: str
    digest: str
    key_id: str
    state: str
    admitted_at: int
    measured_at: int | None
    measures: int


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    version: str
    state: str
    stale: bool


@dataclass(frozen=True)
class Admission:
    accepted: bool
    record: ModuleRecord


_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){2}\Z")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_VERSION = 1
_SNAPSHOT_KEYS = frozenset(
    {"version", "rules", "keys", "modules", "highest", "counters"}
)
_COUNTER_KEYS = (
    "admitted",
    "rejected",
    "loads",
    "unloads",
    "blocks",
    "measures",
    "mismatches",
    "rollbacks",
    "key_revocations",
    "retired",
)
_MODULE_STATES = frozenset({"admitted", "loaded", "quarantined", "blocked"})


def _parse_version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise ValueError("version must be three numeric components")
    parts = value.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _version_text(value: tuple[int, int, int]) -> str:
    return f"{value[0]}.{value[1]}.{value[2]}"


def _is_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int)


class KernelModuleAdmission:
    """Admit, load, measure and block kernel modules from a local whitelist."""

    def __init__(self, *, max_modules: int = 32) -> None:
        self.max_modules = max_modules
        self._lock = threading.RLock()
        self._rules: dict[str, VersionRule] = {}
        self._keys: dict[str, KeyRecord] = {}
        self._modules: dict[str, ModuleRecord] = {}
        self._highest: dict[str, tuple[int, int, int]] = {}
        self._counters: dict[str, int] = {key: 0 for key in _COUNTER_KEYS}
        self._measure_marks: dict[str, set[int]] = {}

    def add_rule(
        self,
        name: str,
        *,
        min_version: str,
        max_version: str,
        allowed_keys: tuple[str, ...],
    ) -> VersionRule:
        with self._lock:
            if not isinstance(name, str) or not name:
                raise KernelModuleError("MODULE_RULE_INVALID", {"name": name})
            if name in self._rules:
                raise KernelModuleError("MODULE_RULE_INVALID", {"name": name})

            try:
                minimum = _parse_version(min_version)
                maximum = _parse_version(max_version)
            except (TypeError, ValueError) as exc:
                raise KernelModuleError(
                    "MODULE_RULE_INVALID", {"name": name}
                ) from exc

            if minimum > maximum:
                raise KernelModuleError(
                    "MODULE_RULE_INVALID",
                    {"min_version": min_version, "max_version": max_version},
                )

            if isinstance(allowed_keys, (str, bytes)):
                raise KernelModuleError("MODULE_RULE_INVALID", {"name": name})
            try:
                raw_keys = tuple(allowed_keys)  # type: ignore[arg-type]
            except TypeError as exc:
                raise KernelModuleError(
                    "MODULE_RULE_INVALID", {"name": name}
                ) from exc

            if not raw_keys or any(
                not isinstance(key, str) or not key for key in raw_keys
            ):
                raise KernelModuleError("MODULE_RULE_INVALID", {"name": name})

            try:
                key_tuple = tuple(sorted(set(raw_keys)))
            except TypeError as exc:
                raise KernelModuleError(
                    "MODULE_RULE_INVALID", {"name": name}
                ) from exc

            rule = VersionRule(
                name=name,
                min_version=min_version,
                max_version=max_version,
                allowed_keys=key_tuple,
            )
            self._rules[name] = rule
            return rule

    def remove_rule(self, name: str) -> bool:
        with self._lock:
            return self._rules.pop(name, None) is not None

    def register_key(
        self,
        key_id: str,
        material: str,
        *,
        not_before: int = 0,
        not_after: int = 4102444800,
    ) -> bool:
        with self._lock:
            if (
                not isinstance(key_id, str)
                or not key_id
                or not isinstance(material, str)
                or not material
                or not _is_int(not_before)
                or not _is_int(not_after)
                or not_before >= not_after
            ):
                raise KernelModuleError(
                    "MODULE_KEY_INVALID", {"key_id": key_id}
                )

            existing = self._keys.get(key_id)
            if existing is not None:
                if existing.material == material:
                    return False
                raise KernelModuleError(
                    "MODULE_KEY_CONFLICT", {"key_id": key_id}
                )

            self._keys[key_id] = KeyRecord(
                key_id=key_id,
                material=material,
                not_before=not_before,
                not_after=not_after,
                revoked_at=None,
            )
            return True

    def revoke_key(self, key_id: str, *, now: int) -> bool:
        with self._lock:
            record = self._keys.get(key_id)
            if record is None:
                raise KernelModuleError(
                    "MODULE_KEY_UNKNOWN", {"key_id": key_id}
                )
            if record.revoked_at is not None:
                return False

            self._keys[key_id] = replace(record, revoked_at=now)
            self._counters["key_revocations"] += 1
            return True

    def expected_signature(
        self, key_id: str, name: str, version: str, digest: str
    ) -> str:
        with self._lock:
            record = self._keys.get(key_id) if isinstance(key_id, str) else None
            if record is None:
                raise KernelModuleError(
                    "MODULE_KEY_UNKNOWN", {"key_id": key_id}
                )

            payload = "|".join(
                (key_id, record.material, name, version, digest)
            ).encode("utf-8")
            return hashlib.sha256(payload).hexdigest()

    def admit(
        self,
        name: str,
        version: str,
        digest: str,
        signature: str,
        *,
        key_id: str,
        now: int,
        allow_rollback: bool = False,
    ) -> Admission:
        with self._lock:

            def reject(code: str, detail: object = None) -> KernelModuleError:
                self._counters["rejected"] += 1
                return KernelModuleError(code, detail)

            rule = self._rules.get(name) if isinstance(name, str) else None
            if rule is None:
                raise reject("MODULE_NOT_ALLOWED", {"name": name})

            try:
                requested_version = _parse_version(version)
                minimum = _parse_version(rule.min_version)
                maximum = _parse_version(rule.max_version)
            except (TypeError, ValueError):
                raise reject(
                    "MODULE_VERSION_DENIED",
                    {"name": name, "version": version},
                )

            if not minimum <= requested_version <= maximum:
                raise reject(
                    "MODULE_VERSION_DENIED",
                    {"name": name, "version": version},
                )

            if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
                raise reject("MODULE_DIGEST_INVALID", {"digest": digest})

            key = self._keys.get(key_id) if isinstance(key_id, str) else None
            if key is None:
                raise reject("MODULE_KEY_UNKNOWN", {"key_id": key_id})

            if now < key.not_before or now > key.not_after:
                raise reject(
                    "MODULE_KEY_EXPIRED",
                    {"key_id": key_id, "now": now},
                )

            if key.revoked_at is not None:
                raise reject(
                    "MODULE_KEY_REVOKED",
                    {"key_id": key_id, "revoked_at": key.revoked_at},
                )

            if key_id not in rule.allowed_keys:
                raise reject("MODULE_KEY_DENIED", {"key_id": key_id})

            expected = self.expected_signature(
                key_id, name, version, digest
            )
            if (
                not isinstance(signature, str)
                or _SIGNATURE_RE.fullmatch(signature) is None
                or not hmac.compare_digest(signature, expected)
            ):
                raise reject("MODULE_SIGNATURE_INVALID", {"name": name})

            existing = self._modules.get(name)
            if existing is not None:
                if existing.version == version and existing.digest == digest:
                    return Admission(accepted=False, record=existing)
                raise reject(
                    "MODULE_DIGEST_CONFLICT",
                    {
                        "name": name,
                        "existing_digest": existing.digest,
                        "requested_digest": digest,
                    },
                )

            highest_version = self._highest.get(name)
            if (
                highest_version is not None
                and requested_version < highest_version
                and not allow_rollback
            ):
                self._counters["rollbacks"] += 1
                raise reject(
                    "MODULE_ROLLBACK_DENIED",
                    {
                        "name": name,
                        "version": version,
                        "highest": _version_text(highest_version),
                    },
                )

            if len(self._modules) >= self.max_modules:
                raise reject(
                    "MODULE_CAPACITY_EXCEEDED",
                    {"max_modules": self.max_modules},
                )

            record = ModuleRecord(
                name=name,
                version=version,
                digest=digest,
                key_id=key_id,
                state="admitted",
                admitted_at=now,
                measured_at=None,
                measures=0,
            )
            self._modules[name] = record
            self._measure_marks[name] = set()
            self._counters["admitted"] += 1
            return Admission(accepted=True, record=record)

    def load(self, name: str, *, now: int) -> bool:
        with self._lock:
            record = self._require_record(name)

            key = self._keys.get(record.key_id)
            if key is None:
                raise KernelModuleError(
                    "MODULE_KEY_UNKNOWN", {"key_id": record.key_id}
                )
            if key.revoked_at is not None:
                raise KernelModuleError(
                    "MODULE_KEY_REVOKED", {"key_id": record.key_id}
                )

            if record.state in ("blocked", "quarantined"):
                raise KernelModuleError("MODULE_BLOCKED", {"name": name})

            if record.state == "loaded":
                return False

            self._modules[name] = replace(record, state="loaded")
            version = _parse_version(record.version)
            previous = self._highest.get(name)
            if previous is None or version > previous:
                self._highest[name] = version

            self._counters["loads"] += 1
            return True

    def unload(self, name: str, *, now: int) -> bool:
        with self._lock:
            record = self._require_record(name)
            if record.state != "loaded":
                return False

            self._modules[name] = replace(record, state="admitted")
            self._counters["unloads"] += 1
            return True

    def measure(self, name: str, digest: str, *, now: int) -> bool:
        with self._lock:
            record = self._require_record(name)

            key = self._keys.get(record.key_id)
            if key is None:
                raise KernelModuleError(
                    "MODULE_KEY_UNKNOWN", {"key_id": record.key_id}
                )
            if key.revoked_at is not None:
                raise KernelModuleError(
                    "MODULE_KEY_REVOKED", {"key_id": record.key_id}
                )

            marks = self._measure_marks.setdefault(name, set())
            if now in marks or (
                record.measured_at is not None and record.measured_at == now
            ):
                return False
            if record.state != "loaded":
                raise KernelModuleError("MODULE_NOT_LOADED", {"name": name})
            marks.add(now)

            if digest == record.digest:
                updated = replace(
                    record,
                    measures=record.measures + 1,
                    measured_at=now,
                )
                self._modules[name] = updated
                self._counters["measures"] += 1
                return True

            self._modules[name] = replace(record, state="quarantined")
            self._counters["mismatches"] += 1
            return False

    def block(self, name: str, reason: str, *, now: int) -> bool:
        with self._lock:
            record = self._require_record(name)
            if record.state == "blocked":
                return False

            was_loaded = record.state == "loaded"
            self._modules[name] = replace(record, state="blocked")
            self._counters["blocks"] += 1
            if was_loaded:
                self._counters["unloads"] += 1
            return True

    def unblock(self, name: str) -> bool:
        with self._lock:
            record = self._require_record(name)
            if record.state not in ("blocked", "quarantined"):
                return False

            self._modules[name] = replace(record, state="admitted")
            return True

    def retire(self, name: str) -> bool:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                return False
            if record.state == "loaded":
                raise KernelModuleError("MODULE_BLOCKED", {"name": name})

            del self._modules[name]
            self._measure_marks.pop(name, None)
            self._counters["retired"] += 1
            return True

    def module_of(self, name: str) -> ModuleRecord:
        with self._lock:
            return self._require_record(name)

    def loaded_modules(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(
                    name
                    for name, record in self._modules.items()
                    if record.state == "loaded"
                )
            )

    def effective_manifest(self) -> tuple[ManifestEntry, ...]:
        with self._lock:
            entries = []
            for name in sorted(self._modules):
                record = self._modules[name]
                key = self._keys.get(record.key_id)
                entries.append(
                    ManifestEntry(
                        name=name,
                        version=record.version,
                        state=record.state,
                        stale=key is not None and key.revoked_at is not None,
                    )
                )
            return tuple(entries)

    def stats(self) -> dict:
        with self._lock:
            return dict(self._counters)

    def snapshot(self) -> dict:
        with self._lock:
            state = {
                "version": _SNAPSHOT_VERSION,
                "rules": {
                    name: asdict(rule) for name, rule in self._rules.items()
                },
                "keys": {
                    key_id: asdict(record)
                    for key_id, record in self._keys.items()
                },
                "modules": {
                    name: asdict(record)
                    for name, record in self._modules.items()
                },
                "highest": {
                    name: _version_text(version)
                    for name, version in self._highest.items()
                },
                "counters": dict(self._counters),
            }
            return copy.deepcopy(state)

    def restore(self, state: dict) -> None:
        try:
            (
                rules,
                keys,
                modules,
                highest,
                counters,
                measure_marks,
            ) = self._parse_snapshot(state)
        except Exception as exc:
            if isinstance(exc, KernelModuleError) and (
                exc.code == "MODULE_SNAPSHOT_INVALID"
            ):
                raise
            raise KernelModuleError("MODULE_SNAPSHOT_INVALID") from None

        with self._lock:
            self._rules = rules
            self._keys = keys
            self._modules = modules
            self._highest = highest
            self._counters = counters
            self._measure_marks = measure_marks

    def _require_record(self, name: str) -> ModuleRecord:
        record = self._modules.get(name) if isinstance(name, str) else None
        if record is None:
            raise KernelModuleError("MODULE_UNKNOWN", {"name": name})
        return record

    def _parse_snapshot(
        self, state: object
    ) -> tuple[
        dict[str, VersionRule],
        dict[str, KeyRecord],
        dict[str, ModuleRecord],
        dict[str, tuple[int, int, int]],
        dict[str, int],
        dict[str, set[int]],
    ]:
        if not isinstance(state, dict) or frozenset(state) != _SNAPSHOT_KEYS:
            raise ValueError("snapshot must contain the supported top-level keys")
        if (
            not _is_int(state["version"])
            or state["version"] != _SNAPSHOT_VERSION
        ):
            raise ValueError("unsupported snapshot version")

        raw_rules = state["rules"]
        if not isinstance(raw_rules, dict):
            raise ValueError("rules must be a mapping")
        rules: dict[str, VersionRule] = {}
        for rule_name, raw_rule in raw_rules.items():
            if not isinstance(rule_name, str) or not rule_name:
                raise ValueError("invalid rule name")
            if not isinstance(raw_rule, dict) or frozenset(raw_rule) != {
                "name",
                "min_version",
                "max_version",
                "allowed_keys",
            }:
                raise ValueError("invalid rule record")
            if raw_rule["name"] != rule_name:
                raise ValueError("rule name mismatch")

            minimum = _parse_version(raw_rule["min_version"])
            maximum = _parse_version(raw_rule["max_version"])
            if minimum > maximum:
                raise ValueError("invalid rule range")

            raw_allowed = raw_rule["allowed_keys"]
            if isinstance(raw_allowed, (str, bytes)) or not isinstance(
                raw_allowed, (list, tuple)
            ):
                raise ValueError("allowed_keys must be a list or tuple")
            if not raw_allowed or any(
                not isinstance(key, str) or not key for key in raw_allowed
            ):
                raise ValueError("allowed_keys must contain non-empty strings")
            if len(set(raw_allowed)) != len(raw_allowed):
                raise ValueError("allowed_keys must be unique")
            allowed_keys = tuple(raw_allowed)
            if allowed_keys != tuple(sorted(allowed_keys)):
                raise ValueError("allowed_keys must be sorted")

            rules[rule_name] = VersionRule(
                name=rule_name,
                min_version=raw_rule["min_version"],
                max_version=raw_rule["max_version"],
                allowed_keys=allowed_keys,
            )

        raw_keys = state["keys"]
        if not isinstance(raw_keys, dict):
            raise ValueError("keys must be a mapping")
        keys: dict[str, KeyRecord] = {}
        for key_id, raw_key in raw_keys.items():
            if not isinstance(key_id, str) or not key_id:
                raise ValueError("invalid key id")
            if not isinstance(raw_key, dict) or frozenset(raw_key) != {
                "key_id",
                "material",
                "not_before",
                "not_after",
                "revoked_at",
            }:
                raise ValueError("invalid key record")
            if raw_key["key_id"] != key_id:
                raise ValueError("key id mismatch")
            if (
                not isinstance(raw_key["material"], str)
                or not raw_key["material"]
                or not _is_int(raw_key["not_before"])
                or not _is_int(raw_key["not_after"])
                or raw_key["not_before"] >= raw_key["not_after"]
                or (
                    raw_key["revoked_at"] is not None
                    and not _is_int(raw_key["revoked_at"])
                )
            ):
                raise ValueError("invalid key fields")

            keys[key_id] = KeyRecord(
                key_id=key_id,
                material=raw_key["material"],
                not_before=raw_key["not_before"],
                not_after=raw_key["not_after"],
                revoked_at=raw_key["revoked_at"],
            )

        raw_modules = state["modules"]
        if not isinstance(raw_modules, dict):
            raise ValueError("modules must be a mapping")
        if len(raw_modules) > self.max_modules:
            raise ValueError("snapshot exceeds module capacity")

        modules: dict[str, ModuleRecord] = {}
        measure_marks: dict[str, set[int]] = {}
        for module_name, raw_module in raw_modules.items():
            if not isinstance(module_name, str) or not module_name:
                raise ValueError("invalid module name")
            if not isinstance(raw_module, dict) or frozenset(raw_module) != {
                "name",
                "version",
                "digest",
                "key_id",
                "state",
                "admitted_at",
                "measured_at",
                "measures",
            }:
                raise ValueError("invalid module record")
            if raw_module["name"] != module_name:
                raise ValueError("module name mismatch")

            _parse_version(raw_module["version"])
            if (
                not isinstance(raw_module["digest"], str)
                or _DIGEST_RE.fullmatch(raw_module["digest"]) is None
            ):
                raise ValueError("invalid module digest")
            if (
                not isinstance(raw_module["key_id"], str)
                or raw_module["key_id"] not in keys
                or raw_module["state"] not in _MODULE_STATES
                or not _is_int(raw_module["admitted_at"])
                or (
                    raw_module["measured_at"] is not None
                    and not _is_int(raw_module["measured_at"])
                )
                or not _is_int(raw_module["measures"])
                or raw_module["measures"] < 0
            ):
                raise ValueError("invalid module fields")

            modules[module_name] = ModuleRecord(
                name=module_name,
                version=raw_module["version"],
                digest=raw_module["digest"],
                key_id=raw_module["key_id"],
                state=raw_module["state"],
                admitted_at=raw_module["admitted_at"],
                measured_at=raw_module["measured_at"],
                measures=raw_module["measures"],
            )
            if raw_module["measured_at"] is not None:
                measure_marks[module_name] = {raw_module["measured_at"]}
            else:
                measure_marks[module_name] = set()

        raw_highest = state["highest"]
        if not isinstance(raw_highest, dict):
            raise ValueError("highest must be a mapping")
        highest: dict[str, tuple[int, int, int]] = {}
        for module_name, raw_version in raw_highest.items():
            if not isinstance(module_name, str) or not module_name:
                raise ValueError("invalid highest module name")
            if isinstance(raw_version, str):
                highest[module_name] = _parse_version(raw_version)
            elif (
                isinstance(raw_version, (list, tuple))
                and len(raw_version) == 3
                and all(_is_int(part) and part >= 0 for part in raw_version)
            ):
                try:
                    highest[module_name] = (
                        int(raw_version[0]),
                        int(raw_version[1]),
                        int(raw_version[2]),
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid highest version") from exc
            else:
                raise ValueError("invalid highest version")

        raw_counters = state["counters"]
        if not isinstance(raw_counters, dict) or frozenset(
            raw_counters
        ) != frozenset(_COUNTER_KEYS):
            raise ValueError("counters are incomplete")
        counters: dict[str, int] = {}
        for key in _COUNTER_KEYS:
            value = raw_counters[key]
            if not _is_int(value) or value < 0:
                raise ValueError(f"counter {key} must be non-negative integer")
            counters[key] = value

        return rules, keys, modules, highest, counters, measure_marks
