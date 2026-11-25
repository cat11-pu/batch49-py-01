"""Atlas gutter planning: slot layout, gutter rectangles and per-mip levels."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AtlasGutterError(ValueError):
    """Refused build step; ``code`` carries an ATLAS_* identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Slot:
    slot_id: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class GutterRect:
    slot_id: str
    side: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class GutterPlan:
    slot_id: str
    declared: int
    actual: int
    rects: tuple[GutterRect, ...]


@dataclass(frozen=True)
class MipLevel:
    level: int
    scale: int
    gutter: int
    slot_count: int
    bytes_used: int


class AtlasGutterBuilder:
    """Plan gutter strips for every slot of one atlas page and per-mip levels."""

    def __init__(self, page_width: int, page_height: int, *,
                 max_gutter: int = 8, max_mip_levels: int = 4,
                 budget_bytes: int = 1048576) -> None:
        self.page_width = page_width
        self.page_height = page_height
        self.max_gutter = max_gutter
        self.max_mip_levels = max_mip_levels
        self.budget_bytes = budget_bytes
        self._lock = threading.RLock()

    def add_slot(self, slot_id: str, x: int, y: int, width: int,
                 height: int) -> Slot:
        """Register one rectangle on the page, rejecting overlap and overflow."""
        raise NotImplementedError

    def slots(self) -> tuple[str, ...]:
        """Return registered slot ids in ascending order."""
        raise NotImplementedError

    def slot(self, slot_id: str) -> Slot:
        """Return one registered slot."""
        raise NotImplementedError

    def plan_gutters(self, declared: int,
                     slot_ids=None) -> tuple[GutterPlan, ...]:
        """Plan edge and corner gutter rectangles for the requested slots."""
        raise NotImplementedError

    def build_level(self, level: int) -> MipLevel:
        """Build (or reuse) one mip level of the gutter chain."""
        raise NotImplementedError

    def invalidate_region(self, slot_id: str, x: int, y: int, width: int,
                          height: int) -> int:
        """Mark a slot region dirty and return the number of merged dirty rects."""
        raise NotImplementedError

    def dirty_rects(self) -> tuple[tuple[int, int, int, int], ...]:
        """Return the merged dirty rectangles in stable order."""
        raise NotImplementedError

    def budget(self) -> dict:
        """Return gutter byte accounting against the configured budget."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Capture slots, plans, levels and counters as a detached dict."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Replace the live state with a snapshot."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return builder counters."""
        raise NotImplementedError
