"""Trusted device ladder: promotion, idle demotion, family merge and revocation."""

from __future__ import annotations

from dataclasses import dataclass


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

    def __init__(self, *, max_devices_per_user: int = 5,
                 max_family_members: int = 3,
                 idle_seconds: int = 2592000) -> None:
        self.max_devices_per_user = max_devices_per_user
        self.max_family_members = max_family_members
        self.idle_seconds = idle_seconds

    def register_device(self, *, user: str, device_id: str, stable_key: str,
                        now: int, session_device: str | None = None
                        ) -> DeviceRecord:
        raise NotImplementedError

    def record_login(self, *, user: str, device_id: str, success: bool,
                     method: str = "password", now: int) -> DeviceRecord:
        raise NotImplementedError

    def promote(self, *, user: str, device_id: str, now: int) -> DeviceRecord:
        raise NotImplementedError

    def demote(self, *, user: str, device_id: str, now: int) -> DeviceRecord:
        raise NotImplementedError

    def merge_family(self, *, user: str, stable_key: str,
                     now: int) -> FamilyMerge:
        raise NotImplementedError

    def revoke(self, *, user: str, device_id: str,
               now: int) -> tuple[str, ...]:
        raise NotImplementedError

    def list_devices(self, user: str) -> list[DeviceRecord]:
        raise NotImplementedError

    def tick(self, now: int) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
