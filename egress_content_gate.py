"""Outbound file egress gate: type sniffing, archive shape and quota reservation."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class EgressError(ValueError):
    """Raised when an egress inspection or reservation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Inspection:
    target: str
    decision: str
    sniffed_type: str
    declared_type: str
    size_bytes: int
    reasons: tuple[str, ...]
    policy_version: int


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    tenant: str
    target: str
    size_bytes: int
    created_ms: int
    expires_ms: int


class EgressContentGate:
    """Check egress file shape per target and reserve tenant egress bytes."""

    def __init__(self, *, window_ms: int = 60000,
                 default_window_bytes: int = 10485760,
                 max_nesting_depth: int = 2,
                 max_uncompressed_bytes: int = 104857600,
                 max_targets: int = 32,
                 max_reservations_per_tenant: int = 64) -> None:
        self.window_ms = window_ms
        self.default_window_bytes = default_window_bytes
        self.max_nesting_depth = max_nesting_depth
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_targets = max_targets
        self.max_reservations_per_tenant = max_reservations_per_tenant
        self._lock = threading.RLock()

    def set_target_policy(self, target: str, *, allowed_types,
                          max_bytes: int, archive_depth: int | None = None,
                          archive_uncompressed_bytes: int | None = None) -> dict:
        raise NotImplementedError

    def target_policy(self, target: str) -> dict:
        raise NotImplementedError

    def set_tenant_quota(self, tenant: str, *, window_bytes: int,
                         window_ms: int | None = None) -> dict:
        raise NotImplementedError

    def inspect(self, target: str, *, filename: str, content_type: str,
                magic: bytes, size_bytes: int, declared_types=(),
                archive_entries=()) -> Inspection:
        raise NotImplementedError

    def reserve(self, tenant: str, target: str, *, size_bytes: int,
                now_ms: int) -> Reservation:
        raise NotImplementedError

    def commit(self, tenant: str, reservation_id: str, *, now_ms: int) -> dict:
        raise NotImplementedError

    def release(self, tenant: str, reservation_id: str, *, now_ms: int) -> bool:
        raise NotImplementedError

    def usage(self, tenant: str) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
