"""Cross-scanner alert alignment, deduplication and human adjudication memory."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AlignError(ValueError):
    """Raised when an alignment operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Alert:
    key: str
    source: str
    vuln_id: str
    weakness: str
    path: str
    line: int
    severity: str


@dataclass(frozen=True)
class MatchScore:
    confidence: int
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AlertGroup:
    group_id: str
    keys: tuple[str, ...]
    sources: tuple[str, ...]
    confidence: int
    adjudicated: bool


@dataclass(frozen=True)
class PendingPair:
    left: str
    right: str
    confidence: int
    reason: str


@dataclass(frozen=True)
class Decision:
    left: str
    right: str
    verdict: str
    operator: str
    note: str
    seq: int


@dataclass(frozen=True)
class AlignResult:
    scan_id: str
    groups: tuple[AlertGroup, ...]
    pending: tuple[PendingPair, ...]


class CrossToolDedupAligner:
    """Pairwise alert matcher with confidence scoring and adjudication history."""

    def __init__(self, *, merge_threshold: int = 80, proximity: int = 5,
                 max_alerts: int = 10000) -> None:
        self.merge_threshold = merge_threshold
        self.proximity = proximity
        self.max_alerts = max_alerts
        self._lock = threading.RLock()
        self._alerts: dict = {}
        self._results: dict = {}
        self._decisions: dict = {}
        self._seq = 0

    def alert_key(self, source: str, vuln_id: str, path: str, line: int) -> str:
        raise NotImplementedError

    def ingest(self, scan_id: str, alerts) -> tuple[str, ...]:
        raise NotImplementedError

    def alerts_for(self, scan_id: str) -> tuple[Alert, ...]:
        raise NotImplementedError

    def compare(self, left_key: str, right_key: str) -> MatchScore:
        raise NotImplementedError

    def align(self, scan_id: str) -> AlignResult:
        raise NotImplementedError

    def pending(self, scan_id: str) -> tuple[PendingPair, ...]:
        raise NotImplementedError

    def adjudicate(self, left_key: str, right_key: str, verdict: str, *,
                   operator: str, note: str = "") -> Decision:
        raise NotImplementedError

    def decisions(self) -> tuple[Decision, ...]:
        raise NotImplementedError

    def decision_for(self, left_key: str, right_key: str):
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
