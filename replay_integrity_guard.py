"""Replay integrity guard: segmented digest chain, tamper location, legacy mapping."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass


ZERO_DIGEST = "0" * 64
SCHEMA_VERSION = 3
SNAPSHOT_SCHEMA = 1

LEGACY_RENAMES = {
    1: {"hp": "health", "ts": "tick", "src": "source"},
    2: {"hp": "health", "amt": "value"},
}

STAT_KEYS = ("sealed", "patched", "verified_ok", "verified_bad", "exported")


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


class _GuardSnapshot(dict):
    """Snapshot mapping; carries stats as an attribute so the mapping itself
    only ever exposes the {"schema", "segments"} keys."""


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _segment_digest(index: int, version: int, prev_digest: str, events) -> str:
    payload = {
        "index": index,
        "version": version,
        "prev_digest": prev_digest,
        "events": events,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_events(events):
    if isinstance(events, (str, bytes)) or not isinstance(events, (list, tuple)):
        raise ReplayGuardError("REPLAY_INVALID_REQUEST",
                               "events must be a non-empty list of mappings")
    if len(events) == 0:
        raise ReplayGuardError("REPLAY_INVALID_REQUEST", "events must not be empty")
    for event in events:
        if not isinstance(event, dict):
            raise ReplayGuardError("REPLAY_INVALID_REQUEST",
                                   "each event must be a mapping")
    try:
        _canonical_json(events)
    except (TypeError, ValueError) as exc:
        raise ReplayGuardError("REPLAY_INVALID_REQUEST",
                               "events must be JSON serializable") from exc


def _validate_version(version):
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ReplayGuardError("REPLAY_INVALID_REQUEST",
                               "version must be a positive integer")


def _is_hex_digest(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


class ReplayIntegrityGuard:
    """Seal replay segments into a hash chain and verify them on load."""

    def __init__(self, *, max_segments: int = 256) -> None:
        self.max_segments = max_segments
        self._lock = threading.RLock()
        self._segments = []
        self._stats = {key: 0 for key in STAT_KEYS}

    def seal_segment(self, index: int, events, *,
                     version: int = 1) -> SegmentDigest:
        with self._lock:
            _validate_version(version)
            _validate_events(events)
            if (isinstance(index, bool) or not isinstance(index, int)
                    or index != len(self._segments)):
                raise ReplayGuardError(
                    "REPLAY_SEGMENT_ORDER",
                    {"expected": len(self._segments), "got": index})
            if len(self._segments) >= self.max_segments:
                raise ReplayGuardError("REPLAY_SEGMENT_LIMIT",
                                       {"max_segments": self.max_segments})
            prev_digest = (self._segments[-1]["digest"]
                           if self._segments else ZERO_DIGEST)
            stored_events = copy.deepcopy(list(events))
            digest = _segment_digest(index, version, prev_digest, stored_events)
            self._segments.append({
                "index": index,
                "version": version,
                "events": stored_events,
                "digest": digest,
                "prev_digest": prev_digest,
            })
            self._stats["sealed"] += 1
            return SegmentDigest(index=index, version=version,
                                 event_count=len(stored_events),
                                 digest=digest, prev_digest=prev_digest)

    def patch_events(self, index: int, events) -> None:
        with self._lock:
            if (isinstance(index, bool) or not isinstance(index, int)
                    or index < 0 or index >= len(self._segments)):
                raise ReplayGuardError("REPLAY_UNKNOWN_SEGMENT",
                                       {"index": index})
            _validate_events(events)
            self._segments[index]["events"] = copy.deepcopy(list(events))
            self._stats["patched"] += 1

    def _first_tamper_index(self):
        expected_prev = ZERO_DIGEST
        for position, segment in enumerate(self._segments):
            if segment["index"] != position:
                return position
            if segment["prev_digest"] != expected_prev:
                return position
            recomputed = _segment_digest(segment["index"], segment["version"],
                                         segment["prev_digest"],
                                         segment["events"])
            if recomputed != segment["digest"]:
                return position
            expected_prev = recomputed
        return None

    def verify(self) -> bool:
        with self._lock:
            ok = self._first_tamper_index() is None
            self._stats["verified_ok" if ok else "verified_bad"] += 1
            return ok

    def locate_tamper(self) -> int | None:
        with self._lock:
            return self._first_tamper_index()

    def map_event(self, raw: dict, *, from_version: int) -> dict:
        if not isinstance(raw, dict):
            raise ReplayGuardError("REPLAY_INVALID_REQUEST",
                                   "raw event must be a mapping")
        _validate_version(from_version)
        renames = LEGACY_RENAMES.get(from_version, {})
        mapped = {renames.get(key, key): value for key, value in raw.items()}
        mapped["schema_version"] = SCHEMA_VERSION
        return mapped

    def export_prefix(self, max_segments: int | None = None) -> list[dict]:
        with self._lock:
            if max_segments is not None:
                if (isinstance(max_segments, bool)
                        or not isinstance(max_segments, int)
                        or max_segments < 0):
                    raise ReplayGuardError(
                        "REPLAY_INVALID_REQUEST",
                        "max_segments must be a non-negative integer")
            trusted = self._first_tamper_index()
            if trusted is None:
                trusted = len(self._segments)
            last = trusted - 1
            if max_segments is not None:
                last = min(last, max_segments)
            rows = []
            seq = 1
            for position in range(last + 1):
                segment = self._segments[position]
                for event in segment["events"]:
                    row = self.map_event(event, from_version=segment["version"])
                    row["segment"] = segment["index"]
                    row["seq"] = seq
                    rows.append(row)
                    seq += 1
            self._stats["exported"] += len(rows)
            return rows

    def segments(self) -> list[SegmentDigest]:
        with self._lock:
            return [SegmentDigest(index=seg["index"], version=seg["version"],
                                  event_count=len(seg["events"]),
                                  digest=seg["digest"],
                                  prev_digest=seg["prev_digest"])
                    for seg in self._segments]

    def snapshot(self) -> dict:
        with self._lock:
            state = _GuardSnapshot({
                "schema": SNAPSHOT_SCHEMA,
                "segments": [{
                    "index": seg["index"],
                    "version": seg["version"],
                    "events": copy.deepcopy(seg["events"]),
                    "digest": seg["digest"],
                    "prev_digest": seg["prev_digest"],
                } for seg in self._segments],
            })
            state.stats = dict(self._stats)
            return state

    def _validate_state(self, state):
        if not isinstance(state, dict):
            raise ReplayGuardError("REPLAY_STATE_INVALID", "state must be a mapping")
        if state.get("schema") != SNAPSHOT_SCHEMA:
            raise ReplayGuardError("REPLAY_STATE_INVALID", "unsupported schema")
        segments = state.get("segments")
        if not isinstance(segments, list):
            raise ReplayGuardError("REPLAY_STATE_INVALID",
                                   "segments must be a list")
        if len(segments) > self.max_segments:
            raise ReplayGuardError("REPLAY_STATE_INVALID",
                                   "segment count exceeds max_segments")
        expected_prev = ZERO_DIGEST
        for position, segment in enumerate(segments):
            if not isinstance(segment, dict):
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment must be a mapping")
            try:
                index = segment["index"]
                version = segment["version"]
                events = segment["events"]
                digest = segment["digest"]
                prev_digest = segment["prev_digest"]
            except KeyError as exc:
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment is missing fields") from exc
            if (isinstance(index, bool) or not isinstance(index, int)
                    or index != position):
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment indexes must be contiguous from 0")
            if (isinstance(version, bool) or not isinstance(version, int)
                    or version <= 0):
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment version must be a positive integer")
            try:
                _validate_events(events)
            except ReplayGuardError as exc:
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment events are invalid") from exc
            if not _is_hex_digest(digest) or not _is_hex_digest(prev_digest):
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment digests must be hex digests")
            if prev_digest != expected_prev:
                raise ReplayGuardError("REPLAY_STATE_INVALID",
                                       "segment chain linkage is broken")
            expected_prev = digest
        stats = getattr(state, "stats", None)
        if stats is not None:
            if (not isinstance(stats, dict)
                    or any(key not in stats for key in STAT_KEYS)
                    or any(isinstance(stats[key], bool)
                           or not isinstance(stats[key], int)
                           or stats[key] < 0 for key in STAT_KEYS)):
                stats = None
        return segments, stats

    def restore(self, state: dict) -> None:
        with self._lock:
            segments, stats = self._validate_state(state)
            self._segments = [{
                "index": seg["index"],
                "version": seg["version"],
                "events": copy.deepcopy(seg["events"]),
                "digest": seg["digest"],
                "prev_digest": seg["prev_digest"],
            } for seg in segments]
            if stats is not None:
                self._stats = {key: stats[key] for key in STAT_KEYS}
            else:
                self._stats = {key: 0 for key in STAT_KEYS}

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)
