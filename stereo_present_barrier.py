"""Stereo presentation barrier: pair locking, view alignment, timeouts, drops."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class StereoBarrierError(ValueError):
    """Refused stereo barrier operation carrying a stable error code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


DEFAULT_MAX_INFLIGHT_PAIRS = 4
DEFAULT_WAIT_LIMIT_MS = 12
DEFAULT_MAX_DROPPED = 1024

VIEW_IDS = ("left", "right")


@dataclass(frozen=True)
class PairState:
    pair_id: str
    at_ms: int
    left: bytes | None
    right: bytes | None


class StereoPresentBarrier:
    """Align left and right views at a presentation barrier before committing."""

    def __init__(
        self,
        max_inflight_pairs: int = DEFAULT_MAX_INFLIGHT_PAIRS,
        wait_limit_ms: int = DEFAULT_WAIT_LIMIT_MS,
        max_dropped: int = DEFAULT_MAX_DROPPED,
    ) -> None:
        self.max_inflight_pairs = max_inflight_pairs
        self.wait_limit_ms = wait_limit_ms
        self.max_dropped = max_dropped
        self._lock = threading.RLock()

    def begin_pair(self, pair_id: str, at_ms: int) -> None:
        raise NotImplementedError

    def submit_view(self, pair_id: str, view_id: str, data: bytes, at_ms: int) -> bool:
        raise NotImplementedError

    def wait_barrier(self, pair_id: str, now_ms: int) -> str:
        raise NotImplementedError

    def present_pair(self, pair_id: str) -> tuple[bytes, bytes]:
        raise NotImplementedError

    def drop_pair(self, pair_id: str, reason: str = "timeout") -> int:
        raise NotImplementedError

    def timeout(self, now_ms: int) -> list[str]:
        raise NotImplementedError

    def inflight(self) -> list[str]:
        raise NotImplementedError

    def dropped_ids(self) -> list[str]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
