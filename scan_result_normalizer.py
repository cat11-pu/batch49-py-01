"""Normalize heterogeneous scanner output: severity, CVSS vectors and paths."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class NormalizeError(ValueError):
    """Raised when a normalization request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SeverityMapping:
    level: str
    raw: str
    conservative: bool


@dataclass(frozen=True)
class CvssResult:
    parsed: bool
    base_score: float | None
    components: tuple
    raw: str
    reason: str


@dataclass(frozen=True)
class PathInfo:
    path: str
    line: int | None


@dataclass(frozen=True)
class BatchReport:
    batch_id: str
    accepted: int
    rejected: int
    done: bool
    cursor: int
    remaining: int


class ScanResultNormalizer:
    """Map vendor records into one canonical shape with resumable batches."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._batches: dict = {}

    def map_severity(self, word) -> SeverityMapping:
        raise NotImplementedError

    def parse_cvss(self, vector) -> CvssResult:
        raise NotImplementedError

    def normalize_path(self, path) -> PathInfo:
        raise NotImplementedError

    def normalize_record(self, record) -> dict:
        raise NotImplementedError

    def normalize_batch(self, records, *, batch_id: str,
                        limit: int | None = None) -> BatchReport:
        raise NotImplementedError

    def resume(self, batch_id: str, records) -> BatchReport:
        raise NotImplementedError

    def normalized(self, batch_id: str) -> tuple:
        raise NotImplementedError

    def rejections(self, batch_id: str) -> tuple:
        raise NotImplementedError

    def batch_state(self, batch_id: str) -> dict:
        raise NotImplementedError

    def checkpoint(self, batch_id: str) -> dict:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
