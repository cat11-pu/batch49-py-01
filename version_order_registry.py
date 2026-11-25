"""Ecosystem aware version comparator registry with bounded parse cache."""

from __future__ import annotations

from dataclasses import dataclass


class VersionOrderError(ValueError):
    """Raised when a registry operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ParsedVersion:
    ecosystem: str
    raw: str
    valid: bool
    key: tuple


class VersionOrderRegistry:
    """Resolve versions through per-ecosystem parsers and keep a bounded cache."""

    def __init__(self, *, cache_capacity: int = 64) -> None:
        self.cache_capacity = cache_capacity

    def register_ecosystem(self, name, parser) -> None:
        raise NotImplementedError

    def declare_bridge(self, source_ecosystem, target_ecosystem, *, translator) -> None:
        raise NotImplementedError

    def parse(self, ecosystem, raw) -> ParsedVersion:
        raise NotImplementedError

    def compare(self, ecosystem, left, right) -> int:
        raise NotImplementedError

    def compare_cross(self, left_ecosystem, left, right_ecosystem, right) -> int:
        raise NotImplementedError

    def sort_versions(self, ecosystem, raws) -> list:
        raise NotImplementedError

    def dedupe(self, ecosystem, raws) -> list:
        raise NotImplementedError

    def ecosystems(self) -> list:
        raise NotImplementedError

    def cache_stats(self) -> dict:
        raise NotImplementedError

    def clear_cache(self, ecosystem=None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
