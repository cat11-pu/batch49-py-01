"""Segment-level key rotation planning with grace windows and license records."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class KeyRotationError(ValueError):
    """Refused key rotation operation carrying a stable error code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


DEFAULT_PERIOD_MS = 6000
DEFAULT_GRACE_SEGMENTS = 1
DEFAULT_MAX_KEYS = 8
DEFAULT_MAX_REVOCATIONS = 64


@dataclass(frozen=True)
class SegmentInfo:
    index: int
    start_ms: int
    duration_ms: int
    epoch: int


class SegmentKeyRotationPlanner:
    """Assign keys to segment epochs, rotate at boundaries, track grace windows."""

    def __init__(
        self,
        period_ms: int = DEFAULT_PERIOD_MS,
        grace_segments: int = DEFAULT_GRACE_SEGMENTS,
        max_keys: int = DEFAULT_MAX_KEYS,
        max_revocations: int = DEFAULT_MAX_REVOCATIONS,
    ) -> None:
        self.period_ms = period_ms
        self.grace_segments = grace_segments
        self.max_keys = max_keys
        self.max_revocations = max_revocations
        self._lock = threading.RLock()

    def add_segment(self, segment_index: int, duration_ms: int) -> int:
        raise NotImplementedError

    def epoch_for_segment(self, segment_index: int) -> int:
        raise NotImplementedError

    def register_key(self, key_id: str, epoch: int) -> None:
        raise NotImplementedError

    def key_count(self) -> int:
        raise NotImplementedError

    def rotate(self, segment_index: int, key_id: str) -> str | None:
        raise NotImplementedError

    def grace_window(self, at_segment: int | None = None) -> tuple[str, str] | None:
        raise NotImplementedError

    def active_key(self) -> str | None:
        raise NotImplementedError

    def revoke(self, key_id: str) -> None:
        raise NotImplementedError

    def is_revoked(self, key_id: str) -> bool:
        raise NotImplementedError

    def license_for(self, segment_index: int, ttl_segments: int = 3) -> str:
        raise NotImplementedError

    def license_usable(self, license_id: str, at_segment: int) -> bool:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
