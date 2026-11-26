"""Save schema migration chain: staged slots, per-step checksums, rollback."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass


class SaveError(ValueError):
    """Raised when a slot or migration operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SlotMeta:
    account: str
    slot_id: str
    version: int
    checksum: str
    status: str
    size: int


@dataclass(frozen=True)
class MigrationSummary:
    account: str
    slot_id: str
    from_version: int
    to_version: int
    steps: int


def canonical_bytes(payload: dict) -> bytes:
    """Encode the payload as sorted-key, whitespace-free UTF-8 JSON."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_checksum(payload: dict) -> str:
    """Return the first 16 lowercase hex characters of SHA-256(canonical_bytes)."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:16]


_SLOT_FIELDS = ("payload", "version", "checksum", "status", "size")
_BACKUP_FIELDS = ("payload", "version", "checksum", "status", "size")
_SUMMARY_FIELDS = ("from_version", "to_version", "steps")
_STAT_COUNTERS = ("migrations", "steps", "rollbacks", "verifications", "repairs")


class SaveMigrationChain:
    """Own per-account save slots plus the single-step migration registry."""

    def __init__(self, *, current_version: int, slots_per_account: int = 3) -> None:
        self.current_version = current_version
        self.slots_per_account = slots_per_account
        self._lock = threading.RLock()
        self._migrations: dict[int, tuple[int, object]] = {}
        self._slots: dict[str, dict[str, dict]] = {}
        self._active: dict[str, str] = {}
        self._slot_locks: dict[tuple[str, str], threading.Lock] = {}
        self._stats = {name: 0 for name in _STAT_COUNTERS}

    def _slot_lock(self, account: str, slot_id: str) -> threading.Lock:
        with self._lock:
            key = (account, slot_id)
            if key not in self._slot_locks:
                self._slot_locks[key] = threading.Lock()
            return self._slot_locks[key]

    def _get_slot(self, account: str, slot_id: str) -> dict:
        try:
            return self._slots[account][slot_id]
        except KeyError:
            raise SaveError("SLOT_NOT_FOUND", {"account": account, "slot_id": slot_id})

    @staticmethod
    def _meta(account: str, slot_id: str, slot: dict) -> SlotMeta:
        return SlotMeta(
            account=account,
            slot_id=slot_id,
            version=slot["version"],
            checksum=slot["checksum"],
            status=slot["status"],
            size=slot["size"],
        )

    def register_migration(self, from_version: int, to_version: int, migrator) -> None:
        with self._lock:
            if to_version != from_version + 1:
                raise SaveError(
                    "MIGRATION_JUMP",
                    {"from_version": from_version, "to_version": to_version},
                )
            if from_version in self._migrations:
                raise SaveError("MIGRATION_DUPLICATE", {"from_version": from_version})
            self._migrations[from_version] = (to_version, migrator)

    def create_slot(self, account: str, slot_id: str, payload: dict, *,
                    version: int = 1, checksum: str | None = None) -> SlotMeta:
        data = copy.deepcopy(payload)
        slot = {
            "payload": data,
            "version": version,
            "checksum": checksum if checksum is not None else payload_checksum(data),
            "status": "ok",
            "size": len(canonical_bytes(data)),
            "backup": None,
            "last_migration": None,
        }
        with self._lock:
            account_slots = self._slots.setdefault(account, {})
            if slot_id in account_slots:
                raise SaveError(
                    "SLOT_EXISTS", {"account": account, "slot_id": slot_id}
                )
            if len(account_slots) >= self.slots_per_account:
                raise SaveError(
                    "SLOT_CAPACITY",
                    {"account": account, "limit": self.slots_per_account},
                )
            account_slots[slot_id] = slot
        return self._meta(account, slot_id, slot)

    def plan_upgrade(self, account: str, slot_id: str) -> list[tuple[int, int]]:
        with self._lock:
            slot = self._get_slot(account, slot_id)
            version = slot["version"]
        return [(v, v + 1) for v in range(version, self.current_version)]

    def migrate_slot(self, account: str, slot_id: str, *,
                     target_version: int | None = None) -> SlotMeta:
        target = self.current_version if target_version is None else target_version
        lock = self._slot_lock(account, slot_id)
        with lock:
            with self._lock:
                slot = self._get_slot(account, slot_id)
                from_version = slot["version"]
                if target > self.current_version or target < from_version:
                    raise SaveError(
                        "MIGRATION_TARGET",
                        {"from_version": from_version, "target_version": target},
                    )
                if target == from_version:
                    return self._meta(account, slot_id, slot)
                plan = [(v, v + 1) for v in range(from_version, target)]
                migrators = []
                for step_from, _step_to in plan:
                    entry = self._migrations.get(step_from)
                    if entry is None:
                        raise SaveError(
                            "MIGRATION_GAP", {"from_version": step_from}
                        )
                    migrators.append(entry[1])
                payload = copy.deepcopy(slot["payload"])
            current = payload
            steps = 0
            try:
                for step_from, migrator in zip(
                    (step_from for step_from, _ in plan), migrators
                ):
                    current = migrator(current, step_from)
                    steps += 1
            except Exception as exc:
                with self._lock:
                    slot["status"] = "needs_repair"
                    self._stats["steps"] += steps
                    self._stats["repairs"] += 1
                raise SaveError("MIGRATION_FAILED", exc) from exc
            with self._lock:
                slot["backup"] = {
                    "payload": payload,
                    "version": from_version,
                    "checksum": slot["checksum"],
                    "status": slot["status"],
                    "size": slot["size"],
                }
                slot["payload"] = current
                slot["version"] = target
                slot["checksum"] = payload_checksum(current)
                slot["size"] = len(canonical_bytes(current))
                slot["status"] = "ok"
                slot["last_migration"] = MigrationSummary(
                    account=account,
                    slot_id=slot_id,
                    from_version=from_version,
                    to_version=target,
                    steps=steps,
                )
                self._stats["migrations"] += 1
                self._stats["steps"] += steps
                return self._meta(account, slot_id, slot)

    def last_migration(self, account: str, slot_id: str) -> MigrationSummary | None:
        with self._lock:
            slot = self._get_slot(account, slot_id)
            return slot["last_migration"]

    def rollback_slot(self, account: str, slot_id: str) -> SlotMeta:
        lock = self._slot_lock(account, slot_id)
        with lock, self._lock:
            slot = self._get_slot(account, slot_id)
            backup = slot["backup"]
            if backup is None:
                raise SaveError(
                    "SLOT_NO_BACKUP", {"account": account, "slot_id": slot_id}
                )
            slot["payload"] = backup["payload"]
            slot["version"] = backup["version"]
            slot["checksum"] = backup["checksum"]
            slot["status"] = backup["status"]
            slot["size"] = backup["size"]
            slot["backup"] = None
            self._stats["rollbacks"] += 1
            return self._meta(account, slot_id, slot)

    def verify_slot(self, account: str, slot_id: str) -> bool:
        with self._lock:
            slot = self._get_slot(account, slot_id)
            ok = payload_checksum(slot["payload"]) == slot["checksum"]
            slot["status"] = "ok" if ok else "corrupt"
            self._stats["verifications"] += 1
            return ok

    def list_slots(self, account: str) -> list[SlotMeta]:
        with self._lock:
            account_slots = self._slots.get(account, {})
            return [
                self._meta(account, slot_id, account_slots[slot_id])
                for slot_id in sorted(account_slots)
            ]

    def slot_payload(self, account: str, slot_id: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._get_slot(account, slot_id)["payload"])

    def set_active_slot(self, account: str, slot_id: str) -> None:
        with self._lock:
            self._get_slot(account, slot_id)
            self._active[account] = slot_id

    def active_slot(self, account: str) -> str | None:
        with self._lock:
            return self._active.get(account)

    def snapshot(self) -> dict:
        with self._lock:
            slots = {}
            for account, account_slots in self._slots.items():
                slots[account] = {}
                for slot_id, slot in account_slots.items():
                    record = {key: copy.deepcopy(slot[key]) for key in _SLOT_FIELDS}
                    record["backup"] = (
                        copy.deepcopy(slot["backup"])
                        if slot["backup"] is not None
                        else None
                    )
                    summary = slot["last_migration"]
                    record["last_migration"] = (
                        {
                            "from_version": summary.from_version,
                            "to_version": summary.to_version,
                            "steps": summary.steps,
                        }
                        if summary is not None
                        else None
                    )
                    slots[account][slot_id] = record
            return {
                "current_version": self.current_version,
                "slots_per_account": self.slots_per_account,
                "slots": slots,
                "active": dict(self._active),
                "stats": dict(self._stats),
            }

    @staticmethod
    def _validate_snapshot(state: object) -> None:
        def fail(detail: object) -> None:
            raise SaveError("SNAPSHOT_INVALID", detail)

        if not isinstance(state, dict):
            fail("snapshot must be a dict")
        for key in ("current_version", "slots_per_account", "slots", "active", "stats"):
            if key not in state:
                fail({"missing": key})
        if not isinstance(state["current_version"], int) or not isinstance(
            state["slots_per_account"], int
        ):
            fail("version fields must be ints")
        if not isinstance(state["slots"], dict) or not isinstance(state["active"], dict):
            fail("slots/active must be dicts")
        stats = state["stats"]
        if not isinstance(stats, dict) or any(
            not isinstance(stats.get(name), int) for name in _STAT_COUNTERS
        ):
            fail("stats must hold int counters")
        for account, account_slots in state["slots"].items():
            if not isinstance(account, str) or not isinstance(account_slots, dict):
                fail("slots must map account str to slot dicts")
            for slot_id, record in account_slots.items():
                if not isinstance(slot_id, str) or not isinstance(record, dict):
                    fail("slot records must be dicts")
                if any(field not in record for field in _SLOT_FIELDS) or any(
                    field not in record for field in ("backup", "last_migration")
                ):
                    fail({"slot": slot_id, "reason": "missing fields"})
                backup = record["backup"]
                if backup is not None and (
                    not isinstance(backup, dict)
                    or any(field not in backup for field in _BACKUP_FIELDS)
                ):
                    fail({"slot": slot_id, "reason": "bad backup"})
                summary = record["last_migration"]
                if summary is not None and (
                    not isinstance(summary, dict)
                    or any(field not in summary for field in _SUMMARY_FIELDS)
                ):
                    fail({"slot": slot_id, "reason": "bad last_migration"})
        for account, slot_id in state["active"].items():
            if not isinstance(account, str) or not isinstance(slot_id, str):
                fail("active must map account str to slot_id str")

    def restore(self, state: dict) -> None:
        self._validate_snapshot(state)
        slots = {}
        for account, account_slots in state["slots"].items():
            slots[account] = {}
            for slot_id, record in account_slots.items():
                restored = {key: copy.deepcopy(record[key]) for key in _SLOT_FIELDS}
                restored["backup"] = (
                    copy.deepcopy(record["backup"])
                    if record["backup"] is not None
                    else None
                )
                summary = record["last_migration"]
                restored["last_migration"] = (
                    MigrationSummary(
                        account=account,
                        slot_id=slot_id,
                        from_version=summary["from_version"],
                        to_version=summary["to_version"],
                        steps=summary["steps"],
                    )
                    if summary is not None
                    else None
                )
                slots[account][slot_id] = restored
        with self._lock:
            self.current_version = state["current_version"]
            self.slots_per_account = state["slots_per_account"]
            self._slots = slots
            self._active = dict(state["active"])
            self._stats = {name: int(state["stats"][name]) for name in _STAT_COUNTERS}

    def stats(self) -> dict:
        with self._lock:
            total = sum(len(account_slots) for account_slots in self._slots.values())
            return {"slots": total, **self._stats}
