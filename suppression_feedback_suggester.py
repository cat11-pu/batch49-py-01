"""Aggregate false-positive feedback into suppression suggestions."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class FeedbackError(ValueError):
    """Raised when a feedback operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Hit:
    hit_id: str
    batch_id: str
    rule_id: str
    path: str
    line: int
    observed_at: str
    status: str
    suggestion_id: str | None


@dataclass(frozen=True)
class Sample:
    sample_id: str
    hit_id: str
    rule_id: str
    path: str
    pattern: str
    marked_by: str
    observed_at: str


@dataclass(frozen=True)
class Suggestion:
    sid: str
    rule_id: str
    pattern: str
    sample_count: int
    hit_count: int
    precision: float
    status: str
    created_seq: int
    applied_by: str
    applied_seq: int
    revoked_by: str
    revoked_reason: str


@dataclass(frozen=True)
class SampleOutcome:
    sample_id: str
    pattern: str
    sample_count: int
    precision: float
    suggestion_id: str | None
    created: bool


@dataclass(frozen=True)
class Preview:
    pattern: str
    hit_ids: tuple
    batches: tuple
    count: int


@dataclass(frozen=True)
class RevokeReport:
    restored: int
    batches: tuple


class SuppressionFeedbackSuggester:
    """Turn marked false positives into reviewed and revocable suppressions."""

    def __init__(self, *, min_samples: int = 3,
                 min_precision: float = 0.8,
                 max_samples: int = 10000) -> None:
        self.min_samples = min_samples
        self.min_precision = min_precision
        self.max_samples = max_samples
        self._lock = threading.RLock()
        self._hits: dict = {}
        self._samples: dict = {}
        self._suggestions: dict = {}
        self._sample_seq = 0
        self._suggestion_seq = 0
        self._apply_seq = 0
        self._tokens: dict = {}

    def ingest_hits(self, hits) -> int:
        raise NotImplementedError

    def visible_hits(self) -> tuple:
        raise NotImplementedError

    def suppressed_hits(self) -> tuple:
        raise NotImplementedError

    def pattern_for(self, rule_id: str, path: str) -> str:
        raise NotImplementedError

    def add_sample(self, sample: dict) -> SampleOutcome:
        raise NotImplementedError

    def samples_for(self, pattern: str) -> tuple:
        raise NotImplementedError

    def suggestion(self, sid: str) -> Suggestion:
        raise NotImplementedError

    def suggestions(self) -> tuple:
        raise NotImplementedError

    def preview(self, sid: str) -> Preview:
        raise NotImplementedError

    def prepare_apply(self, sid: str) -> str:
        raise NotImplementedError

    def apply(self, sid: str, *, token: str, operator: str) -> Suggestion:
        raise NotImplementedError

    def revoke(self, sid: str, *, operator: str, reason: str) -> RevokeReport:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
