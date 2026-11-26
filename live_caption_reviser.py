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


class _StreamState:
    """Mutable per-stream caption state."""

    __slots__ = ("committed", "pending", "last_text", "last_time", "revisions")

    def __init__(self) -> None:
        self.committed = ""
        self.pending = ""
        self.last_text = None
        self.last_time = None
        self.revisions = 0


class LiveCaptionReviser:
    """Commit the stable prefix of streaming hypotheses, keep revising the tail."""

    def __init__(self, *, window_ms: int = 400, max_pending_chars: int = 240,
                 max_revisions: int = 64, max_streams: int = 8) -> None:
        self.window_ms = window_ms
        self.max_pending_chars = max_pending_chars
        self.max_revisions = max_revisions
        self.max_streams = max_streams
        self._streams: dict[str, _StreamState] = {}
        self._stats = {key: 0 for key in STATS_KEYS}
        self._lock = threading.RLock()

    def _error(self, key: str, detail: object = None) -> CaptionError:
        self._stats["rejected"] += 1
        return CaptionError(ERROR_CODES[key], detail)

    @staticmethod
    def _tail_after(committed: str, text: str) -> str:
        if not committed:
            return text
        if text.startswith(committed):
            return text[len(committed):].lstrip()
        common = 0
        for have, got in zip(committed, text):
            if have != got:
                break
            common += 1
        return text[common:].lstrip()

    def _commit_state(self, state: _StreamState) -> None:
        if not state.pending:
            return
        if state.committed:
            state.committed = state.committed + " " + state.pending
        else:
            state.committed = state.pending
        state.pending = ""
        self._stats["commits"] += 1

    def push_hypothesis(self, stream_id: str, text: str, *, now_ms: int) -> dict:
        """Return {"status", "stream_id", "pending", "revisions"}; status is
        "accepted" or "duplicate"."""
        with self._lock:
            if not isinstance(stream_id, str) or not stream_id \
                    or not isinstance(text, str):
                raise self._error("argument",
                                  {"stream_id": stream_id, "text": text})
            state = self._streams.get(stream_id)
            if state is None:
                if len(self._streams) >= self.max_streams:
                    raise self._error("capacity", stream_id)
                state = _StreamState()
            if state.last_time is not None and text == state.last_text \
                    and now_ms == state.last_time:
                self._stats["duplicates"] += 1
                return {"status": "duplicate", "stream_id": stream_id,
                        "pending": state.pending, "revisions": state.revisions}
            tail = self._tail_after(state.committed, text)
            if len(tail) > self.max_pending_chars:
                raise self._error("pending_overflow",
                                  {"stream_id": stream_id, "chars": len(tail)})
            if state.revisions >= self.max_revisions:
                raise self._error("revision_limit", stream_id)
            state.pending = tail
            state.last_text = text
            state.last_time = now_ms
            state.revisions += 1
            self._streams[stream_id] = state
            self._stats["hypotheses"] += 1
            self._stats["revisions"] += 1
            return {"status": "accepted", "stream_id": stream_id,
                    "pending": state.pending, "revisions": state.revisions}

    def stabilize(self, stream_id: str, *, now_ms: int) -> dict:
        """Return {"status", ...}: "waiting" carries "remaining_ms", "committed"
        and "idle" carry "committed"."""
        with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise self._error("stream_missing", stream_id)
            if not state.pending:
                return {"status": "idle", "stream_id": stream_id,
                        "committed": state.committed}
            elapsed = now_ms - (state.last_time or 0)
            if elapsed < self.window_ms:
                return {"status": "waiting", "stream_id": stream_id,
                        "remaining_ms": self.window_ms - elapsed,
                        "committed": state.committed, "pending": state.pending}
            self._commit_state(state)
            return {"status": "committed", "stream_id": stream_id,
                    "committed": state.committed}

    def commit(self, stream_id: str) -> dict:
        """Return {"status", "committed", "committed_tokens"}; status is
        "committed"."""
        with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise self._error("stream_missing", stream_id)
            self._commit_state(state)
            tokens = len(state.committed.split()) if state.committed else 0
            return {"status": "committed", "stream_id": stream_id,
                    "committed": state.committed, "committed_tokens": tokens}

    def revise(self, stream_id: str, text: str, *, now_ms: int | None = None) -> dict:
        """Return {"status", "pending"}; status is "revised"."""
        with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise self._error("stream_missing", stream_id)
            if not isinstance(text, str):
                raise self._error("argument", {"stream_id": stream_id,
                                               "text": text})
            if state.committed and not text.startswith(state.committed):
                raise self._error("committed_immutable", stream_id)
            tail = self._tail_after(state.committed, text)
            if len(tail) > self.max_pending_chars:
                raise self._error("pending_overflow",
                                  {"stream_id": stream_id, "chars": len(tail)})
            if state.revisions >= self.max_revisions:
                raise self._error("revision_limit", stream_id)
            state.pending = tail
            state.revisions += 1
            self._stats["revisions"] += 1
            return {"status": "revised", "stream_id": stream_id,
                    "pending": state.pending}

    def window(self, stream_id: str) -> int:
        """Return the configured window in milliseconds."""
        with self._lock:
            if stream_id not in self._streams:
                raise self._error("stream_missing", stream_id)
            return self.window_ms

    def latency(self, stream_id: str, *, now_ms: int) -> int:
        """Return milliseconds since the last accepted hypothesis."""
        with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise self._error("stream_missing", stream_id)
            if state.last_time is None:
                return 0
            return now_ms - state.last_time

    def text(self, stream_id: str) -> dict:
        """Return {"committed", "pending", "display"}."""
        with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                raise self._error("stream_missing", stream_id)
            committed = state.committed
            pending = state.pending
            if committed and pending:
                display = committed + " " + pending
            else:
                display = committed or pending
            return {"committed": committed, "pending": pending,
                    "display": display}

    def streams(self) -> list[str]:
        """Return stream ids in ascending order."""
        with self._lock:
            return sorted(self._streams)

    def snapshot(self) -> dict:
        """Return a detached state dict accepted by restore()."""
        with self._lock:
            return {
                "version": 1,
                "config": {
                    "window_ms": self.window_ms,
                    "max_pending_chars": self.max_pending_chars,
                    "max_revisions": self.max_revisions,
                    "max_streams": self.max_streams,
                },
                "streams": {
                    stream_id: {
                        "committed": state.committed,
                        "pending": state.pending,
                        "last_text": state.last_text,
                        "last_time": state.last_time,
                        "revisions": state.revisions,
                    }
                    for stream_id, state in self._streams.items()
                },
                "stats": dict(self._stats),
            }

    def _validated_snapshot(self, state: object) -> tuple[dict, dict, dict]:
        def invalid() -> CaptionError:
            return self._error("snapshot_invalid", state)

        if not isinstance(state, dict):
            raise invalid()
        config = state.get("config")
        streams = state.get("streams")
        stats = state.get("stats")
        if not isinstance(config, dict) or not isinstance(streams, dict) \
                or not isinstance(stats, dict):
            raise invalid()
        for key in ("window_ms", "max_pending_chars", "max_revisions",
                    "max_streams"):
            if not isinstance(config.get(key), int):
                raise invalid()
        for key in STATS_KEYS:
            if not isinstance(stats.get(key), int):
                raise invalid()
        new_streams: dict[str, _StreamState] = {}
        for stream_id, entry in streams.items():
            if not isinstance(stream_id, str) or not isinstance(entry, dict):
                raise invalid()
            committed = entry.get("committed")
            pending = entry.get("pending")
            last_text = entry.get("last_text")
            last_time = entry.get("last_time")
            revisions = entry.get("revisions")
            if not isinstance(committed, str) or not isinstance(pending, str):
                raise invalid()
            if last_text is not None and not isinstance(last_text, str):
                raise invalid()
            if last_time is not None \
                    and not isinstance(last_time, (int, float)):
                raise invalid()
            if not isinstance(revisions, int):
                raise invalid()
            restored = _StreamState()
            restored.committed = committed
            restored.pending = pending
            restored.last_text = last_text
            restored.last_time = last_time
            restored.revisions = revisions
            new_streams[stream_id] = restored
        new_config = {key: config[key] for key in
                      ("window_ms", "max_pending_chars", "max_revisions",
                       "max_streams")}
        new_stats = {key: stats[key] for key in STATS_KEYS}
        return new_config, new_streams, new_stats

    def restore(self, state: dict) -> None:
        """Restore a snapshot produced by snapshot()."""
        with self._lock:
            config, streams, stats = self._validated_snapshot(state)
            self.window_ms = config["window_ms"]
            self.max_pending_chars = config["max_pending_chars"]
            self.max_revisions = config["max_revisions"]
            self.max_streams = config["max_streams"]
            self._streams = streams
            self._stats = stats

    def stats(self) -> dict:
        """Return counters keyed by STATS_KEYS."""
        with self._lock:
            return dict(self._stats)
