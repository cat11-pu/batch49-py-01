"""File integrity baseline ledger: incremental drift comparison, quarantine
tickets and restore/accept handling for drifted paths."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class FimDriftError(ValueError):
    """Raised when a baseline or drift operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class BaselineEntry:
    path: str
    digest: str
    size: int
    mode: int
    owner: str
    inode: int
    version: int


@dataclass(frozen=True)
class DriftEntry:
    path: str
    kind: str
    changed: tuple[str, ...]
    baseline_version: int
    observed_digest: str | None
    seq: int


@dataclass(frozen=True)
class QuarantineTicket:
    ticket_id: str
    path: str
    drift_kind: str
    baseline_version: int
    observed_digest: str | None
    sealed_at: int


class FimDriftLedger:
    """Track baselines, observations and quarantine state for file integrity."""

    def __init__(self, *, max_paths: int = 64,
                 max_open_drift: int = 16) -> None:
        self.max_paths = max_paths
        self.max_open_drift = max_open_drift
        self._lock = threading.RLock()

    def set_baseline(self, path: str, *, digest: str, size: int,
                     mode: int = 0o644, owner: str = "root",
                     inode: int = 1) -> int:
        raise NotImplementedError

    def baseline_for(self, path: str) -> BaselineEntry | None:
        raise NotImplementedError

    def observe(self, path: str, *, digest: str, size: int,
                mode: int = 0o644, owner: str = "root", inode: int = 1,
                now_ns: int = 0) -> int:
        raise NotImplementedError

    def compare(self, *, since: int | None = None) -> tuple[DriftEntry, ...]:
        raise NotImplementedError

    def quarantine(self, path: str, *, now_ns: int = 0) -> QuarantineTicket:
        raise NotImplementedError

    def tickets(self) -> tuple[QuarantineTicket, ...]:
        raise NotImplementedError

    def restore(self, ticket_id: str, *, now_ns: int = 0) -> bool:
        raise NotImplementedError

    def accept(self, ticket_id: str, *, now_ns: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore_state(self, state: object) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError
