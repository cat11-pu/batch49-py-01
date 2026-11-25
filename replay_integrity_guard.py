"""Replay integrity guard: segmented digest chain, tamper location, legacy mapping."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ReplayGuardError(ValueError):
    """Raised when a replay integrity operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SegmentDigest:
    index: int
    version: int
    event_count: int
    digest: str
    prev_digest: str


class ReplayIntegrityGuard:
    """Seal replay segments into a hash chain and verify them on load."""

    def __init__(self, *, max_segments: int = 256) -> None:
        self.max_segments = max_segments
        self._lock = threading.RLock()

    def seal_segment(self, index: int, events, *,
                     version: int = 1) -> SegmentDigest:
        raise NotImplementedError

    def patch_events(self, index: int, events) -> None:
        raise NotImplementedError

    def verify(self) -> bool:
        raise NotImplementedError

    def locate_tamper(self) -> int | None:
        raise NotImplementedError

    def map_event(self, raw: dict, *, from_version: int) -> dict:
        raise NotImplementedError

    def export_prefix(self, max_segments: int | None = None) -> list[dict]:
        raise NotImplementedError

    def segments(self) -> list[SegmentDigest]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
