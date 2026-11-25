"""Per-account TOTP step ledger with leases, epochs and replay protection."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class TotpError(ValueError):
    """Raised when a ledger operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AccountConfig:
    account: str
    epoch: int
    drift_steps: int
    step_ms: int
    retention_steps: int


@dataclass(frozen=True)
class StepLease:
    lease_id: str
    account: str
    epoch: int
    steps: tuple[int, ...]
    expires_at: int


@dataclass(frozen=True)
class ConsumeResult:
    status: str
    step: int | None
    epoch: int | None


class TotpStepLedger:
    """One-time step ledger sharded per account."""

    def __init__(self, *, shard_capacity: int = 64, lease_ttl_ms: int = 30000,
                 clock=None) -> None:
        self.shard_capacity = shard_capacity
        self.lease_ttl_ms = lease_ttl_ms
        self._clock = clock
        self._lock = threading.RLock()

    def register_account(self, account, *, epoch=1, drift_steps=1,
                         step_ms=30000, retention_steps=8) -> AccountConfig:
        raise NotImplementedError

    def consume(self, account, *, epoch, now, lease_id=None) -> ConsumeResult:
        raise NotImplementedError

    def lease(self, account, *, now, count=1) -> StepLease:
        raise NotImplementedError

    def release(self, lease_id, *, now) -> int:
        raise NotImplementedError

    def rebind(self, account, *, now) -> int:
        raise NotImplementedError

    def purge_expired(self, now) -> tuple:
        raise NotImplementedError

    def consumed_steps(self, account, *, epoch=None) -> tuple:
        raise NotImplementedError

    def lease_of(self, lease_id):
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
