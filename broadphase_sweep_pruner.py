"""Sweep-and-prune broadphase with incremental re-sort, pair dedup and a per-frame budget."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class BroadphaseError(ValueError):
    """Raised when a broadphase operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Bounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class BroadphaseSweepPruner:
    """Generate candidate AABB pairs along one axis within a per-frame budget."""

    def __init__(self, *, budget: int = 64) -> None:
        self.budget = budget
        self._lock = threading.RLock()

    def upsert(self, entity_id: str, min_x: float, min_y: float,
               max_x: float, max_y: float) -> None:
        raise NotImplementedError

    def remove(self, entity_id: str) -> bool:
        raise NotImplementedError

    def query_pairs(self) -> list:
        raise NotImplementedError

    def set_budget(self, budget: int) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def entity_count(self) -> int:
        raise NotImplementedError

    def bounds(self, entity_id: str) -> Bounds:
        raise NotImplementedError

    def contains(self, entity_id: str) -> bool:
        raise NotImplementedError

    def reset_cache(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
