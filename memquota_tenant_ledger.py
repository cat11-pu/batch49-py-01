"""Tenant memory quota ledger: per-tenant accounting, overcommit reclaim in
priority order and OOM victim selection."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class MemQuotaError(RuntimeError):
    """Raised when a quota operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Charge:
    charge_id: str
    tenant: str
    pid: int
    bytes: int
    seq: int


@dataclass(frozen=True)
class OomAction:
    tenant: str
    pid: int
    charge_id: str
    bytes: int
    priority: int
    reason: str


@dataclass(frozen=True)
class EnforceReport:
    reclaimed: tuple[str, ...]
    oom: OomAction | None
    usage_before: int
    usage_after: int
    unresolved: bool


class MemQuotaTenantLedger:
    """Account memory charges per tenant under one global limit."""

    def __init__(self, *, global_limit: int, max_tenants: int = 16) -> None:
        self.global_limit = global_limit
        self.max_tenants = max_tenants
        self._lock = threading.RLock()

    def add_tenant(self, name: str, *, quota: int, floor: int = 0,
                   priority: int = 100) -> None:
        raise NotImplementedError

    def remove_tenant(self, name: str) -> int:
        raise NotImplementedError

    def charge(self, tenant: str, *, pid: int, bytes: int) -> Charge:
        raise NotImplementedError

    def release(self, charge_id: str) -> int:
        raise NotImplementedError

    def usage(self) -> int:
        raise NotImplementedError

    def tenant_usage(self, name: str) -> int:
        raise NotImplementedError

    def charges(self, tenant: str | None = None) -> tuple[Charge, ...]:
        raise NotImplementedError

    def enforce(self) -> EnforceReport:
        raise NotImplementedError

    def oom_candidates(self, *, limit: int = 3) -> tuple[OomAction, ...]:
        raise NotImplementedError

    def resize_global_limit(self, limit: int) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore_state(self, state: object) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError
