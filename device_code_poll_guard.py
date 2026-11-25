"""OAuth device authorization polling guard: user-code phishing, poll pacing
and token binding."""

from __future__ import annotations

import threading


class DeviceCodePollError(ValueError):
    """Raised when a device flow operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DeviceCodePollGuard:
    """Track device codes, user codes and token families for one client pool."""

    def __init__(self, *, interval: int = 5, expires_in: int = 600,
                 slow_down_step: int = 5, max_interval: int = 60,
                 max_pending_per_client: int = 8, max_verify_attempts: int = 5,
                 verify_lock_window: int = 300, token_ttl: int = 300,
                 max_bindings: int = 32) -> None:
        self.interval = interval
        self.expires_in = expires_in
        self.slow_down_step = slow_down_step
        self.max_interval = max_interval
        self.max_pending_per_client = max_pending_per_client
        self.max_verify_attempts = max_verify_attempts
        self.verify_lock_window = verify_lock_window
        self.token_ttl = token_ttl
        self.max_bindings = max_bindings
        self._lock = threading.RLock()

    def normalize_user_code(self, raw: str) -> str:
        raise NotImplementedError

    def issue(self, client_id: str, scopes, *, now: int = 0,
              device_code: str = None, user_code: str = None) -> dict:
        raise NotImplementedError

    def approve(self, user_code: str, *, subject: str, now: int = 0) -> dict:
        raise NotImplementedError

    def deny(self, user_code: str, *, now: int = 0) -> bool:
        raise NotImplementedError

    def poll(self, device_code: str, *, client_id: str, now: int = 0) -> dict:
        raise NotImplementedError

    def refresh(self, refresh_token: str, *, client_id: str, now: int = 0) -> dict:
        raise NotImplementedError

    def revoke(self, token: str, *, now: int = 0) -> bool:
        raise NotImplementedError

    def bindings(self) -> list:
        raise NotImplementedError

    def describe(self, device_code: str) -> dict:
        raise NotImplementedError

    def purge(self, *, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
