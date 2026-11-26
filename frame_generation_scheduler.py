"""Frame generation scheduling: slot planning, presentation, drops, latency budget."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass


class FrameGenError(ValueError):
    """Refused frame generation operation carrying a stable error code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


DEFAULT_TARGET_HZ = 50
DEFAULT_MAX_GENERATED_PER_GAP = 2
DEFAULT_LATENCY_BUDGET_MS = 50
DEFAULT_MAX_INFLIGHT_GENERATED = 8

HARD_MAX_GENERATED_PER_GAP = 2
SNAPSHOT_VERSION = 1
COUNTER_KEYS = ("real", "planned", "presented", "dropped", "skipped")


@dataclass(frozen=True)
class RealFrame:
    frame_id: str
    pts_ms: int
    slot: int


@dataclass
class _SlotEntry:
    kind: str  # "real" or "gen"
    owner: str  # real frame id; for generated frames the id of the gap-closing real frame
    seq: int  # 0 for real frames, 1-based for generated frames
    presented: bool = False


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class FrameGenerationScheduler:
    """Place generated frames into presentation slots between real frames."""

    def __init__(
        self,
        target_hz: int = DEFAULT_TARGET_HZ,
        max_generated_per_gap: int = DEFAULT_MAX_GENERATED_PER_GAP,
        latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS,
        max_inflight_generated: int = DEFAULT_MAX_INFLIGHT_GENERATED,
    ) -> None:
        self.target_hz = target_hz
        self.max_generated_per_gap = max_generated_per_gap
        self.latency_budget_ms = latency_budget_ms
        self.max_inflight_generated = max_inflight_generated
        self._lock = threading.RLock()
        self._frames: dict[str, RealFrame] = {}
        self._order: list[str] = []
        self._slots: dict[int, _SlotEntry] = {}
        self._counters = {key: 0 for key in COUNTER_KEYS}

    def _slot_for(self, pts_ms: int) -> int:
        return (pts_ms * self.target_hz) // 1000

    def _budget_slots(self) -> int:
        return (self.latency_budget_ms * self.target_hz) // 1000

    def _inflight_locked(self) -> int:
        return sum(
            1
            for entry in self._slots.values()
            if entry.kind == "gen" and not entry.presented
        )

    def submit_real(self, frame_id: str, pts_ms: int) -> int:
        with self._lock:
            if not isinstance(frame_id, str) or not frame_id:
                raise FrameGenError("INVALID_FRAME", frame_id)
            if not _is_int(pts_ms) or pts_ms < 0:
                raise FrameGenError("INVALID_FRAME", pts_ms)
            if frame_id in self._frames:
                raise FrameGenError("FRAME_DUPLICATE", frame_id)

            slot = self._slot_for(pts_ms)

            # A newer real frame overtakes queued generated frames: cancel the
            # ones not yet presented and reclaim their slots.
            for stale in [
                s
                for s, entry in self._slots.items()
                if entry.kind == "gen" and not entry.presented and s >= slot
            ]:
                del self._slots[stale]
                self._counters["dropped"] += 1

            prev = None
            for other_id in self._order:
                other = self._frames[other_id]
                if other.slot < slot and (prev is None or other.slot > prev.slot):
                    prev = other

            self._frames[frame_id] = RealFrame(frame_id, pts_ms, slot)
            self._order.append(frame_id)
            if slot not in self._slots:
                self._slots[slot] = _SlotEntry("real", frame_id, 0)
            self._counters["real"] += 1

            planned = 0
            if prev is not None:
                available = slot - prev.slot - 1
                wanted = min(
                    available,
                    self.max_generated_per_gap,
                    HARD_MAX_GENERATED_PER_GAP,
                    self._budget_slots(),
                )
                if wanted > 0:
                    room = self.max_inflight_generated - self._inflight_locked()
                    planned = max(0, min(wanted, room))
                    if planned == 0:
                        self._counters["skipped"] += wanted
                    for seq in range(1, planned + 1):
                        self._slots[prev.slot + seq] = _SlotEntry("gen", frame_id, seq)
                    self._counters["planned"] += planned
            return planned

    def plan_slots(self, frame_id: str) -> list[int]:
        with self._lock:
            if frame_id not in self._frames:
                raise FrameGenError("FRAME_UNKNOWN", frame_id)
            return sorted(
                slot
                for slot, entry in self._slots.items()
                if entry.kind == "gen" and entry.owner == frame_id
            )

    def present(self, slot: int) -> str:
        with self._lock:
            entry = self._slots.get(slot)
            if entry is None:
                raise FrameGenError("SLOT_EMPTY", slot)
            if entry.presented:
                raise FrameGenError("SLOT_ALREADY_PRESENTED", slot)
            entry.presented = True
            self._counters["presented"] += 1
            if entry.kind == "real":
                return entry.owner
            return f"gen:{entry.owner}:{entry.seq}"

    def drop(self, slot: int) -> bool:
        with self._lock:
            entry = self._slots.get(slot)
            if entry is None:
                raise FrameGenError("SLOT_EMPTY", slot)
            if entry.kind == "real":
                raise FrameGenError("SLOT_NOT_DROPPABLE", slot)
            if entry.presented:
                raise FrameGenError("SLOT_ALREADY_PRESENTED", slot)
            del self._slots[slot]
            self._counters["dropped"] += 1
            return True

    def latency_budget(self) -> int:
        return self.latency_budget_ms

    def refresh_rate(self) -> int:
        return self.target_hz

    def set_refresh_rate(self, hz: int) -> None:
        with self._lock:
            if not _is_int(hz) or hz <= 0:
                raise FrameGenError("INVALID_RATE", hz)
            self.target_hz = hz

    def stats(self) -> dict:
        with self._lock:
            result = dict(self._counters)
            result["inflight"] = self._inflight_locked()
            return result

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": SNAPSHOT_VERSION,
                "frames": {
                    frame_id: {
                        "pts_ms": self._frames[frame_id].pts_ms,
                        "slot": self._frames[frame_id].slot,
                        "order": index,
                    }
                    for index, frame_id in enumerate(self._order)
                },
                "slots": {
                    slot: {
                        "kind": entry.kind,
                        "owner": entry.owner,
                        "seq": entry.seq,
                        "presented": entry.presented,
                    }
                    for slot, entry in self._slots.items()
                },
                "counters": dict(self._counters),
                "config": {
                    "target_hz": self.target_hz,
                    "max_generated_per_gap": self.max_generated_per_gap,
                    "latency_budget_ms": self.latency_budget_ms,
                    "max_inflight_generated": self.max_inflight_generated,
                },
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            frames, order, slots, counters, config = self._parse_snapshot(state)
            self._frames = frames
            self._order = order
            self._slots = slots
            self._counters = counters
            self.target_hz = config["target_hz"]
            self.max_generated_per_gap = config["max_generated_per_gap"]
            self.latency_budget_ms = config["latency_budget_ms"]
            self.max_inflight_generated = config["max_inflight_generated"]

    def _parse_snapshot(self, state: object):
        def invalid(detail: object = None) -> FrameGenError:
            return FrameGenError("SNAPSHOT_INVALID", detail)

        if not isinstance(state, dict):
            raise invalid(state)
        if not {"version", "frames", "slots", "counters", "config"} <= set(state):
            raise invalid(state)
        if state["version"] != SNAPSHOT_VERSION:
            raise invalid(state.get("version"))

        raw_frames = state["frames"]
        raw_slots = state["slots"]
        raw_counters = state["counters"]
        raw_config = state["config"]
        if not isinstance(raw_frames, dict):
            raise invalid(raw_frames)
        if not isinstance(raw_slots, dict):
            raise invalid(raw_slots)
        if not isinstance(raw_counters, dict):
            raise invalid(raw_counters)
        if not isinstance(raw_config, dict):
            raise invalid(raw_config)

        frames: dict[str, RealFrame] = {}
        ordered: list[tuple[int, str]] = []
        for frame_id, record in raw_frames.items():
            if not isinstance(frame_id, str) or not frame_id:
                raise invalid(frame_id)
            if not isinstance(record, dict):
                raise invalid(record)
            pts_ms = record.get("pts_ms")
            slot = record.get("slot")
            order = record.get("order", len(ordered))
            if not _is_int(pts_ms) or pts_ms < 0:
                raise invalid(record)
            if not _is_int(slot) or slot < 0:
                raise invalid(record)
            if not _is_int(order) or order < 0:
                raise invalid(record)
            frames[frame_id] = RealFrame(frame_id, pts_ms, slot)
            ordered.append((order, frame_id))
        order = [frame_id for _, frame_id in sorted(ordered)]

        slots: dict[int, _SlotEntry] = {}
        for raw_slot, record in raw_slots.items():
            if _is_int(raw_slot):
                slot = raw_slot
            elif isinstance(raw_slot, str) and raw_slot.lstrip("-").isdigit():
                slot = int(raw_slot)
            else:
                raise invalid(raw_slot)
            if slot < 0 or not isinstance(record, dict):
                raise invalid(record)
            kind = record.get("kind")
            owner = record.get("owner")
            seq = record.get("seq")
            presented = record.get("presented", False)
            if kind not in ("real", "gen"):
                raise invalid(record)
            if not isinstance(owner, str) or owner not in frames:
                raise invalid(record)
            if not _is_int(seq) or seq < 0:
                raise invalid(record)
            if kind == "real" and (seq != 0 or frames[owner].slot != slot):
                raise invalid(record)
            if kind == "gen" and seq < 1:
                raise invalid(record)
            slots[slot] = _SlotEntry(kind, owner, seq, bool(presented))

        counters = {}
        for key in COUNTER_KEYS:
            value = raw_counters.get(key, 0)
            if not _is_int(value) or value < 0:
                raise invalid(raw_counters)
            counters[key] = value

        config = {
            "target_hz": raw_config.get("target_hz", self.target_hz),
            "max_generated_per_gap": raw_config.get(
                "max_generated_per_gap", self.max_generated_per_gap
            ),
            "latency_budget_ms": raw_config.get(
                "latency_budget_ms", self.latency_budget_ms
            ),
            "max_inflight_generated": raw_config.get(
                "max_inflight_generated", self.max_inflight_generated
            ),
        }
        if not _is_int(config["target_hz"]) or config["target_hz"] <= 0:
            raise invalid(raw_config)
        for key in ("max_generated_per_gap", "latency_budget_ms", "max_inflight_generated"):
            if not _is_int(config[key]) or config[key] < 0:
                raise invalid(raw_config)

        return frames, order, slots, counters, copy.deepcopy(config)
