"""Multi-source CVE affected range merging and single-version verdicts."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class CveRangeError(ValueError):
    """Raised when range input or matcher state is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RangeVerdict:
    cve: str
    version: str
    status: str
    reason: str
    sources: tuple[str, ...]


class CveRangeMatcher:
    """Merge per-source affected intervals and judge one version at a time."""

    def __init__(self, *, cache_size: int = 128) -> None:
        self.cache_size = cache_size
        self._lock = threading.RLock()

    def add_source_ranges(self, cve, source, ranges, *, revision=1) -> int:
        raise NotImplementedError

    def set_exclusions(self, cve, versions) -> None:
        raise NotImplementedError

    def add_backport_exception(self, cve, *, distro, version, source=None) -> None:
        raise NotImplementedError

    def merge_ranges(self, cve) -> list:
        raise NotImplementedError

    def evaluate(self, cve, version, *, distro=None) -> RangeVerdict:
        raise NotImplementedError

    def evaluate_batch(self, cve, versions, *, distro=None) -> list:
        raise NotImplementedError

    def compare_versions(self, left, right) -> int:
        raise NotImplementedError

    def normalize_version(self, raw) -> str:
        raise NotImplementedError

    def sources_for(self, cve) -> list:
        raise NotImplementedError

    def cache_stats(self) -> dict:
        raise NotImplementedError

    def invalidate(self, cve=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
