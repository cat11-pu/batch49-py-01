"""Sweep-and-prune broadphase with incremental re-sort, pair dedup and a per-frame budget."""

from __future__ import annotations

import bisect
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


_SNAPSHOT_VERSION = 1


class BroadphaseSweepPruner:
    """Generate candidate AABB pairs along one axis within a per-frame budget."""

    def __init__(self, *, budget: int = 64) -> None:
        self._lock = threading.RLock()
        self._validate_budget(budget)
        self.budget = budget
        self._entities: dict[str, Bounds] = {}
        # Axis list of (min_x, entity_id), kept sorted; only moved entities are
        # re-inserted, so unchanged frames never trigger a full re-sort.
        self._axis: list[tuple[float, str]] = []
        # Pairs already reported and still valid (neither endpoint changed).
        self._reported: set[tuple[str, str]] = set()
        # Candidate pairs not yet reported, ordered by (distance, ids).
        self._pending: list[tuple[str, str]] = []
        self._candidates: set[tuple[str, str]] = set()
        self._last_reported = 0
        self._dirty = True

    @staticmethod
    def _validate_budget(budget: object) -> None:
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise BroadphaseError("INVALID_BUDGET",
                                  "budget must be a non-negative integer")

    @staticmethod
    def _validate_entity_id(entity_id: object) -> None:
        if not isinstance(entity_id, str) or entity_id == "":
            raise BroadphaseError("INVALID_ENTITY",
                                  "entity id must be a non-empty string")

    @staticmethod
    def _make_bounds(min_x: float, min_y: float,
                     max_x: float, max_y: float) -> Bounds:
        try:
            lo_x, lo_y = float(min_x), float(min_y)
            hi_x, hi_y = float(max_x), float(max_y)
        except (TypeError, ValueError):
            raise BroadphaseError("INVALID_BOUNDS",
                                  "bounds must be real numbers") from None
        if not (lo_x <= hi_x and lo_y <= hi_y):
            raise BroadphaseError("INVALID_BOUNDS",
                                  "lower bound exceeds upper bound")
        return Bounds(lo_x, lo_y, hi_x, hi_y)

    @staticmethod
    def _normalize_pair(first: str, second: str) -> tuple[str, str]:
        return (first, second) if first < second else (second, first)

    def _pair_sort_key(self, pair: tuple[str, str]):
        first, second = pair
        a = self._entities[first]
        b = self._entities[second]
        # Doubled center deltas keep the ordering without computing halves.
        dx = (a.min_x + a.max_x) - (b.min_x + b.max_x)
        dy = (a.min_y + a.max_y) - (b.min_y + b.max_y)
        return (dx * dx + dy * dy, first, second)

    def _drop_pairs_with(self, entity_id: str) -> None:
        self._reported = {pair for pair in self._reported
                          if entity_id not in pair}
        self._pending = [pair for pair in self._pending
                         if entity_id not in pair]

    def _sweep_candidates(self) -> set[tuple[str, str]]:
        candidates: set[tuple[str, str]] = set()
        axis = self._axis
        entities = self._entities
        for index in range(len(axis)):
            _, first = axis[index]
            a = entities[first]
            for next_index in range(index + 1, len(axis)):
                _, second = axis[next_index]
                b = entities[second]
                if b.min_x > a.max_x:
                    break
                if a.min_y <= b.max_y and b.min_y <= a.max_y:
                    candidates.add(self._normalize_pair(first, second))
        return candidates

    def _refresh_locked(self) -> None:
        if not self._dirty:
            return
        self._candidates = self._sweep_candidates()
        fresh = self._candidates - self._reported
        self._pending = sorted(fresh, key=self._pair_sort_key)
        self._dirty = False

    def upsert(self, entity_id: str, min_x: float, min_y: float,
               max_x: float, max_y: float) -> None:
        self._validate_entity_id(entity_id)
        bounds = self._make_bounds(min_x, min_y, max_x, max_y)
        with self._lock:
            previous = self._entities.get(entity_id)
            if previous is not None:
                if previous == bounds:
                    return
                # Incremental re-sort: only the moved entity changes position.
                slot = bisect.bisect_left(self._axis, (previous.min_x, entity_id))
                if slot < len(self._axis) and self._axis[slot] == (previous.min_x, entity_id):
                    self._axis.pop(slot)
                else:
                    self._axis.remove((previous.min_x, entity_id))
                self._drop_pairs_with(entity_id)
            self._entities[entity_id] = bounds
            bisect.insort(self._axis, (bounds.min_x, entity_id))
            self._dirty = True

    def remove(self, entity_id: str) -> bool:
        with self._lock:
            previous = self._entities.pop(entity_id, None)
            if previous is None:
                return False
            slot = bisect.bisect_left(self._axis, (previous.min_x, entity_id))
            if slot < len(self._axis) and self._axis[slot] == (previous.min_x, entity_id):
                self._axis.pop(slot)
            else:
                self._axis.remove((previous.min_x, entity_id))
            self._drop_pairs_with(entity_id)
            self._dirty = True
            return True

    def query_pairs(self) -> list:
        with self._lock:
            self._refresh_locked()
            take = self._pending[:self.budget]
            del self._pending[:self.budget]
            self._reported.update(take)
            self._last_reported = len(take)
            return list(take)

    def set_budget(self, budget: int) -> None:
        self._validate_budget(budget)
        with self._lock:
            # Shrinking the budget only throttles future frames; the reported
            # cache is kept intact.
            self.budget = budget

    def stats(self) -> dict:
        with self._lock:
            self._refresh_locked()
            return {
                "entities": len(self._entities),
                "candidates": len(self._candidates),
                "reported": self._last_reported,
                "pending": len(self._pending),
                "cache": len(self._reported),
                "budget": self.budget,
            }

    def entity_count(self) -> int:
        with self._lock:
            return len(self._entities)

    def bounds(self, entity_id: str) -> Bounds:
        with self._lock:
            try:
                return self._entities[entity_id]
            except KeyError:
                raise BroadphaseError("UNKNOWN_ENTITY", entity_id) from None

    def contains(self, entity_id: str) -> bool:
        with self._lock:
            return entity_id in self._entities

    def reset_cache(self) -> None:
        with self._lock:
            self._reported.clear()
            self._dirty = True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": _SNAPSHOT_VERSION,
                "budget": self.budget,
                "entities": {
                    entity_id: [b.min_x, b.min_y, b.max_x, b.max_y]
                    for entity_id, b in self._entities.items()
                },
                "reported": sorted([list(pair) for pair in self._reported]),
            }

    def restore(self, state: dict) -> None:
        entities, reported, budget = self._parse_snapshot(state)
        with self._lock:
            self._entities = entities
            self._axis = sorted((b.min_x, entity_id)
                                for entity_id, b in entities.items())
            # Drop dangling references so the cache stays bounded by the
            # restored entity set.
            self._reported = {pair for pair in reported
                              if pair[0] in entities and pair[1] in entities}
            self.budget = budget
            self._pending = []
            self._candidates = set()
            self._last_reported = 0
            self._dirty = True

    @staticmethod
    def _parse_snapshot(state: object):
        def invalid(detail: object) -> BroadphaseError:
            return BroadphaseError("INVALID_SNAPSHOT", detail)

        if not isinstance(state, dict):
            raise invalid("snapshot must be a dict")
        if state.get("version") != _SNAPSHOT_VERSION:
            raise invalid("unsupported snapshot version")
        budget = state.get("budget")
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise invalid("budget must be a non-negative integer")
        raw_entities = state.get("entities")
        if not isinstance(raw_entities, dict):
            raise invalid("entities must be a dict")
        entities: dict[str, Bounds] = {}
        for entity_id, values in raw_entities.items():
            if not isinstance(entity_id, str) or entity_id == "":
                raise invalid("entity id must be a non-empty string")
            if (not isinstance(values, (list, tuple)) or len(values) != 4):
                raise invalid("bounds must hold four numbers")
            try:
                bounds = BroadphaseSweepPruner._make_bounds(*values)
            except BroadphaseError:
                raise invalid("invalid bounds") from None
            entities[entity_id] = bounds
        raw_reported = state.get("reported")
        if not isinstance(raw_reported, (list, tuple)):
            raise invalid("reported must be a list")
        reported: set[tuple[str, str]] = set()
        for entry in raw_reported:
            if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                    or not all(isinstance(item, str) and item for item in entry)
                    or entry[0] == entry[1]):
                raise invalid("reported entries must be pairs of entity ids")
            reported.add(BroadphaseSweepPruner._normalize_pair(*entry))
        return entities, reported, budget
