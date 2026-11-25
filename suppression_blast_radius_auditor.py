"""Read-only audit of suppression rule blast radius over a time window."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AuditError(ValueError):
    """Raised when an audit operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: str
    created_at: str
    expires_at: str | None
    enabled: bool


@dataclass(frozen=True)
class Hit:
    hit_id: str
    rule_id: str
    path: str
    line: int
    batch_id: str
    observed_at: str


@dataclass(frozen=True)
class CoverageEntry:
    rule_id: str
    distinct_paths: int
    distinct_hits: int
    ratio: float


@dataclass(frozen=True)
class AuditReport:
    snapshot_at: str
    config_version: int
    rules_total: int
    enabled_total: int
    distinct_paths: int
    overbroad: tuple
    expired: tuple
    never_hit: tuple
    cleanup: tuple
    coverage: tuple


class SuppressionBlastRadiusAuditor:
    """Aggregate suppressed hits and flag suppressions that are too wide."""

    def __init__(self, *, overbroad_ratio: float = 0.6, min_hits: int = 3,
                 window_days: int = 30) -> None:
        self.overbroad_ratio = overbroad_ratio
        self.min_hits = min_hits
        self.window_days = window_days
        self._lock = threading.RLock()
        self._rules: dict = {}
        self._hits: dict = {}
        self._config_version = 0
        self._history: list = []

    def load_rules(self, rules) -> int:
        raise NotImplementedError

    def rules(self) -> tuple:
        raise NotImplementedError

    def record_hits(self, hits) -> int:
        raise NotImplementedError

    def coverage(self, *, now: str) -> tuple:
        raise NotImplementedError

    def audit(self, *, now: str) -> AuditReport:
        raise NotImplementedError

    def rule_detail(self, rule_id: str, *, now: str) -> dict:
        raise NotImplementedError

    def cleanup(self, *, now: str) -> tuple:
        raise NotImplementedError

    def audit_history(self) -> tuple:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
