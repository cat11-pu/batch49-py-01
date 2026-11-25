"""FIDO2 PIN retry store: retry counter, hard lock and resident credentials."""

from __future__ import annotations

import threading
from dataclasses import dataclass

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 63


class Fido2PinError(ValueError):
    """Raised when a PIN or resident credential operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CredentialRecord:
    """One resident credential registered on the security key."""

    credential_id: str
    user_name: str
    rp_id: str
    created_at: int


@dataclass(frozen=True)
class PinAttempt:
    """Outcome of one PIN verification attempt."""

    status: str
    retries_left: int


@dataclass(frozen=True)
class PinState:
    """Current retry budget and hard-lock flag."""

    retries_left: int
    hard_locked: bool


class Fido2PinRetryStore:
    """Keep PIN retry state and resident credentials for one security key."""

    def __init__(self, pin: str = "123456", *, max_retries: int = 8,
                 max_resident: int = 25, device_id: str = "key-0001",
                 firmware_generation: int = 1) -> None:
        self.pin = pin
        self.max_retries = max_retries
        self.max_resident = max_resident
        self.device_id = device_id
        self.firmware_generation = firmware_generation
        self._lock = threading.RLock()
        self._credentials = []
        self._retries_left = max_retries
        self._hard_locked = False
        self._counters = {
            "verifies": 0,
            "failures": 0,
            "hard_locks": 0,
            "resets": 0,
            "registered": 0,
            "deleted": 0,
            "pin_changes": 0,
        }

    def verify_pin(self, pin: str, *, now: int | None = None) -> PinAttempt:
        raise NotImplementedError

    def change_pin(self, old_pin: str, new_pin: str, *,
                   now: int | None = None) -> None:
        raise NotImplementedError

    def pin_status(self) -> PinState:
        raise NotImplementedError

    def device_info(self) -> dict:
        raise NotImplementedError

    def register_credential(self, credential_id: str, user_name: str, *,
                            rp_id: str = "default",
                            now: int | None = None) -> CredentialRecord:
        raise NotImplementedError

    def delete_credential(self, credential_id: str) -> bool:
        raise NotImplementedError

    def list_credentials(self) -> list[CredentialRecord]:
        raise NotImplementedError

    def reset(self, *, now: int | None = None) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
