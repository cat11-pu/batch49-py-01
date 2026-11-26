"""HTTP/2 connection and stream ledger for Rapid Reset style floods."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field


class H2GuardError(ValueError):
    """Raised when a connection or stream operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class _Connection:
    conn_id: str
    opened_at: int = 0
    state: str = "open"
    last_stream_id: int = 0
    concurrent: int = 0
    streams: dict[int, str] = field(default_factory=dict)
    reset_events: list[int] = field(default_factory=list)
    open_events: list[int] = field(default_factory=list)
    quarantine_until: int | None = None
    quarantine_reason: str | None = None
    quarantined: bool = False


class H2ResetFloodGuard:
    """Track stream lifecycles and quarantine connections that reset too fast."""

    _STAT_KEYS = (
        "connections_opened",
        "streams_opened",
        "resets",
        "quarantines",
        "refused",
        "pruned",
    )

    def __init__(self, *, max_concurrent_streams: int = 100,
                 window_seconds: int = 60, reset_limit: int = 20,
                 ratio_limit: float = 0.5, min_samples: int = 10,
                 quarantine_seconds: int = 30, max_connections: int = 64,
                 max_reset_history: int = 2048) -> None:
        self.max_concurrent_streams = max_concurrent_streams
        self.window_seconds = window_seconds
        self.reset_limit = reset_limit
        self.ratio_limit = ratio_limit
        self.min_samples = min_samples
        self.quarantine_seconds = quarantine_seconds
        self.max_connections = max_connections
        self.max_reset_history = max_reset_history

        self._lock = threading.RLock()
        self._connections: dict[str, _Connection] = {}
        self._stats = {key: 0 for key in self._STAT_KEYS}

    # ------------------------------------------------------------------
    # Internal helpers. Callers must hold self._lock.
    # ------------------------------------------------------------------
    def _refuse(self, code: str, detail: object = None) -> None:
        self._stats["refused"] += 1
        raise H2GuardError(code, detail)

    def _connection(self, conn_id: object, *, live: bool = True) -> _Connection:
        if not isinstance(conn_id, str):
            self._refuse("H2_CONN_INVALID", conn_id)
        key = conn_id.strip()
        if not key:
            self._refuse("H2_CONN_INVALID", conn_id)
        conn = self._connections.get(key)
        if conn is None:
            self._refuse("H2_CONN_UNKNOWN", key)
        if live and conn.state != "open":
            self._refuse("H2_CONN_UNKNOWN", key)
        return conn

    def _is_quarantined(self, conn: _Connection, now: int) -> bool:
        return (conn.quarantined and conn.quarantine_until is not None
                and now < conn.quarantine_until)

    def _count_since(self, timestamps: list[int], now: int) -> int:
        return sum(1 for timestamp in timestamps
                   if now - timestamp <= self.window_seconds)

    def _expire_events(self, timestamps: list[int], now: int) -> list[int]:
        return [timestamp for timestamp in timestamps
                if now - timestamp <= self.window_seconds]

    def _activate_quarantine(self, conn: _Connection, now: int,
                             reason: str) -> None:
        conn.quarantined = True
        conn.quarantine_until = now + self.quarantine_seconds
        conn.quarantine_reason = reason
        self._stats["quarantines"] += 1

    def _view(self, conn: _Connection, now: int) -> dict:
        reset_count = self._count_since(conn.reset_events, now)
        opened_count = self._count_since(conn.open_events, now)
        ratio = reset_count / opened_count if opened_count else 0.0
        return {
            "conn_id": conn.conn_id,
            "state": conn.state,
            "concurrent": conn.concurrent,
            "last_stream_id": conn.last_stream_id,
            "opened_count": opened_count,
            "reset_count": reset_count,
            "ratio": ratio,
            "quarantined": self._is_quarantined(conn, now),
            "quarantine_until": conn.quarantine_until,
            "quarantine_reason": conn.quarantine_reason,
        }

    def _verdict(self, conn: _Connection, now: int) -> dict:
        if self._is_quarantined(conn, now):
            out = self._view(conn, now)
            out.update(level="quarantined", action="QUARANTINED")
            return out

        out = self._view(conn, now)
        reset_count = out["reset_count"]
        opened_count = out["opened_count"]
        ratio = out["ratio"]

        if (reset_count >= self.reset_limit
                and reset_count >= self.min_samples):
            self._activate_quarantine(conn, now, "reset-flood")
            out = self._view(conn, now)
            out.update(level="storm", action="GOAWAY")
            return out

        if opened_count >= self.min_samples and ratio >= self.ratio_limit:
            out.update(level="suspicious", action="MONITOR")
        else:
            out.update(level="normal", action="NONE")
        return out

    # ------------------------------------------------------------------
    # Connections and streams.
    # ------------------------------------------------------------------
    def open_connection(self, conn_id: str, *, now: int = 0) -> dict:
        with self._lock:
            if not isinstance(conn_id, str):
                self._refuse("H2_CONN_INVALID", conn_id)
            key = conn_id.strip()
            if not key:
                self._refuse("H2_CONN_INVALID", conn_id)
            if key in self._connections:
                self._refuse("H2_CONN_EXISTS", key)
            active = sum(1 for conn in self._connections.values()
                         if conn.state == "open")
            if active >= self.max_connections:
                self._refuse("H2_CONNECTION_LIMIT", key)

            conn = _Connection(conn_id=key, opened_at=now)
            self._connections[key] = conn
            self._stats["connections_opened"] += 1
            return {
                "conn_id": key,
                "state": conn.state,
                "opened_at": now,
            }

    def close_connection(self, conn_id: str, *, now: int = 0) -> bool:
        with self._lock:
            conn = self._connection(conn_id, live=False)
            if conn.state != "open":
                return False
            conn.state = "closed"
            for stream_id, stream_state in conn.streams.items():
                if stream_state == "open":
                    conn.streams[stream_id] = "closed"
            conn.concurrent = 0
            conn.quarantined = False
            conn.quarantine_until = None
            conn.quarantine_reason = None
            return True

    def open_stream(self, conn_id: str, stream_id: int, *, now: int = 0) -> dict:
        with self._lock:
            conn = self._connection(conn_id)
            if self._is_quarantined(conn, now):
                self._refuse("H2_QUARANTINED", conn.conn_id)
            if (not isinstance(stream_id, int) or isinstance(stream_id, bool)
                    or stream_id <= 0 or stream_id % 2 == 0
                    or stream_id <= conn.last_stream_id):
                self._refuse("H2_STREAM_ID_INVALID", stream_id)
            if conn.concurrent >= self.max_concurrent_streams:
                self._refuse("H2_CONCURRENCY_EXCEEDED", conn.conn_id)

            conn.last_stream_id = stream_id
            conn.streams[stream_id] = "open"
            conn.concurrent += 1
            conn.open_events.append(now)
            self._stats["streams_opened"] += 1
            return {
                "conn_id": conn.conn_id,
                "stream_id": stream_id,
                "state": "open",
                "concurrent": conn.concurrent,
                "last_stream_id": conn.last_stream_id,
            }

    def close_stream(self, conn_id: str, stream_id: int, *, now: int = 0) -> bool:
        with self._lock:
            conn = self._connection(conn_id)
            if not isinstance(stream_id, int) or isinstance(stream_id, bool):
                return False
            if conn.streams.get(stream_id) != "open":
                return False
            conn.streams[stream_id] = "closed"
            conn.concurrent -= 1
            return True

    def record_reset(self, conn_id: str, stream_id: int, *, now: int = 0) -> dict:
        with self._lock:
            conn = self._connection(conn_id)
            if (not isinstance(stream_id, int) or isinstance(stream_id, bool)
                    or stream_id not in conn.streams):
                if not self._is_quarantined(conn, now):
                    self._activate_quarantine(conn, now, "unknown-stream")
                self._refuse("H2_STREAM_UNKNOWN", stream_id)

            if conn.streams[stream_id] == "open":
                conn.streams[stream_id] = "closed"
                conn.concurrent -= 1
            conn.reset_events.append(now)
            if self.max_reset_history == 0:
                conn.reset_events.clear()
            elif (self.max_reset_history > 0
                    and len(conn.reset_events) > self.max_reset_history):
                del conn.reset_events[:len(conn.reset_events)
                                      - self.max_reset_history]
            self._stats["resets"] += 1
            return {
                "conn_id": conn.conn_id,
                "stream_id": stream_id,
                "stream_state": conn.streams[stream_id],
                "reset_count": self._count_since(conn.reset_events, now),
                "concurrent": conn.concurrent,
            }

    # ------------------------------------------------------------------
    # Abuse evaluation and quarantine.
    # ------------------------------------------------------------------
    def evaluate(self, conn_id: str, *, now: int = 0) -> dict:
        with self._lock:
            return self._verdict(self._connection(conn_id), now)

    def quarantine(self, conn_id: str, *, now: int = 0,
                   reason: str = "manual") -> dict:
        with self._lock:
            conn = self._connection(conn_id)
            was_active = self._is_quarantined(conn, now)
            conn.quarantined = True
            conn.quarantine_until = now + self.quarantine_seconds
            conn.quarantine_reason = reason
            if not was_active:
                self._stats["quarantines"] += 1
            return {
                "conn_id": conn.conn_id,
                "action": "GOAWAY",
                "until": conn.quarantine_until,
                "reason": reason,
                "quarantined": True,
            }

    def release(self, conn_id: str, *, now: int = 0) -> bool:
        with self._lock:
            conn = self._connection(conn_id, live=False)
            if not self._is_quarantined(conn, now):
                return False
            conn.quarantined = False
            conn.quarantine_until = None
            conn.quarantine_reason = None
            return True

    def state(self, conn_id: str, *, now: int = 0) -> dict:
        with self._lock:
            return self._view(self._connection(conn_id, live=False), now)

    def prune(self, *, now: int = 0) -> int:
        with self._lock:
            removed = 0
            for conn in self._connections.values():
                kept = self._expire_events(conn.reset_events, now)
                removed += len(conn.reset_events) - len(kept)
                conn.reset_events = kept
                conn.open_events = self._expire_events(conn.open_events, now)
            self._stats["pruned"] += removed
            return removed

    # ------------------------------------------------------------------
    # Snapshots and statistics.
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            connections = {}
            for key, conn in self._connections.items():
                connections[key] = {
                    "conn_id": conn.conn_id,
                    "opened_at": conn.opened_at,
                    "state": conn.state,
                    "last_stream_id": conn.last_stream_id,
                    "concurrent": conn.concurrent,
                    "streams": dict(conn.streams),
                    "reset_events": list(conn.reset_events),
                    "open_events": list(conn.open_events),
                    "quarantine_until": conn.quarantine_until,
                    "quarantine_reason": conn.quarantine_reason,
                    "quarantined": conn.quarantined,
                }
            return {
                "connections": connections,
                "stats": copy.deepcopy(self._stats),
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                connections, stats = self._rebuild_snapshot(state)
            except (H2GuardError, TypeError, ValueError, KeyError):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state) from None

            self._connections = connections
            self._stats = stats

    def _rebuild_snapshot(self, state: object) -> tuple[
            dict[str, _Connection], dict[str, int]]:
        if not isinstance(state, dict):
            raise H2GuardError("H2_SNAPSHOT_INVALID", state)
        if "connections" not in state or "stats" not in state:
            raise H2GuardError("H2_SNAPSHOT_INVALID", state)
        raw_connections = state["connections"]
        raw_stats = state["stats"]
        if not isinstance(raw_connections, dict) or not isinstance(raw_stats, dict):
            raise H2GuardError("H2_SNAPSHOT_INVALID", state)

        stats = {}
        for key in self._STAT_KEYS:
            value = raw_stats[key]
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            stats[key] = value

        connections = {}
        for key, raw in raw_connections.items():
            if not isinstance(key, str) or not key.strip() or key != key.strip():
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            if not isinstance(raw, dict):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            required = {
                "conn_id", "opened_at", "state", "last_stream_id",
                "concurrent", "streams", "reset_events", "open_events",
                "quarantine_until", "quarantine_reason", "quarantined",
            }
            if not required.issubset(raw):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            conn_id = raw["conn_id"]
            if not isinstance(conn_id, str) or conn_id != key:
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            if raw["state"] not in ("open", "closed"):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            for name in ("opened_at", "last_stream_id", "concurrent"):
                value = raw[name]
                if (not isinstance(value, int) or isinstance(value, bool)
                        or value < 0):
                    raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            if not isinstance(raw["streams"], dict):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            streams = {}
            for stream_id, stream_state in raw["streams"].items():
                if (not isinstance(stream_id, int)
                        or isinstance(stream_id, bool)
                        or stream_id <= 0 or stream_id % 2 == 0
                        or stream_state not in ("open", "closed")):
                    raise H2GuardError("H2_SNAPSHOT_INVALID", state)
                streams[stream_id] = stream_state
            highest = max(streams, default=0)
            if raw["last_stream_id"] < highest:
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            if raw["concurrent"] != sum(
                    1 for stream_state in streams.values()
                    if stream_state == "open"):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)

            events = {}
            for name in ("reset_events", "open_events"):
                value = raw[name]
                if not isinstance(value, list):
                    raise H2GuardError("H2_SNAPSHOT_INVALID", state)
                timestamps = []
                for timestamp in value:
                    if (not isinstance(timestamp, int)
                            or isinstance(timestamp, bool)):
                        raise H2GuardError("H2_SNAPSHOT_INVALID", state)
                    timestamps.append(timestamp)
                events[name] = timestamps

            until = raw["quarantine_until"]
            if until is not None and (not isinstance(until, int)
                                      or isinstance(until, bool)):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            reason = raw["quarantine_reason"]
            if reason is not None and not isinstance(reason, str):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            quarantined = raw["quarantined"]
            if not isinstance(quarantined, bool):
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)
            if quarantined and until is None:
                raise H2GuardError("H2_SNAPSHOT_INVALID", state)

            connections[key] = _Connection(
                conn_id=conn_id,
                opened_at=raw["opened_at"],
                state=raw["state"],
                last_stream_id=raw["last_stream_id"],
                concurrent=raw["concurrent"],
                streams=streams,
                reset_events=events["reset_events"],
                open_events=events["open_events"],
                quarantine_until=until,
                quarantine_reason=reason,
                quarantined=quarantined,
            )

        active = sum(1 for conn in connections.values()
                     if conn.state == "open")
        if active > self.max_connections:
            raise H2GuardError("H2_SNAPSHOT_INVALID", state)
        return connections, stats

    def stats(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)
