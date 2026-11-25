"""Recording pause/resume state machine with segment manifests and recovery."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class RecordingError(ValueError):
    """Raised when a recording transition, write, or recovery is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SegmentInfo:
    index: int
    start_frame: int
    end_frame: int
    frames: int
    transition_frames: int
    state: str


class RecordingPauseController:
    """Own one recording session: media clock, segments, and manifest recovery."""

    def __init__(self, *, fps: int = 30, max_pauses: int = 8,
                 max_segment_frames: int = 300, max_segments: int = 16,
                 min_segment_frames: int = 15, transition_frames: int = 0,
                 bytes_per_frame: int = 4) -> None:
        self.fps = fps
        self.max_pauses = max_pauses
        self.max_segment_frames = max_segment_frames
        self.max_segments = max_segments
        self.min_segment_frames = min_segment_frames
        self.transition_frames = transition_frames
        self.bytes_per_frame = bytes_per_frame
        self._lock = threading.RLock()

    def start(self) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def resume(self, *, transition_frames: int | None = None) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def write_frame(self, wall_ms: int) -> bool:
        raise NotImplementedError

    def segments(self) -> list[SegmentInfo]:
        raise NotImplementedError

    def recover(self, manifest) -> None:
        raise NotImplementedError

    def media_time(self) -> int:
        raise NotImplementedError

    def state(self) -> str:
        raise NotImplementedError

    def status(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
