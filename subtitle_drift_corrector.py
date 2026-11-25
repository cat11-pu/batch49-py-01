"""Subtitle drift correction: anchor fitting, piecewise-linear remap, cue shifting."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class SubtitleDriftError(ValueError):
    """Refused subtitle drift operation carrying a stable error code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


DEFAULT_MAX_ANCHORS = 64
DEFAULT_MAX_SEGMENTS = 8
DEFAULT_MAX_SHIFT_MS = 4000
DEFAULT_ANCHOR_TTL_MS = 600000


@dataclass(frozen=True)
class Anchor:
    media_ms: int
    drift_ms: int
    added_at_ms: int


class SubtitleDriftCorrector:
    """Estimate subtitle drift from human anchors and remap whole cue lists."""

    def __init__(
        self,
        max_anchors: int = DEFAULT_MAX_ANCHORS,
        max_segments: int = DEFAULT_MAX_SEGMENTS,
        max_shift_ms: int = DEFAULT_MAX_SHIFT_MS,
        anchor_ttl_ms: int = DEFAULT_ANCHOR_TTL_MS,
    ) -> None:
        self.max_anchors = max_anchors
        self.max_segments = max_segments
        self.max_shift_ms = max_shift_ms
        self.anchor_ttl_ms = anchor_ttl_ms
        self._lock = threading.RLock()

    def add_anchor(self, media_ms: int, drift_ms: int, added_at_ms: int | None = None) -> bool:
        raise NotImplementedError

    def anchor_count(self) -> int:
        raise NotImplementedError

    def anchors_for(self, now_ms: int | None = None) -> list[tuple[int, int]]:
        raise NotImplementedError

    def fit_segments(self, now_ms: int | None = None) -> list[tuple[int, int, float]]:
        raise NotImplementedError

    def map_time(self, media_ms: int, now_ms: int | None = None) -> int:
        raise NotImplementedError

    def apply(
        self, cues: list[tuple[int, int, str]], now_ms: int | None = None
    ) -> list[tuple[int, int, str]]:
        raise NotImplementedError

    def max_shift(self) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
