"""Authorized scan scope gate: target range validation, denial precedence and quota ledger."""

from __future__ import annotations

import threading


class ScanScopeError(ValueError):
    """Raised when a scan scope operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ScanScopeGate:
    """Validate scan targets against tenant scope rules and meter a tenant quota."""

    def __init__(self, *, tenant_budget: int = 100,
                 max_targets_per_request: int = 50,
                 max_rules_per_tenant: int = 32,
                 window_seconds: int = 3600) -> None:
        self.tenant_budget = tenant_budget
        self.max_targets_per_request = max_targets_per_request
        self.max_rules_per_tenant = max_rules_per_tenant
        self.window_seconds = window_seconds
        self._lock = threading.RLock()

    def grant_scope(self, tenant: str, *, cidrs=(), domains=(), ports=()) -> int:
        raise NotImplementedError

    def deny_scope(self, tenant: str, *, cidrs=(), domains=(), ports=()) -> int:
        raise NotImplementedError

    def revoke_scope(self, tenant: str, *, cidrs=(), domains=(), ports=(),
                     effect: str = "allow") -> int:
        raise NotImplementedError

    def scope_of(self, tenant: str) -> dict:
        raise NotImplementedError

    def authorize(self, tenant: str, targets, *, request_id: str | None = None,
                  now: int = 0) -> dict:
        raise NotImplementedError

    def release(self, request_id: str, *, now: int = 0) -> int:
        raise NotImplementedError

    def remaining(self, tenant: str, *, now: int = 0) -> int:
        raise NotImplementedError

    def denials(self, tenant: str | None = None) -> tuple:
        raise NotImplementedError

    def reset_window(self, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
