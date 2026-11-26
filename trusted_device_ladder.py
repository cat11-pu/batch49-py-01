"""Trusted device ladder: promotion, idle demotion, family merge and revocation."""

from __future__ import annotations

import copy
import threading
from dataclasses import asdict, dataclass, fields, replace
from typing import ClassVar


class TrustedDeviceError(ValueError):
    """Raised when a trusted device operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DeviceRecord:
    user: str
    device_id: str
    stable_key: str
    family_id: str
    level: int
    consecutive: int
    verified: bool
    last_used: int
    revoked: bool


@dataclass(frozen=True)
class FamilyMerge:
    family_id: str
    stable_key: str
    members: tuple[str, ...]
    level: int


class TrustedDeviceLadder:
    """Track per-user devices, trust levels and merged device families."""

    _SNAPSHOT_VERSION = 1
    _COUNTER_KEYS = (
        "registered",
        "promoted",
        "demoted",
        "merged",
        "revoked",
        "evicted",
    )
    _STAT_KEYS = _COUNTER_KEYS + ("active_devices", "families")
    _PROMOTION_THRESHOLDS: ClassVar[dict[int, int]] = {0: 2, 1: 3, 2: 4}

    def __init__(self, *, max_devices_per_user: int = 5,
                 max_family_members: int = 3,
                 idle_seconds: int = 2592000) -> None:
        self.max_devices_per_user = max_devices_per_user
        self.max_family_members = max_family_members
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._devices: dict[tuple[str, str], DeviceRecord] = {}
        self._counters = {key: 0 for key in self._COUNTER_KEYS}

    @staticmethod
    def _invalid(message: str, detail: object = None) -> TrustedDeviceError:
        return TrustedDeviceError("DEVICE_INVALID_REQUEST",
                                  {"reason": message, "detail": detail})

    @classmethod
    def _require_text(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise cls._invalid(f"{field_name} must be a non-empty string", value)
        return value

    @classmethod
    def _require_now(cls, now: object) -> int:
        if isinstance(now, bool) or not isinstance(now, int):
            raise cls._invalid("now must be an integer number of seconds", now)
        return now

    def _active_user_records_locked(self, user: str) -> list[DeviceRecord]:
        return [
            record
            for record in self._devices.values()
            if record.user == user and not record.revoked
        ]

    def _get_active_locked(self, user: str,
                           device_id: str) -> DeviceRecord:
        record = self._devices.get((user, device_id))
        if record is None or record.revoked:
            raise TrustedDeviceError(
                "DEVICE_NOT_FOUND",
                {"user": user, "device_id": device_id},
            )
        return record

    def register_device(self, *, user: str, device_id: str, stable_key: str,
                        now: int, session_device: str | None = None
                        ) -> DeviceRecord:
        with self._lock:
            user = self._require_text(user, "user")
            device_id = self._require_text(device_id, "device_id")
            stable_key = self._require_text(stable_key, "stable_key")
            now = self._require_now(now)
            if session_device is not None:
                session_device = self._require_text(
                    session_device, "session_device"
                )

            key = (user, device_id)
            if key in self._devices:
                raise TrustedDeviceError(
                    "DEVICE_DUPLICATE",
                    {"user": user, "device_id": device_id},
                )

            same_stable_key = [
                record
                for record in self._devices.values()
                if record.user == user
                and not record.revoked
                and record.stable_key == stable_key
            ]
            if len(same_stable_key) >= self.max_family_members:
                raise TrustedDeviceError(
                    "DEVICE_FAMILY_CAPACITY",
                    {
                        "user": user,
                        "stable_key": stable_key,
                        "limit": self.max_family_members,
                    },
                )

            user_records = self._active_user_records_locked(user)
            if len(user_records) >= self.max_devices_per_user:
                candidates = [
                    record
                    for record in user_records
                    if record.device_id != session_device
                ]
                if not candidates:
                    raise TrustedDeviceError(
                        "DEVICE_CAPACITY_EXCEEDED",
                        {
                            "user": user,
                            "limit": self.max_devices_per_user,
                            "session_device": session_device,
                        },
                    )

                victim = min(
                    candidates,
                    key=lambda record: (
                        record.level,
                        record.last_used,
                        record.device_id,
                    ),
                )
                del self._devices[(user, victim.device_id)]
                self._counters["evicted"] += 1

            record = DeviceRecord(
                user=user,
                device_id=device_id,
                stable_key=stable_key,
                family_id=f"solo:{device_id}",
                level=0,
                consecutive=0,
                verified=False,
                last_used=now,
                revoked=False,
            )
            self._devices[key] = record
            self._counters["registered"] += 1
            return record

    def record_login(self, *, user: str, device_id: str, success: bool,
                     method: str = "password", now: int) -> DeviceRecord:
        with self._lock:
            user = self._require_text(user, "user")
            device_id = self._require_text(device_id, "device_id")
            now = self._require_now(now)
            if method not in ("password", "mfa"):
                raise self._invalid("unsupported login method", method)

            record = self._get_active_locked(user, device_id)
            changes: dict[str, object] = {"last_used": now}
            if success:
                changes["consecutive"] = record.consecutive + 1
                if method == "mfa":
                    changes["verified"] = True
            else:
                changes["consecutive"] = 0

            updated = replace(record, **changes)
            self._devices[(user, device_id)] = updated
            return updated

    def promote(self, *, user: str, device_id: str, now: int) -> DeviceRecord:
        with self._lock:
            user = self._require_text(user, "user")
            device_id = self._require_text(device_id, "device_id")
            now = self._require_now(now)
            record = self._get_active_locked(user, device_id)

            if record.level >= 3:
                raise TrustedDeviceError(
                    "DEVICE_ALREADY_TOP",
                    {"user": user, "device_id": device_id, "level": record.level},
                )

            required = self._PROMOTION_THRESHOLDS[record.level]
            needs_verification = record.level >= 1
            if (record.consecutive < required or
                    (needs_verification and not record.verified)):
                raise TrustedDeviceError(
                    "DEVICE_PROMOTION_NOT_ELIGIBLE",
                    {
                        "user": user,
                        "device_id": device_id,
                        "level": record.level,
                        "consecutive": record.consecutive,
                        "required": required,
                        "verified": record.verified,
                    },
                )

            updated = replace(
                record,
                level=record.level + 1,
                consecutive=0,
            )
            self._devices[(user, device_id)] = updated
            self._counters["promoted"] += 1
            return updated

    def demote(self, *, user: str, device_id: str, now: int) -> DeviceRecord:
        with self._lock:
            user = self._require_text(user, "user")
            device_id = self._require_text(device_id, "device_id")
            now = self._require_now(now)
            record = self._get_active_locked(user, device_id)

            if record.level <= 0:
                raise TrustedDeviceError(
                    "DEVICE_ALREADY_LOWEST",
                    {"user": user, "device_id": device_id},
                )

            updated = replace(
                record,
                level=record.level - 1,
                consecutive=0,
            )
            self._devices[(user, device_id)] = updated
            self._counters["demoted"] += 1
            return updated

    def merge_family(self, *, user: str, stable_key: str,
                     now: int) -> FamilyMerge:
        with self._lock:
            user = self._require_text(user, "user")
            stable_key = self._require_text(stable_key, "stable_key")
            now = self._require_now(now)

            members = sorted(
                (
                    record
                    for record in self._devices.values()
                    if record.user == user
                    and not record.revoked
                    and record.stable_key == stable_key
                ),
                key=lambda record: record.device_id,
            )
            if not members:
                raise TrustedDeviceError(
                    "DEVICE_NOT_FOUND",
                    {"user": user, "stable_key": stable_key},
                )
            if len(members) > self.max_family_members:
                raise TrustedDeviceError(
                    "DEVICE_FAMILY_CAPACITY",
                    {
                        "user": user,
                        "stable_key": stable_key,
                        "limit": self.max_family_members,
                    },
                )

            strongest_level = max(record.level for record in members)
            family_id = f"family:{stable_key}"
            for member in members:
                updated = replace(
                    member,
                    family_id=family_id,
                    level=strongest_level,
                )
                self._devices[(user, member.device_id)] = updated

            self._counters["merged"] += len(members)
            return FamilyMerge(
                family_id=family_id,
                stable_key=stable_key,
                members=tuple(member.device_id for member in members),
                level=strongest_level,
            )

    def revoke(self, *, user: str, device_id: str,
               now: int) -> tuple[str, ...]:
        with self._lock:
            user = self._require_text(user, "user")
            device_id = self._require_text(device_id, "device_id")
            now = self._require_now(now)
            target = self._get_active_locked(user, device_id)

            if target.family_id.startswith("family:"):
                invalidated = sorted(
                    (
                        record
                        for record in self._active_user_records_locked(user)
                        if record.family_id == target.family_id
                    ),
                    key=lambda record: record.device_id,
                )
            else:
                invalidated = [target]

            device_ids = tuple(record.device_id for record in invalidated)
            for record in invalidated:
                self._devices[(user, record.device_id)] = replace(
                    record,
                    revoked=True,
                )

            self._counters["revoked"] += len(device_ids)
            return device_ids

    def list_devices(self, user: str) -> list[DeviceRecord]:
        with self._lock:
            records = [
                record
                for record in self._devices.values()
                if record.user == user and not record.revoked
            ]
            return sorted(records, key=lambda record: record.device_id)

    def tick(self, now: int) -> int:
        with self._lock:
            now = self._require_now(now)
            demoted = 0
            for key, record in list(self._devices.items()):
                if record.revoked or record.level <= 0:
                    continue
                if now - record.last_used >= self.idle_seconds:
                    self._devices[key] = replace(
                        record,
                        level=record.level - 1,
                        consecutive=0,
                    )
                    demoted += 1
                    self._counters["demoted"] += 1
            return demoted

    def stats(self) -> dict:
        with self._lock:
            active = [
                record
                for record in self._devices.values()
                if not record.revoked
            ]
            result = {key: self._counters[key] for key in self._COUNTER_KEYS}
            result["active_devices"] = len(active)
            result["families"] = len({record.family_id for record in active})
            return result

    def snapshot(self) -> dict:
        with self._lock:
            payload = {
                "version": self._SNAPSHOT_VERSION,
                "devices": [asdict(record) for record in self._devices.values()],
                "stats": self.stats(),
            }
            return copy.deepcopy(payload)

    def restore(self, state) -> None:
        with self._lock:
            try:
                snapshot = copy.deepcopy(state)
                devices, counters = self._parse_snapshot(snapshot)
            except TrustedDeviceError:
                raise
            except Exception as exc:
                raise TrustedDeviceError(
                    "DEVICE_SNAPSHOT_INVALID",
                    {"reason": "snapshot is not readable"},
                ) from exc

            self._devices = devices
            self._counters = counters

    def _parse_snapshot(self, snapshot: object):
        if not isinstance(snapshot, dict):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "snapshot must be a dict"})
        if set(snapshot) != {"version", "devices", "stats"}:
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "invalid top-level fields"})

        version = snapshot["version"]
        if isinstance(version, bool) or version != self._SNAPSHOT_VERSION:
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "unsupported version"})

        raw_devices = snapshot["devices"]
        if isinstance(raw_devices, dict):
            raw_records = list(raw_devices.values())
        elif isinstance(raw_devices, (list, tuple)):
            raw_records = list(raw_devices)
        else:
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "devices must be a list"})

        record_fields = {item.name for item in fields(DeviceRecord)}
        devices: dict[tuple[str, str], DeviceRecord] = {}
        for raw_record in raw_records:
            if isinstance(raw_record, DeviceRecord):
                record = raw_record
            elif isinstance(raw_record, dict):
                if set(raw_record) != record_fields:
                    raise TrustedDeviceError(
                        "DEVICE_SNAPSHOT_INVALID",
                        {"reason": "invalid device fields"},
                    )
                try:
                    record = DeviceRecord(**raw_record)
                except TypeError as exc:
                    raise TrustedDeviceError(
                        "DEVICE_SNAPSHOT_INVALID",
                        {"reason": "invalid device record"},
                    ) from exc
            else:
                raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                         {"reason": "invalid device record"})

            self._validate_restored_record(record)
            key = (record.user, record.device_id)
            if key in devices:
                raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                         {"reason": "duplicate device"})
            devices[key] = record

        raw_stats = snapshot["stats"]
        if not isinstance(raw_stats, dict):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "stats must be a dict"})
        if set(raw_stats) != set(self._STAT_KEYS):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "invalid stats fields"})

        counters = {}
        for key in self._STAT_KEYS:
            value = raw_stats.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TrustedDeviceError(
                    "DEVICE_SNAPSHOT_INVALID",
                    {"reason": f"stats.{key} must be a non-negative integer"},
                )
            if key in self._COUNTER_KEYS:
                counters[key] = value

        return devices, counters

    @staticmethod
    def _validate_restored_record(record: DeviceRecord) -> None:
        if not isinstance(record, DeviceRecord):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "invalid device record type"})

        text_fields = (
            record.user,
            record.device_id,
            record.stable_key,
            record.family_id,
        )
        if not all(isinstance(value, str) and value for value in text_fields):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "device identity fields are invalid"})
        if isinstance(record.level, bool) or not isinstance(record.level, int):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "level must be an integer"})
        if not 0 <= record.level <= 3:
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "level is out of range"})
        if (isinstance(record.consecutive, bool)
                or not isinstance(record.consecutive, int)
                or record.consecutive < 0):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "consecutive is invalid"})
        if not isinstance(record.verified, bool) or not isinstance(record.revoked, bool):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "boolean flag is invalid"})
        if isinstance(record.last_used, bool) or not isinstance(record.last_used, int):
            raise TrustedDeviceError("DEVICE_SNAPSHOT_INVALID",
                                     {"reason": "last_used must be an integer"})
