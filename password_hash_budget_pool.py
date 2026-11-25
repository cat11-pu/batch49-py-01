"""Bounded password-hash verification pool with per-tenant compute budgets."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PasswordHashError(ValueError):
    """Raised when a pool operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class HashPolicy:
    tenant: str
    per_tenant_concurrency: int
    queue_deadline_ms: int
    cost_ms: int
    window_ms: int
    budget_units: int
    verify_units: int
    upgrade_units: int
    lockout_threshold: int
    lock_ms: int
    generation: int


@dataclass(frozen=True)
class VerifyResult:
    status: str
    reason: str
    retry_after_ms: int
    needs_upgrade: bool
    generation: int | None


@dataclass(frozen=True)
class UpgradeOutcome:
    account: str
    status: str
    from_generation: int
    to_generation: int
    code: str


class PasswordHashBudgetPool:
    """Verification pool, budget accounting and deferred rehash queue."""

    def __init__(self, *, pool_capacity: int = 8, queue_limit: int = 8,
                 clock=None) -> None:
        self.pool_capacity = pool_capacity
        self.queue_limit = queue_limit
        self._clock = clock
        self._lock = threading.RLock()

    def register_policy(self, tenant, *, per_tenant_concurrency=4,
                        queue_deadline_ms=100, cost_ms=10, window_ms=1000,
                        budget_units=100, verify_units=1, upgrade_units=4,
                        lockout_threshold=5, lock_ms=300000) -> HashPolicy:
        raise NotImplementedError

    def add_record(self, tenant, account, secret, *, generation=1) -> None:
        raise NotImplementedError

    def advance_generation(self, tenant) -> int:
        raise NotImplementedError

    def verify(self, tenant, account, secret, *, now) -> VerifyResult:
        raise NotImplementedError

    def defer_upgrade(self, tenant, account, *, generation) -> None:
        raise NotImplementedError

    def run_upgrades(self, tenant, *, now, limit=None) -> list:
        raise NotImplementedError

    def budget_of(self, tenant, *, now) -> int:
        raise NotImplementedError

    def pending_upgrades(self, tenant) -> list:
        raise NotImplementedError

    def failure_count(self, tenant, account) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
