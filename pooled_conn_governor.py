"""Outbound connection pool governor with leases, reaping and wait queue limits."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PoolError(RuntimeError):
    """Raised when a pool operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ConnView:
    conn_id: str
    target: str
    owner: str
    requests: int
    created_at: str
    leased_since: str | None


@dataclass(frozen=True)
class AcquireResult:
    status: str
    conn: ConnView | None
    reused: bool
    waiter_id: str | None
    position: int | None


class PooledConnGovernor:
    """Govern per-target connection pools, idle reuse and borrow leases."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def configure(self, target: str, *, max_size: int = 4, max_idle: int = 2,
                  max_requests: int = 100, max_lifetime_s: int = 300,
                  idle_ttl_s: int = 60, lease_ttl_s: int = 30,
                  max_waiters: int = 4, wait_timeout_s: int = 5) -> dict:
        raise NotImplementedError

    def acquire(self, target: str, now: str, owner: str = "") -> AcquireResult:
        raise NotImplementedError

    def poll_waiter(self, waiter_id: str, now: str) -> dict:
        raise NotImplementedError

    def release(self, conn_id: str, healthy: bool = True,
                now: str | None = None) -> dict:
        raise NotImplementedError

    def discard(self, conn_id: str, reason: str = "manual") -> bool:
        raise NotImplementedError

    def reap(self, now: str) -> dict:
        raise NotImplementedError

    def leak_report(self) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
