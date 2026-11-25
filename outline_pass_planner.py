"""Screen-space outline planner: edge tests, width budget, tile sampling."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class OutlineError(ValueError):
    """Rejection raised by the planner; ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TilePlan:
    """Sampling plan for one screen tile."""

    tile: tuple
    samples: int
    trimmed: bool


class OutlinePassPlanner:
    """Plan the outline pass: thresholds, per-pixel width and tile budget."""

    def __init__(self, *, grid=(8, 8), max_width: int = 6,
                 max_samples_per_frame: int = 1024) -> None:
        self.grid = grid
        self.max_width = max_width
        self.max_samples_per_frame = max_samples_per_frame
        self._lock = threading.RLock()

    def set_thresholds(self, *, depth: float, normal: float) -> dict:
        raise NotImplementedError

    def thresholds(self) -> dict:
        raise NotImplementedError

    def detect_edge(self, depth_delta, normal_dot) -> int:
        raise NotImplementedError

    def width_for_depth(self, depth, base_width) -> int:
        raise NotImplementedError

    def pixel_width(self, depth, base_width, depth_hit, normal_hit) -> int:
        raise NotImplementedError

    def plan_tiles(self, width: int, tiles) -> tuple:
        raise NotImplementedError

    def set_participation(self, *, transparent: bool = False,
                          masked: bool = False) -> None:
        raise NotImplementedError

    def composite_order(self, objects) -> tuple:
        raise NotImplementedError

    def budget(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
