"""CIBA backchannel authentication request lifecycle coordinator."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class CibaError(ValueError):
    """Raised when a CIBA operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CibaRequest:
    auth_req_id: str
    client: str
    user: str
    scope: str
    binding_message: str
    hint: str
    status: str
    interval_ms: int
    created_at: int
    expires_at: int
    approved_by: str | None
    denied_reason: str | None


@dataclass(frozen=True)
class PollResult:
    status: str
    interval_ms: int
    token: str | None
    reason: str


class CibaRequestCoordinator:
    """Track requests, device hints and polling cadence per user."""

    def __init__(self, *, per_user_limit: int = 5, per_client_limit: int = 10,
                 max_interval_ms: int = 60000, clock=None) -> None:
        self.per_user_limit = per_user_limit
        self.per_client_limit = per_client_limit
        self.max_interval_ms = max_interval_ms
        self._clock = clock
        self._lock = threading.RLock()

    def register_device(self, user, device_id) -> None:
        raise NotImplementedError

    def create_request(self, client, user, *, scope, binding_message, hint,
                       now, ttl_ms=600000, interval_ms=5000) -> CibaRequest:
        raise NotImplementedError

    def route_hint(self, auth_req_id, *, now) -> tuple:
        raise NotImplementedError

    def approve(self, auth_req_id, device_id, *, now) -> CibaRequest:
        raise NotImplementedError

    def deny(self, auth_req_id, device_id, *, reason, now) -> CibaRequest:
        raise NotImplementedError

    def poll(self, auth_req_id, *, client, now) -> PollResult:
        raise NotImplementedError

    def expire(self, now) -> int:
        raise NotImplementedError

    def request_of(self, auth_req_id):
        raise NotImplementedError

    def open_count(self, user=None, client=None) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
