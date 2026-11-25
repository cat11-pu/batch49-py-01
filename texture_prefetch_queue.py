"""Texture prefetch queue: camera prediction, decode slots, cancellation, upload budget."""

from __future__ import annotations

import threading


class TextureError(ValueError):
    """Raised when a texture prefetch operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TexturePrefetchQueue:
    """Predict texture sectors from camera motion and manage their decode queue."""

    def __init__(self, *, max_inflight: int = 4, max_ready: int = 32,
                 upload_bytes_per_frame: int = 4096,
                 lookahead: int = 2) -> None:
        self.max_inflight = max_inflight
        self.max_ready = max_ready
        self.upload_bytes_per_frame = upload_bytes_per_frame
        self.lookahead = lookahead
        self._lock = threading.RLock()

    def set_world(self, sector_count: int) -> None:
        """Define the number of sectors in the ring world."""
        raise NotImplementedError

    def set_pose(self, x: float, y: float, heading_deg: float,
                 speed: float) -> None:
        """Update the camera pose used for prediction."""
        raise NotImplementedError

    def predict_sectors(self) -> list:
        """Return the sectors the camera is about to enter."""
        raise NotImplementedError

    def enqueue(self, sector_id: int) -> bool:
        """Queue one sector for decode, returning False for duplicates."""
        raise NotImplementedError

    def next_decode(self) -> int | None:
        """Start decoding the highest-priority queued sector, if a slot is free."""
        raise NotImplementedError

    def mark_ready(self, sector_id: int, size_bytes: int) -> bool:
        """Move one inflight sector into the ready table."""
        raise NotImplementedError

    def cancel_stale(self, keep) -> list:
        """Cancel queued requests whose sectors are no longer wanted."""
        raise NotImplementedError

    def upload(self, sector_id: int) -> int:
        """Upload one ready sector within the per-frame byte budget."""
        raise NotImplementedError

    def begin_frame(self) -> None:
        """Reset the upload budget for a new frame."""
        raise NotImplementedError

    def ready(self) -> list:
        """Return the ready sectors in insertion order."""
        raise NotImplementedError

    def evictable(self) -> list:
        """Return sectors kept only because their decode already started."""
        raise NotImplementedError

    def budget(self) -> dict:
        """Return frame, queue, inflight, ready and upload counters."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached snapshot of world, pose, queues and counters."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore state produced by snapshot, rejecting malformed input."""
        raise NotImplementedError
