"""Live caption reviser: stable-window prefix commits over streaming hypotheses."""

from __future__ import annotations

import threading

# Condition -> error code carried by CaptionError.code.
ERROR_CODES = {
    "argument": "CAPTION_ARGUMENT_INVALID",     # empty/non-text stream id or text
    "stream_missing": "CAPTION_STREAM_MISSING",  # unknown stream on read/commit
    "capacity": "CAPTION_CAPACITY_EXCEEDED",    # more than max_streams
    "pending_overflow": "CAPTION_PENDING_OVERFLOW",  # tail above max_pending_chars
    "revision_limit": "CAPTION_REVISION_LIMIT",  # revisions above max_revisions
    "committed_immutable": "CAPTION_COMMITTED_IMMUTABLE",  # revise crossing the commit point
    "snapshot_invalid": "CAPTION_SNAPSHOT_INVALID",
}

STATS_KEYS = ("hypotheses", "revisions", "commits", "duplicates", "rejected")


class CaptionError(ValueError):
    """Raised when a caption operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LiveCaptionReviser:
    """Commit the stable prefix of streaming hypotheses, keep revising the tail."""

    def __init__(self, *, window_ms: int = 400, max_pending_chars: int = 240,
                 max_revisions: int = 64, max_streams: int = 8) -> None:
        self.window_ms = window_ms
        self.max_pending_chars = max_pending_chars
        self.max_revisions = max_revisions
        self.max_streams = max_streams
        self._lock = threading.RLock()

    def push_hypothesis(self, stream_id: str, text: str, *, now_ms: int) -> dict:
        """Return {"status", "stream_id", "pending", "revisions"}; status is
        "accepted" or "duplicate"."""
        raise NotImplementedError

    def stabilize(self, stream_id: str, *, now_ms: int) -> dict:
        """Return {"status", ...}: "waiting" carries "remaining_ms", "committed"
        and "idle" carry "committed"."""
        raise NotImplementedError

    def commit(self, stream_id: str) -> dict:
        """Return {"status", "committed", "committed_tokens"}; status is
        "committed"."""
        raise NotImplementedError

    def revise(self, stream_id: str, text: str, *, now_ms: int | None = None) -> dict:
        """Return {"status", "pending"}; status is "revised"."""
        raise NotImplementedError

    def window(self, stream_id: str) -> int:
        """Return the configured window in milliseconds."""
        raise NotImplementedError

    def latency(self, stream_id: str, *, now_ms: int) -> int:
        """Return milliseconds since the last accepted hypothesis."""
        raise NotImplementedError

    def text(self, stream_id: str) -> dict:
        """Return {"committed", "pending", "display"}."""
        raise NotImplementedError

    def streams(self) -> list[str]:
        """Return stream ids in ascending order."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached state dict accepted by restore()."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore a snapshot produced by snapshot()."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return counters keyed by STATS_KEYS."""
        raise NotImplementedError
