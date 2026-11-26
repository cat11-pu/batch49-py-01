"""多源审计流序号续接：缺口区间、补录窗口、消费游标与快照恢复。"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class AuditSeqError(ValueError):
    """审计流序号操作被拒绝时抛出。"""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Gap:
    """一个尚未闭合的序号缺口区间。"""

    source: str
    start: int
    end: int
    missing: int
    discovered_at: int
    status: str


_VERSION = 1
_STATUS_OPEN = "open"
_STATUS_UNCLOSABLE = "unclosable"
_STAT_KEYS = (
    "accepted",
    "duplicates",
    "buffered",
    "dropped",
    "backfills",
    "gaps_closed",
    "retirements",
)


class AuditSequenceStitcher:
    """按来源维护单调序号、缺口区间、补录与消费游标。"""

    def __init__(self, *, backfill_window: int = 1000,
                 buffer_cap: int = 64) -> None:
        self.backfill_window = backfill_window
        self.buffer_cap = buffer_cap
        self._lock = threading.RLock()
        self._sources: dict[str, dict[str, Any]] = {}
        self._stats = {key: 0 for key in _STAT_KEYS}

    def ingest(self, source, seq, body, *, ts=0):
        self._validate_source(source)
        self._validate_seq(seq)
        ts = self._clock_value(ts)

        with self._lock:
            state = self._sources.get(source)
            if state is not None and state["retired"]:
                raise AuditSeqError(
                    "AUDIT_SEQ_SOURCE_RETIRED",
                    {"source": source, "seq": seq},
                )

            if seq == 0:
                state = self._reset_generation(source, state)
                self._stats["accepted"] += 1
                return {"status": "accepted", "frontier": state["frontier"]}

            if state is None:
                state = self._new_state()
                self._sources[source] = state
                state["started"] = True

            if seq in state["dedup"]:
                if body != state["dedup"][seq]:
                    self._stats["duplicates"] += 1
                    raise AuditSeqError(
                        "AUDIT_SEQ_CONFLICT",
                        {"source": source, "seq": seq},
                    )
                self._stats["duplicates"] += 1
                return {
                    "status": "duplicate",
                    "frontier": state["frontier"],
                }

            closed_gap = self._find_gap(
                state, seq, status=_STATUS_UNCLOSABLE
            )
            if closed_gap is not None:
                raise AuditSeqError(
                    "AUDIT_SEQ_GAP_CLOSED",
                    {
                        "source": source,
                        "seq": seq,
                        "start": closed_gap["start"],
                    },
                )

            stored_body = copy.deepcopy(body)
            row = {"seq": seq, "body": stored_body, "ts": ts}
            state["dedup"][seq] = stored_body
            state["buffer"][seq] = row

            if seq > state["max_seq"]:
                if seq > state["max_seq"] + 1:
                    self._add_gap_range(
                        state,
                        state["max_seq"] + 1,
                        seq - 1,
                        ts,
                    )
                state["max_seq"] = seq

            self._remove_open_gap_point(state, seq)
            self._drain_buffer(state)

            if seq in state["events"]:
                status = "accepted"
            else:
                status = "buffered"
                self._stats["buffered"] += 1

            self._stats["accepted"] += 1
            self._enforce_buffer_cap(state, ts)

            return {"status": status, "frontier": state["frontier"]}

    def backfill(self, source, seq, body, *, ts=0):
        self._validate_source(source)
        self._validate_seq(seq)
        ts = self._clock_value(ts)

        with self._lock:
            state = self._sources.get(source)
            if state is None:
                raise AuditSeqError(
                    "AUDIT_SEQ_NOT_MISSING",
                    {"source": source, "seq": seq},
                )
            if state["retired"]:
                raise AuditSeqError(
                    "AUDIT_SEQ_SOURCE_RETIRED",
                    {"source": source, "seq": seq},
                )

            if seq <= state["cursor"] or seq <= state["frontier"]:
                raise AuditSeqError(
                    "AUDIT_SEQ_NOT_MISSING",
                    {
                        "source": source,
                        "seq": seq,
                        "cursor": state["cursor"],
                        "frontier": state["frontier"],
                    },
                )

            closed_gap = self._find_gap(
                state, seq, status=_STATUS_UNCLOSABLE
            )
            if closed_gap is not None:
                raise AuditSeqError(
                    "AUDIT_SEQ_GAP_CLOSED",
                    {
                        "source": source,
                        "seq": seq,
                        "start": closed_gap["start"],
                    },
                )

            gap = self._find_gap(state, seq, status=_STATUS_OPEN)
            if gap is None:
                raise AuditSeqError(
                    "AUDIT_SEQ_NOT_MISSING",
                    {"source": source, "seq": seq},
                )

            if not self._within_window(gap, ts):
                gap["status"] = _STATUS_UNCLOSABLE
                raise AuditSeqError(
                    "AUDIT_SEQ_GAP_CLOSED",
                    {
                        "source": source,
                        "seq": seq,
                        "start": gap["start"],
                        "reason": "backfill_window",
                    },
                )

            if seq in state["dedup"] and body != state["dedup"][seq]:
                raise AuditSeqError(
                    "AUDIT_SEQ_CONFLICT",
                    {"source": source, "seq": seq},
                )

            stored_body = copy.deepcopy(body)
            row = {"seq": seq, "body": stored_body, "ts": ts}
            state["dedup"].setdefault(seq, stored_body)
            state["buffer"][seq] = row
            self._remove_open_gap_point(state, seq)
            self._drain_buffer(state)
            self._stats["backfills"] += 1
            self._enforce_buffer_cap(state, ts)

            return {"status": "backfilled", "frontier": state["frontier"]}

    def gaps(self, source, *, now=None):
        self._validate_source(source)
        if now is not None:
            now = self._clock_value(now)

        with self._lock:
            state = self._sources.get(source)
            if state is None:
                return []

            result = []
            for item in sorted(state["gaps"], key=lambda gap: gap["start"]):
                status = item["status"]
                if (
                    status == _STATUS_OPEN
                    and now is not None
                    and not self._within_window(item, now)
                ):
                    status = _STATUS_UNCLOSABLE
                result.append(
                    Gap(
                        source=source,
                        start=item["start"],
                        end=item["end"],
                        missing=item["end"] - item["start"] + 1,
                        discovered_at=item["discovered_at"],
                        status=status,
                    )
                )
            return result

    def close_gap(self, source, start):
        self._validate_source(source)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise AuditSeqError(
                "AUDIT_SEQ_INVALID",
                {"field": "start", "value": start},
            )

        with self._lock:
            state = self._sources.get(source)
            if state is None:
                return 0

            for gap in state["gaps"]:
                if gap["start"] == start and gap["status"] == _STATUS_OPEN:
                    missing = gap["end"] - gap["start"] + 1
                    gap["status"] = _STATUS_UNCLOSABLE
                    self._stats["gaps_closed"] += 1
                    return missing
            return 0

    def cursor(self, source):
        self._validate_source(source)
        with self._lock:
            state = self._sources.get(source)
            return 0 if state is None else state["cursor"]

    def ack(self, source, seq):
        self._validate_source(source)
        self._validate_seq(seq)

        with self._lock:
            state = self._sources.get(source)
            if state is None:
                if seq == 0:
                    return 0
                raise AuditSeqError(
                    "AUDIT_SEQ_NOT_DELIVERED",
                    {"source": source, "seq": seq, "frontier": 0},
                )

            if state["retired"]:
                raise AuditSeqError(
                    "AUDIT_SEQ_SOURCE_RETIRED",
                    {"source": source, "seq": seq},
                )
            if seq < state["cursor"]:
                raise AuditSeqError(
                    "AUDIT_SEQ_CURSOR_REGRESSION",
                    {"source": source, "seq": seq, "cursor": state["cursor"]},
                )
            if seq > state["frontier"]:
                raise AuditSeqError(
                    "AUDIT_SEQ_NOT_DELIVERED",
                    {
                        "source": source,
                        "seq": seq,
                        "frontier": state["frontier"],
                    },
                )

            state["cursor"] = seq
            return state["cursor"]

    def resume(self, source):
        self._validate_source(source)
        with self._lock:
            state = self._sources.get(source)
            cursor = 0 if state is None else state["cursor"]
            return cursor + 1

    def events(self, source):
        self._validate_source(source)
        with self._lock:
            state = self._sources.get(source)
            if state is None:
                return []
            rows = [state["events"][seq] for seq in sorted(state["events"])]
            return copy.deepcopy(rows)

    def generation(self, source):
        self._validate_source(source)
        with self._lock:
            state = self._sources.get(source)
            return 0 if state is None else state["generation"]

    def retire(self, source):
        self._validate_source(source)
        with self._lock:
            state = self._sources.get(source)
            if state is None:
                state = self._new_state()
                self._sources[source] = state

            first = not state["retired"]
            if first:
                state["retired"] = True
                self._stats["retirements"] += 1
            return first

    def sources(self):
        with self._lock:
            return sorted(self._sources)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(
                {
                    "version": _VERSION,
                    "sources": self._sources,
                    "stats": self._stats,
                }
            )

    def restore(self, state):
        if not isinstance(state, dict):
            raise AuditSeqError(
                "AUDIT_SEQ_INVALID",
                {"field": "state", "value_type": type(state).__name__},
            )
        if state.get("version", _VERSION) != _VERSION:
            raise AuditSeqError(
                "AUDIT_SEQ_INVALID",
                {"field": "version", "value": state.get("version")},
            )

        restored = copy.deepcopy(state)
        raw_sources = restored.get("sources", {})
        if not isinstance(raw_sources, dict):
            raise AuditSeqError(
                "AUDIT_SEQ_INVALID",
                {"field": "sources"},
            )

        sources: dict[str, dict[str, Any]] = {}
        for source, raw_source in raw_sources.items():
            self._validate_source(source)
            if not isinstance(raw_source, dict):
                raise AuditSeqError(
                    "AUDIT_SEQ_INVALID",
                    {"field": "sources", "source": source},
                )
            sources[source] = self._normalize_state(raw_source)

        stats = {key: 0 for key in _STAT_KEYS}
        raw_stats = restored.get("stats", {})
        if isinstance(raw_stats, dict):
            for key in _STAT_KEYS:
                try:
                    stats[key] = int(raw_stats.get(key, 0))
                except (TypeError, ValueError):
                    stats[key] = 0

        with self._lock:
            self._sources = sources
            self._stats = stats

    def stats(self):
        with self._lock:
            return {key: self._stats[key] for key in _STAT_KEYS}

    @staticmethod
    def _validate_source(source) -> None:
        if not isinstance(source, str) or not source:
            raise AuditSeqError(
                "AUDIT_SEQ_INVALID",
                {"field": "source", "value": source},
            )

    @staticmethod
    def _validate_seq(seq) -> None:
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise AuditSeqError(
                "AUDIT_SEQ_INVALID",
                {"field": "seq", "value": seq},
            )

    @staticmethod
    def _clock_value(value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            return value.timestamp()
        return value

    def _within_window(self, gap: dict[str, Any], now) -> bool:
        window = self.backfill_window
        if window is None or window < 0 or now is None:
            return True
        try:
            return now <= gap["discovered_at"] + window
        except TypeError:
            return True

    @staticmethod
    def _new_state() -> dict[str, Any]:
        return {
            "generation": 1,
            "started": False,
            "frontier": 0,
            "cursor": 0,
            "max_seq": 0,
            "retired": False,
            "events": {},
            "buffer": {},
            "dedup": {},
            "gaps": [],
        }

    def _reset_generation(self, source, state):
        if state is None:
            state = self._new_state()
            self._sources[source] = state
        elif state.get("started"):
            state["generation"] = int(state.get("generation", 1)) + 1

        generation = state["generation"]
        retired = state["retired"]
        state.clear()
        state.update(self._new_state())
        state["generation"] = generation
        state["started"] = True
        state["retired"] = retired
        return state

    @staticmethod
    def _find_gap(state, seq, *, status=None):
        for gap in state["gaps"]:
            if gap["start"] <= seq <= gap["end"]:
                if status is None or gap["status"] == status:
                    return gap
        return None

    def _add_gap_range(self, state, start: int, end: int,
                       discovered_at) -> None:
        if start > end:
            return

        start = max(start, state["frontier"] + 1, state["cursor"] + 1)
        if start > end:
            return

        merged_start = start
        merged_end = end
        first_discovered = discovered_at
        kept = []

        for gap in state["gaps"]:
            if gap["status"] != _STATUS_OPEN:
                kept.append(gap)
                continue
            if gap["start"] <= end + 1 and gap["end"] >= start - 1:
                merged_start = min(merged_start, gap["start"])
                merged_end = max(merged_end, gap["end"])
                first_discovered = min(
                    first_discovered,
                    gap["discovered_at"],
                )
            else:
                kept.append(gap)

        kept.append(
            {
                "start": merged_start,
                "end": merged_end,
                "missing": merged_end - merged_start + 1,
                "discovered_at": first_discovered,
                "status": _STATUS_OPEN,
            }
        )
        kept.sort(key=lambda gap: gap["start"])
        state["gaps"] = kept
        self._normalize_gap_counts(state)

    @staticmethod
    def _remove_open_gap_point(state, seq: int) -> bool:
        gap = AuditSequenceStitcher._find_gap(
            state, seq, status=_STATUS_OPEN
        )
        if gap is None:
            return False

        start = gap["start"]
        end = gap["end"]
        if seq == start and seq == end:
            state["gaps"].remove(gap)
        elif seq == start:
            gap["start"] = seq + 1
        elif seq == end:
            gap["end"] = seq - 1
        else:
            gap["end"] = seq - 1
            state["gaps"].append(
                {
                    "start": seq + 1,
                    "end": end,
                    "missing": end - seq,
                    "discovered_at": gap["discovered_at"],
                    "status": _STATUS_OPEN,
                }
            )

        for item in state["gaps"]:
            item["missing"] = item["end"] - item["start"] + 1
        state["gaps"].sort(key=lambda item: item["start"])
        return True

    @staticmethod
    def _normalize_gap_counts(state) -> None:
        for gap in state["gaps"]:
            gap["missing"] = gap["end"] - gap["start"] + 1

    @staticmethod
    def _drain_buffer(state) -> None:
        while state["frontier"] + 1 in state["buffer"]:
            seq = state["frontier"] + 1
            row = state["buffer"].pop(seq)
            state["events"][seq] = row
            state["frontier"] = seq

    def _enforce_buffer_cap(self, state, trigger_ts) -> None:
        cap = max(0, int(self.buffer_cap))
        while len(state["buffer"]) > cap:
            oldest_seq = min(state["buffer"])
            state["buffer"].pop(oldest_seq)
            state["dedup"].pop(oldest_seq, None)
            self._stats["dropped"] += 1
            self._add_gap_range(
                state, oldest_seq, oldest_seq, trigger_ts
            )

    def _normalize_state(self, raw: dict[str, Any]) -> dict[str, Any]:
        state = self._new_state()
        state["generation"] = int(raw.get("generation", 1) or 1)
        state["started"] = bool(raw.get("started", True))
        state["frontier"] = int(raw.get("frontier", 0) or 0)
        state["cursor"] = int(raw.get("cursor", 0) or 0)
        state["max_seq"] = int(raw.get("max_seq", state["frontier"]) or 0)
        state["retired"] = bool(raw.get("retired", False))

        state["events"] = self._normalize_rows(raw.get("events", {}))
        state["buffer"] = self._normalize_rows(raw.get("buffer", {}))

        dedup: dict[int, Any] = {}
        raw_dedup = raw.get("dedup")
        if isinstance(raw_dedup, dict):
            for key, value in raw_dedup.items():
                seq = int(key)
                dedup[seq] = copy.deepcopy(value)
        elif isinstance(raw_dedup, (list, set, tuple)):
            for value in raw_dedup:
                if isinstance(value, dict) and "seq" in value:
                    seq = int(value["seq"])
                    dedup[seq] = copy.deepcopy(value.get("body"))
                else:
                    dedup[int(value)] = None

        for seq, row in state["events"].items():
            dedup[seq] = copy.deepcopy(row["body"])
        for seq, row in state["buffer"].items():
            dedup[seq] = copy.deepcopy(row["body"])
        state["dedup"] = dedup

        gaps = []
        raw_gaps = raw.get("gaps", [])
        if isinstance(raw_gaps, list):
            for raw_gap in raw_gaps:
                if not isinstance(raw_gap, dict):
                    continue
                start = int(raw_gap["start"])
                end = int(raw_gap["end"])
                gaps.append(
                    {
                        "start": start,
                        "end": end,
                        "missing": end - start + 1,
                        "discovered_at": self._clock_value(
                            raw_gap.get("discovered_at", 0)
                        ),
                        "status": raw_gap.get("status", _STATUS_OPEN),
                    }
                )
        state["gaps"] = sorted(gaps, key=lambda gap: gap["start"])
        return state

    def _normalize_rows(self, raw) -> dict[int, dict[str, Any]]:
        rows: dict[int, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                seq = int(key)
                if isinstance(value, dict) and "body" in value:
                    row = copy.deepcopy(value)
                    row["seq"] = seq
                else:
                    row = {
                        "seq": seq,
                        "body": copy.deepcopy(value),
                        "ts": 0,
                    }
                rows[seq] = row
        elif isinstance(raw, list):
            for value in raw:
                if not isinstance(value, dict) or "seq" not in value:
                    continue
                seq = int(value["seq"])
                row = copy.deepcopy(value)
                row["seq"] = seq
                row.setdefault("ts", 0)
                rows[seq] = row
        return rows
