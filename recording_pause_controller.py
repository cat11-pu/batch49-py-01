"""Recording pause/resume state machine with segment manifests and recovery."""

from __future__ import annotations

import copy
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


_STATES = ("idle", "recording", "paused", "stopped")
_SEGMENT_STATES = ("open", "complete", "needs_repair")


class _Segment:
    """Mutable internal segment; exported as immutable SegmentInfo."""

    __slots__ = ("index", "start_frame", "end_frame", "frames",
                 "transition_frames", "state")

    def __init__(self, index, start_frame, frames=0, transition_frames=0,
                 state="open"):
        self.index = index
        self.start_frame = start_frame
        self.frames = frames
        self.transition_frames = transition_frames
        self.state = state
        self.end_frame = start_frame + frames

    def info(self):
        return SegmentInfo(
            index=self.index,
            start_frame=self.start_frame,
            end_frame=self.end_frame,
            frames=self.frames,
            transition_frames=self.transition_frames,
            state=self.state,
        )


def _as_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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
        self._state = "idle"
        self._media_frame = 0
        self._written_frames = 0
        self._dropped_frames = 0
        self._pauses = 0
        self._segments = []
        self._pending_transition = 0

    # ------------------------------------------------------------------
    # internal helpers (must be called with the lock held)
    # ------------------------------------------------------------------

    def _open_segment(self):
        if self._segments and self._segments[-1].state == "open":
            return self._segments[-1]
        return None

    def _close_open_segment(self):
        segment = self._open_segment()
        if segment is None:
            return
        if segment.frames < self.min_segment_frames and len(self._segments) > 1:
            previous = self._segments[-2]
            previous.frames += segment.frames
            previous.end_frame = segment.end_frame
            previous.transition_frames += segment.transition_frames
            self._segments.pop()
        else:
            segment.state = "complete"

    # ------------------------------------------------------------------
    # state machine
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._state != "idle":
                raise RecordingError("STATE_ERROR",
                                     f"cannot start from {self._state}")
            self._state = "recording"

    def pause(self) -> None:
        with self._lock:
            if self._state == "paused":
                return
            if self._state != "recording":
                raise RecordingError("STATE_ERROR",
                                     f"cannot pause from {self._state}")
            if self._pauses >= self.max_pauses:
                raise RecordingError("PAUSE_LIMIT",
                                     f"pauses {self._pauses} >= {self.max_pauses}")
            self._pauses += 1
            self._close_open_segment()
            self._state = "paused"

    def resume(self, *, transition_frames: int | None = None) -> None:
        with self._lock:
            if self._state == "recording":
                return
            if self._state != "paused":
                raise RecordingError("STATE_ERROR",
                                     f"cannot resume from {self._state}")
            transition = (self.transition_frames if transition_frames is None
                          else _as_int(transition_frames))
            if transition is None or transition < 0:
                raise RecordingError("INVALID_TRANSITION",
                                     f"bad transition_frames={transition_frames!r}")
            self._media_frame += transition
            self._pending_transition += transition
            self._state = "recording"

    def stop(self) -> None:
        with self._lock:
            if self._state not in ("recording", "paused"):
                raise RecordingError("STATE_ERROR",
                                     f"cannot stop from {self._state}")
            self._close_open_segment()
            self._state = "stopped"

    def write_frame(self, wall_ms: int) -> bool:
        with self._lock:
            if self._state == "paused":
                self._dropped_frames += 1
                return False
            if self._state != "recording":
                raise RecordingError("STATE_ERROR",
                                     f"cannot write while {self._state}")
            segment = self._open_segment()
            if segment is None:
                if len(self._segments) >= self.max_segments:
                    raise RecordingError(
                        "SEGMENT_LIMIT",
                        f"segments {len(self._segments)} >= {self.max_segments}")
                segment = _Segment(index=len(self._segments),
                                   start_frame=self._media_frame,
                                   transition_frames=self._pending_transition)
                self._pending_transition = 0
                self._segments.append(segment)
            segment.frames += 1
            segment.end_frame += 1
            self._media_frame += 1
            self._written_frames += 1
            if segment.frames >= self.max_segment_frames:
                self._close_open_segment()
            return True

    def segments(self) -> list[SegmentInfo]:
        with self._lock:
            return [segment.info() for segment in self._segments]

    # ------------------------------------------------------------------
    # recovery
    # ------------------------------------------------------------------

    def _parse_manifest(self, manifest):
        corrupt = RecordingError("CORRUPT_MANIFEST", "manifest failed validation")
        if isinstance(manifest, (str, bytes)) or manifest is None:
            raise corrupt
        try:
            entries = list(manifest)
        except TypeError:
            raise corrupt from None
        if len(entries) > self.max_segments:
            raise RecordingError(
                "SEGMENT_LIMIT",
                f"manifest has {len(entries)} segments > {self.max_segments}")
        segments = []
        previous_end = 0
        last_position = len(entries) - 1
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise corrupt
            try:
                index = _as_int(entry["index"])
                start = _as_int(entry["start_frame"])
                end = _as_int(entry["end_frame"])
                frames = _as_int(entry["frames"])
                transition = _as_int(entry["transition_frames"])
                size = _as_int(entry["bytes"])
                closed = bool(entry["closed"])
            except (KeyError, TypeError):
                raise corrupt from None
            if (index is None or start is None or end is None or frames is None
                    or transition is None or size is None):
                raise corrupt
            if index != position or start < 0 or frames < 0 or transition < 0:
                raise corrupt
            if end != start + frames or start < previous_end or size < 0:
                raise corrupt
            if closed:
                if size != frames * self.bytes_per_frame:
                    raise corrupt
                state = "complete"
            else:
                if position != last_position:
                    raise corrupt
                whole_frames = size // self.bytes_per_frame
                frames = min(frames, whole_frames)
                end = start + frames
                state = "needs_repair"
            segment = _Segment(index=index, start_frame=start, frames=frames,
                               transition_frames=transition, state=state)
            segment.end_frame = end
            segments.append(segment)
            previous_end = end
        return segments

    def recover(self, manifest) -> None:
        with self._lock:
            if self._state != "idle":
                raise RecordingError("STATE_ERROR",
                                     f"cannot recover while {self._state}")
            segments = self._parse_manifest(manifest)
            self._segments = segments
            self._media_frame = segments[-1].end_frame if segments else 0
            self._written_frames = sum(s.frames for s in segments)
            self._dropped_frames = 0
            self._pauses = 0
            self._pending_transition = 0
            self._state = "paused"

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    def media_time(self) -> int:
        with self._lock:
            return self._media_frame

    def state(self) -> str:
        with self._lock:
            return self._state

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "media_frame": self._media_frame,
                "written_frames": self._written_frames,
                "dropped_frames": self._dropped_frames,
                "pauses": self._pauses,
                "segments": len(self._segments),
            }

    # ------------------------------------------------------------------
    # snapshot / restore
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy({
                "state": self._state,
                "media_frame": self._media_frame,
                "written_frames": self._written_frames,
                "dropped_frames": self._dropped_frames,
                "pauses": self._pauses,
                "pending_transition_frames": self._pending_transition,
                "segments": [
                    {
                        "index": s.index,
                        "start_frame": s.start_frame,
                        "end_frame": s.end_frame,
                        "frames": s.frames,
                        "transition_frames": s.transition_frames,
                        "state": s.state,
                    }
                    for s in self._segments
                ],
            })

    def _parse_snapshot(self, snapshot):
        invalid = RecordingError("SNAPSHOT_INVALID", "snapshot failed validation")
        if not isinstance(snapshot, dict):
            raise invalid
        required = ("state", "media_frame", "written_frames", "dropped_frames",
                    "pauses", "pending_transition_frames", "segments")
        if any(key not in snapshot for key in required):
            raise invalid
        state = snapshot["state"]
        if state not in _STATES:
            raise invalid
        counters = {}
        for key in ("media_frame", "written_frames", "dropped_frames",
                    "pauses", "pending_transition_frames"):
            value = _as_int(snapshot[key])
            if value is None or value < 0:
                raise invalid
            counters[key] = value
        raw_segments = snapshot["segments"]
        if not isinstance(raw_segments, list):
            raise invalid
        if len(raw_segments) > self.max_segments:
            raise invalid
        segments = []
        previous_end = 0
        open_count = 0
        for position, entry in enumerate(raw_segments):
            if not isinstance(entry, dict):
                raise invalid
            try:
                index = _as_int(entry["index"])
                start = _as_int(entry["start_frame"])
                end = _as_int(entry["end_frame"])
                frames = _as_int(entry["frames"])
                transition = _as_int(entry["transition_frames"])
                seg_state = entry["state"]
            except (KeyError, TypeError):
                raise invalid from None
            if (index is None or start is None or end is None or frames is None
                    or transition is None):
                raise invalid
            if seg_state not in _SEGMENT_STATES:
                raise invalid
            if (index != position or start < 0 or frames < 0 or transition < 0
                    or end < start + frames or start < previous_end):
                raise invalid
            if seg_state == "open":
                open_count += 1
                if position != len(raw_segments) - 1 or state != "recording":
                    raise invalid
            segment = _Segment(index=index, start_frame=start, frames=frames,
                               transition_frames=transition, state=seg_state)
            segment.end_frame = end
            segments.append(segment)
            previous_end = end
        if open_count > 1:
            raise invalid
        if segments and counters["media_frame"] < segments[-1].end_frame:
            raise invalid
        return state, counters, segments

    def restore(self, state: dict) -> None:
        with self._lock:
            new_state, counters, segments = self._parse_snapshot(state)
            self._state = new_state
            self._media_frame = counters["media_frame"]
            self._written_frames = counters["written_frames"]
            self._dropped_frames = counters["dropped_frames"]
            self._pauses = counters["pauses"]
            self._pending_transition = counters["pending_transition_frames"]
            self._segments = segments

    def stats(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "media_frame": self._media_frame,
                "written_frames": self._written_frames,
                "dropped_frames": self._dropped_frames,
                "pauses": self._pauses,
                "segments": len(self._segments),
                "fps": self.fps,
                "max_pauses": self.max_pauses,
                "max_segment_frames": self.max_segment_frames,
                "max_segments": self.max_segments,
                "min_segment_frames": self.min_segment_frames,
                "transition_frames": self.transition_frames,
                "bytes_per_frame": self.bytes_per_frame,
            }
