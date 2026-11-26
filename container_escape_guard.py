"""Container creation admission: dangerous mounts, capability inflation and
host namespace exposure."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading


class ContainerEscapeError(ValueError):
    """Raised when a spec is malformed or a hard capacity limit is hit."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


_DEFAULT_CAPABILITIES = frozenset({
    "CAP_CHOWN",
    "CAP_DAC_OVERRIDE",
    "CAP_FOWNER",
    "CAP_KILL",
    "CAP_SETGID",
    "CAP_SETUID",
    "CAP_NET_BIND_SERVICE",
    "CAP_NET_RAW",
    "CAP_SYS_CHROOT",
    "CAP_MKNOD",
    "CAP_AUDIT_WRITE",
    "CAP_SETFCAP",
})

_DANGEROUS_CAPABILITIES = frozenset({
    "CAP_SYS_ADMIN",
    "CAP_SYS_PTRACE",
    "CAP_SYS_MODULE",
    "CAP_DAC_READ_SEARCH",
    "CAP_NET_ADMIN",
    "CAP_SYS_BOOT",
    "CAP_SYS_RAWIO",
})

_KNOWN_CAPABILITIES = _DEFAULT_CAPABILITIES | _DANGEROUS_CAPABILITIES

_SENSITIVE_SOURCES = (
    "/etc",
    "/root",
    "/home",
    "/boot",
    "/var/lib/docker",
    "/run/secrets",
)

_PSEUDO_SOURCES = ("/proc", "/sys", "/dev")
_PSEUDO_TARGETS = ("/proc", "/sys", "/dev", "/boot")

_MOUNT_CLASSIFICATION = {
    "host_root": ("ESC_MOUNT_HOST_ROOT", "critical"),
    "control": ("ESC_MOUNT_CONTROL_SOCKET", "critical"),
    "pseudo_fs": ("ESC_MOUNT_PSEUDO_FS", "high"),
    "sensitive": ("ESC_MOUNT_SENSITIVE", "medium"),
    "shared": ("ESC_MOUNT_SHARED_PROPAGATION", "high"),
}

_NAMESPACE_FLAGS = (
    ("host_ipc", "ipc"),
    ("host_mount", "mount"),
    ("host_network", "network"),
    ("host_pid", "pid"),
    ("host_user", "user"),
    ("host_uts", "uts"),
)

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2}

_COUNTER_KEYS = (
    "evaluations",
    "allowed",
    "reviewed",
    "denied",
    "quarantined_hits",
    "findings",
    "findings_dropped",
    "releases",
)

_CONFIG_FIELDS = (
    "max_mounts",
    "max_findings",
    "quarantine_after",
    "quarantine_ttl",
    "max_profiles",
    "max_path_len",
)

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")

_MISSING = object()


def _is_at_or_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


class ContainerEscapeGuard:
    """Score container specs and quarantine repeat offenders by container key."""

    def __init__(self, *, max_mounts: int = 16, max_findings: int = 64,
                 quarantine_after: int = 3, quarantine_ttl: int = 300,
                 max_profiles: int = 8, max_path_len: int = 256) -> None:
        config = {
            "max_mounts": max_mounts,
            "max_findings": max_findings,
            "quarantine_after": quarantine_after,
            "quarantine_ttl": quarantine_ttl,
            "max_profiles": max_profiles,
            "max_path_len": max_path_len,
        }
        self._validate_config(config, code="ESC_SPEC_INVALID")
        for field, value in config.items():
            setattr(self, field, value)

        self._lock = threading.RLock()
        self._profiles: dict[str, dict] = {}
        self._quarantine: dict[str, dict] = {}
        self._ledger: dict[str, list] = {}
        self._counters = {name: 0 for name in _COUNTER_KEYS}

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_config(config: dict, *, code: str) -> None:
        for field in _CONFIG_FIELDS:
            value = config.get(field, _MISSING)
            if (value is _MISSING or not isinstance(value, int)
                    or isinstance(value, bool)):
                raise ContainerEscapeError(
                    code, {"field": field, "value": value}
                )
            minimum = 1 if field in ("quarantine_after", "max_path_len") else 0
            if value < minimum:
                raise ContainerEscapeError(
                    code, {"field": field, "value": value}
                )

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def _fingerprint(cls, spec: dict) -> str:
        try:
            payload = cls._canonical_json(spec)
        except (TypeError, ValueError) as exc:
            raise ContainerEscapeError("ESC_SPEC_INVALID", str(exc)) from exc
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _normalize_capability(name: object) -> str:
        if not isinstance(name, str):
            raise ContainerEscapeError("ESC_CAP_UNKNOWN", name)
        normalized = name.strip().upper()
        if not normalized:
            raise ContainerEscapeError("ESC_CAP_UNKNOWN", name)
        if not normalized.startswith("CAP_"):
            normalized = "CAP_" + normalized
        return normalized

    # ------------------------------------------------------------------
    # Public analysis primitives
    # ------------------------------------------------------------------
    def normalize_path(self, path: str) -> str:
        if not isinstance(path, str):
            raise ContainerEscapeError("ESC_PATH_INVALID", path)
        path = path.strip()
        if not path or not path.startswith("/"):
            raise ContainerEscapeError("ESC_PATH_INVALID", path)
        if any(segment == ".." for segment in path.split("/")):
            raise ContainerEscapeError("ESC_PATH_INVALID", path)
        if any(char.isspace() for char in path):
            raise ContainerEscapeError("ESC_PATH_INVALID", path)

        segments = [
            segment for segment in path.split("/")
            if segment and segment != "."
        ]
        normalized = "/" + "/".join(segments) if segments else "/"
        if len(normalized) > self.max_path_len:
            raise ContainerEscapeError(
                "ESC_PATH_INVALID",
                {"path": normalized, "length": len(normalized)},
            )
        return normalized

    def classify_mount(self, source: str, target: str, *,
                       propagation: str = "rprivate",
                       read_only: bool = False) -> str:
        source = self.normalize_path(source)
        target = self.normalize_path(target)

        if source == "/":
            return "host_root"
        if source.endswith(".sock"):
            return "control"
        if (any(_is_at_or_under(source, root) for root in _PSEUDO_SOURCES)
                or any(_is_at_or_under(target, root)
                       for root in _PSEUDO_TARGETS)):
            return "pseudo_fs"

        shared_propagation = (
            isinstance(propagation, str)
            and propagation.lower() in ("shared", "rshared")
        )
        if not read_only and shared_propagation:
            return "shared"
        if any(_is_at_or_under(source, root) for root in _SENSITIVE_SOURCES):
            return "sensitive"
        return "safe"

    def check_capabilities(self, add=(), drop=()) -> dict:
        add_names = [self._normalize_capability(name) for name in add]
        drop_names = [self._normalize_capability(name) for name in drop]

        add_set = set(add_names)
        drop_set = set(drop_names)
        conflict = sorted(add_set & drop_set)
        if conflict:
            raise ContainerEscapeError("ESC_CAP_CONFLICT", conflict)

        for name in sorted(add_set | drop_set):
            if name == "CAP_ALL":
                continue
            if name not in _KNOWN_CAPABILITIES:
                raise ContainerEscapeError("ESC_CAP_UNKNOWN", name)

        if "CAP_ALL" in add_set:
            raise ContainerEscapeError("ESC_CAP_UNKNOWN", "CAP_ALL")

        if "CAP_ALL" in drop_set:
            effective = set()
        else:
            effective = set(_DEFAULT_CAPABILITIES)
        effective -= drop_set
        effective |= add_set

        dangerous = effective & _DANGEROUS_CAPABILITIES
        return {
            "effective": tuple(sorted(effective)),
            "dangerous": tuple(sorted(dangerous)),
            "allowed": not dangerous,
        }

    def namespace_plan(self, spec: dict) -> dict:
        if not isinstance(spec, dict):
            raise ContainerEscapeError("ESC_SPEC_INVALID", spec)

        shared = tuple(sorted(
            name for flag, name in _NAMESPACE_FLAGS
            if spec.get(flag) is True
        ))
        if spec.get("privileged") is True:
            score = 6
        else:
            score = len(shared)
            if spec.get("host_pid") is True:
                score += 2
            if spec.get("host_network") is True:
                score += 1
        return {"shared": shared, "score": score}

    # ------------------------------------------------------------------
    # Spec handling
    # ------------------------------------------------------------------
    def _resolve_spec(self, spec: dict) -> tuple[str, dict]:
        if not isinstance(spec, dict):
            raise ContainerEscapeError("ESC_SPEC_INVALID", spec)

        key = spec.get("id", _MISSING)
        if (key is _MISSING or not isinstance(key, str)
                or not key.strip()):
            raise ContainerEscapeError("ESC_SPEC_INVALID", {"field": "id"})

        profile_name = spec.get("profile", _MISSING)
        if profile_name is not _MISSING:
            if (not isinstance(profile_name, str)
                    or not profile_name.strip()):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID", {"field": "profile"}
                )
            profile = self._profiles.get(profile_name)
            if profile is None:
                raise ContainerEscapeError("ESC_PROFILE_UNKNOWN", profile_name)
            merged = copy.deepcopy(profile["spec"])
            for field, value in spec.items():
                merged[field] = copy.deepcopy(value)
        else:
            merged = copy.deepcopy(spec)

        merged["id"] = key
        return key, merged

    def _validate_spec_fields(self, spec: dict) -> dict:
        mounts = spec.get("mounts", [])
        if not isinstance(mounts, list):
            raise ContainerEscapeError("ESC_SPEC_INVALID", {"field": "mounts"})
        if len(mounts) > self.max_mounts:
            raise ContainerEscapeError(
                "ESC_CAPACITY_EXCEEDED",
                {"mounts": len(mounts), "max_mounts": self.max_mounts},
            )

        normalized_mounts = []
        for index, mount in enumerate(mounts):
            if not isinstance(mount, dict):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID",
                    {"field": f"mounts[{index}]"},
                )
            source = mount.get("source", _MISSING)
            target = mount.get("target", _MISSING)
            if not isinstance(source, str) or not isinstance(target, str):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID",
                    {"field": f"mounts[{index}]"},
                )
            read_only = mount.get("read_only", False)
            propagation = mount.get("propagation", "rprivate")
            if not isinstance(read_only, bool):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID",
                    {"field": f"mounts[{index}].read_only"},
                )
            if not isinstance(propagation, str):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID",
                    {"field": f"mounts[{index}].propagation"},
                )
            normalized_mounts.append({
                "source": source,
                "target": target,
                "read_only": read_only,
                "propagation": propagation,
            })

        cap_add = spec.get("cap_add", [])
        cap_drop = spec.get("cap_drop", [])
        if not isinstance(cap_add, list) or not isinstance(cap_drop, list):
            raise ContainerEscapeError(
                "ESC_SPEC_INVALID", {"field": "capabilities"}
            )
        for index, name in enumerate(cap_add):
            if not isinstance(name, str):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID", {"field": f"cap_add[{index}]"}
                )
        for index, name in enumerate(cap_drop):
            if not isinstance(name, str):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID", {"field": f"cap_drop[{index}]"}
                )

        flags = {}
        for field in (
            "host_pid",
            "host_network",
            "host_ipc",
            "host_uts",
            "host_user",
            "host_mount",
            "privileged",
        ):
            value = spec.get(field, False)
            if not isinstance(value, bool):
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID", {"field": field}
                )
            flags[field] = value

        return {
            "mounts": normalized_mounts,
            "cap_add": list(cap_add),
            "cap_drop": list(cap_drop),
            **flags,
        }

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _build_findings(self, spec: dict) -> tuple[list, int]:
        candidates = []

        for mount in spec["mounts"]:
            classification = self.classify_mount(
                mount["source"],
                mount["target"],
                propagation=mount["propagation"],
                read_only=mount["read_only"],
            )
            if classification == "safe":
                continue
            code, severity = _MOUNT_CLASSIFICATION[classification]
            target = self.normalize_path(mount["target"])
            candidates.append({
                "code": code,
                "severity": severity,
                "target": target,
                "detail": {
                    "source": self.normalize_path(mount["source"]),
                    "target": target,
                    "classification": classification,
                    "read_only": mount["read_only"],
                    "propagation": mount["propagation"],
                },
            })

        capabilities = self.check_capabilities(
            spec["cap_add"], spec["cap_drop"]
        )
        if capabilities["dangerous"]:
            candidates.append({
                "code": "ESC_CAP_DANGEROUS",
                "severity": "critical",
                "target": "",
                "detail": {
                    "capabilities": list(capabilities["dangerous"]),
                },
            })

        if spec["host_pid"]:
            candidates.append({
                "code": "ESC_HOST_PID",
                "severity": "critical",
                "target": "",
                "detail": {"namespace": "pid"},
            })
        if spec["host_network"]:
            candidates.append({
                "code": "ESC_HOST_NETWORK",
                "severity": "high",
                "target": "",
                "detail": {"namespace": "network"},
            })

        other_namespaces = [
            name for field, name in _NAMESPACE_FLAGS
            if name not in ("pid", "network") and spec[field]
        ]
        if other_namespaces:
            candidates.append({
                "code": "ESC_HOST_NAMESPACE",
                "severity": "high",
                "target": "",
                "detail": {"namespaces": sorted(other_namespaces)},
            })

        if spec["privileged"]:
            candidates.append({
                "code": "ESC_PRIVILEGED",
                "severity": "critical",
                "target": "",
                "detail": {"privileged": True},
            })

        if spec["host_pid"] and spec["host_network"]:
            for finding in candidates:
                if finding["code"] == "ESC_HOST_NETWORK":
                    finding["severity"] = "critical"
                    finding["detail"]["combination"] = (
                        "host_pid+host_network"
                    )

        candidates.sort(key=lambda finding: (
            _SEVERITY_RANK[finding["severity"]],
            finding["code"],
            finding["target"],
            self._canonical_json(finding["detail"]),
        ))

        unique = []
        seen = set()
        for finding in candidates:
            identity = (finding["code"], finding["target"])
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(finding)

        dropped = max(0, len(unique) - self.max_findings)
        return unique[:self.max_findings], dropped

    @staticmethod
    def _verdict(findings: list) -> str:
        severities = {finding["severity"] for finding in findings}
        if "critical" in severities:
            return "deny"
        if "high" in severities or "medium" in severities:
            return "review"
        return "allow"

    def evaluate(self, spec: dict, *, now: int = 0) -> dict:
        with self._lock:
            key, merged = self._resolve_spec(spec)
            effective = self._validate_spec_fields(merged)

            state = self._quarantine.get(key)
            if state is not None and state["until"] > 0:
                if now < state["until"]:
                    self._counters["quarantined_hits"] += 1
                    return {
                        "key": key,
                        "verdict": "quarantined",
                        "findings": [],
                        "quarantined": True,
                        "until": state["until"],
                    }
                del self._quarantine[key]

            findings, dropped = self._build_findings(effective)
            verdict = self._verdict(findings)

            self._counters["evaluations"] += 1
            self._counters["findings"] += len(findings)
            self._counters["findings_dropped"] += dropped

            quarantined = False
            until = 0
            if verdict == "allow":
                self._counters["allowed"] += 1
                self._quarantine.pop(key, None)
            elif verdict == "review":
                self._counters["reviewed"] += 1
                self._quarantine.pop(key, None)
            else:
                self._counters["denied"] += 1
                state = self._quarantine.setdefault(
                    key, {"until": 0, "denials": 0}
                )
                state["denials"] += 1
                if state["denials"] >= self.quarantine_after:
                    state["until"] = now + self.quarantine_ttl
                    quarantined = True
                    until = state["until"]

            self._ledger[key] = copy.deepcopy(findings)

            return {
                "key": key,
                "verdict": verdict,
                "findings": copy.deepcopy(findings),
                "quarantined": quarantined,
                "until": until,
            }

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    def register_profile(self, name: str, spec: dict) -> dict:
        with self._lock:
            if not isinstance(name, str) or not name.strip():
                raise ContainerEscapeError(
                    "ESC_SPEC_INVALID", {"field": "name"}
                )
            if (name not in self._profiles
                    and len(self._profiles) >= self.max_profiles):
                raise ContainerEscapeError(
                    "ESC_CAPACITY_EXCEEDED",
                    {"profiles": len(self._profiles),
                     "max_profiles": self.max_profiles},
                )
            if not isinstance(spec, dict):
                raise ContainerEscapeError("ESC_SPEC_INVALID", spec)

            fingerprint = self._fingerprint(spec)
            self._profiles[name] = {
                "spec": copy.deepcopy(spec),
                "fingerprint": fingerprint,
            }
            return {"name": name, "fingerprint": fingerprint}

    def profiles(self) -> list:
        with self._lock:
            return [
                {
                    "name": name,
                    "fingerprint": self._profiles[name]["fingerprint"],
                }
                for name in sorted(self._profiles)
            ]

    # ------------------------------------------------------------------
    # Quarantine
    # ------------------------------------------------------------------
    def quarantine_state(self, key: str, *, now: int = 0) -> dict:
        with self._lock:
            state = self._quarantine.get(key)
            if state is None:
                return {
                    "key": key,
                    "quarantined": False,
                    "until": 0,
                    "denials": 0,
                }
            if state["until"] > 0 and now >= state["until"]:
                del self._quarantine[key]
                return {
                    "key": key,
                    "quarantined": False,
                    "until": 0,
                    "denials": 0,
                }
            return {
                "key": key,
                "quarantined": state["until"] > 0,
                "until": state["until"],
                "denials": state["denials"],
            }

    def release(self, key: str, *, now: int = 0) -> bool:
        with self._lock:
            state = self._quarantine.get(key)
            if state is None or state["until"] <= 0:
                return False
            if now >= state["until"]:
                del self._quarantine[key]
                return False
            del self._quarantine[key]
            self._counters["releases"] += 1
            return True

    # ------------------------------------------------------------------
    # Ledger and statistics
    # ------------------------------------------------------------------
    def findings(self) -> list:
        with self._lock:
            result = []
            for key in sorted(self._ledger):
                for finding in self._ledger[key]:
                    item = copy.deepcopy(finding)
                    item["key"] = key
                    result.append(item)
            result.sort(key=lambda item: (
                item["key"],
                item["code"],
                item["target"],
            ))
            return result

    def stats(self) -> dict:
        with self._lock:
            result = copy.deepcopy(self._counters)
            result["profiles"] = len(self._profiles)
            result["ledger_findings"] = sum(
                len(items) for items in self._ledger.values()
            )
            return result

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "config": {
                    field: getattr(self, field) for field in _CONFIG_FIELDS
                },
                "profiles": copy.deepcopy(self._profiles),
                "quarantine": copy.deepcopy(self._quarantine),
                "findings": copy.deepcopy(self._ledger),
                "counters": copy.deepcopy(self._counters),
            }

    def _validate_snapshot(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "not a dict")
        try:
            state = copy.deepcopy(state)
        except Exception as exc:
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", str(exc)) from exc

        if state.get("version") != 1:
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "version")

        config = state.get("config")
        if not isinstance(config, dict):
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "config")
        self._validate_config(config, code="ESC_SNAPSHOT_INVALID")

        profiles = state.get("profiles")
        if not isinstance(profiles, dict):
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "profiles")
        for name, entry in profiles.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", "profiles"
                )
            spec = entry.get("spec")
            fingerprint = entry.get("fingerprint")
            if (not isinstance(spec, dict) or not isinstance(fingerprint, str)
                    or not _FINGERPRINT_RE.match(fingerprint)):
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", "profiles"
                )
            self._canonical_json(spec)

        quarantine = state.get("quarantine")
        if not isinstance(quarantine, dict):
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "quarantine")
        for key, entry in quarantine.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", "quarantine"
                )
            until = entry.get("until")
            denials = entry.get("denials")
            if (not isinstance(until, int) or isinstance(until, bool)
                    or until < 0
                    or not isinstance(denials, int)
                    or isinstance(denials, bool) or denials < 0):
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", "quarantine"
                )

        ledger = state.get("findings", state.get("ledger"))
        if not isinstance(ledger, dict):
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "findings")
        for key, items in ledger.items():
            if not isinstance(key, str) or not isinstance(items, list):
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", "findings"
                )
            for finding in items:
                if not isinstance(finding, dict):
                    raise ContainerEscapeError(
                        "ESC_SNAPSHOT_INVALID", "findings"
                    )
                if (not isinstance(finding.get("code"), str)
                        or finding.get("severity") not in _SEVERITY_RANK
                        or not isinstance(finding.get("target"), str)
                        or "detail" not in finding):
                    raise ContainerEscapeError(
                        "ESC_SNAPSHOT_INVALID", "findings"
                    )

        counters = state.get("counters")
        if not isinstance(counters, dict):
            raise ContainerEscapeError("ESC_SNAPSHOT_INVALID", "counters")
        for name in _COUNTER_KEYS:
            value = counters.get(name)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", "counters"
                )

        return {
            "config": config,
            "profiles": profiles,
            "quarantine": quarantine,
            "ledger": ledger,
            "counters": {name: counters[name] for name in _COUNTER_KEYS},
        }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                validated = self._validate_snapshot(state)
            except ContainerEscapeError as exc:
                if exc.code == "ESC_SNAPSHOT_INVALID":
                    raise
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", exc.detail
                ) from exc
            except Exception as exc:
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", str(exc)
                ) from exc

            old = (
                {field: getattr(self, field) for field in _CONFIG_FIELDS},
                self._profiles,
                self._quarantine,
                self._ledger,
                self._counters,
            )
            try:
                for field, value in validated["config"].items():
                    setattr(self, field, value)
                self._profiles = validated["profiles"]
                self._quarantine = validated["quarantine"]
                self._ledger = validated["ledger"]
                self._counters = validated["counters"]
            except Exception as exc:
                config, profiles, quarantine, ledger, counters = old
                for field, value in config.items():
                    setattr(self, field, value)
                self._profiles = profiles
                self._quarantine = quarantine
                self._ledger = ledger
                self._counters = counters
                raise ContainerEscapeError(
                    "ESC_SNAPSHOT_INVALID", str(exc)
                ) from exc
