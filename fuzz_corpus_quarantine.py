"""Crash corpus quarantine: reproduction streaks, promotion ledger, bounded capacity."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class QuarantineError(ValueError):
    """Raised when a quarantine operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class QuarantineEntry:
    corpus_id: str
    fingerprint: str
    severity: int
    payload_sha: str
    state: str
    reproductions: int
    streak: int
    last_reproduced_at: str | None
    submitted_at: str


@dataclass(frozen=True)
class Promotion:
    corpus_id: str
    fingerprint: str
    issue_id: str
    promoted_at: str


class FuzzCorpusQuarantine:
    """Hold crash corpora until a reproduction streak justifies promotion."""

    def __init__(self, *, capacity: int = 8, required_reproductions: int = 3) -> None:
        self.capacity = capacity
        self.required_reproductions = required_reproductions
        self._lock = threading.RLock()

    def submit(self, corpus_id: str, fingerprint: str, *, severity: int,
               payload_sha: str, at: str) -> QuarantineEntry:
        raise NotImplementedError

    def record_reproduction(self, corpus_id: str, *, reproduced: bool,
                            at: str) -> QuarantineEntry:
        raise NotImplementedError

    def mark_unstable(self, corpus_id: str, *, at: str) -> QuarantineEntry:
        raise NotImplementedError

    def review(self, corpus_id: str, *, at: str) -> str:
        raise NotImplementedError

    def promote(self, corpus_id: str, *, at: str) -> Promotion:
        raise NotImplementedError

    def entry(self, corpus_id: str) -> QuarantineEntry | None:
        raise NotImplementedError

    def entries(self, *, state: str | None = None) -> list[QuarantineEntry]:
        raise NotImplementedError

    def fingerprint_groups(self) -> dict[str, list[str]]:
        raise NotImplementedError

    def prune(self, *, at: str) -> list[QuarantineEntry]:
        raise NotImplementedError

    def evicted(self) -> list[QuarantineEntry]:
        raise NotImplementedError

    def promotions(self) -> list[Promotion]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
