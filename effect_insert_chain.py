"""Ordered effect insert chains with bypass, reordering, and tail flushing."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class EffectError(ValueError):
    """Raised when a chain edit, bypass, flush, or block processing is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SlotView:
    bus_id: str
    position: int
    effect_id: str
    bypassed: bool
    latency_frames: int
    state: str


@dataclass(frozen=True)
class RemoveResult:
    effect_id: str
    state: str
    tail_remaining: int
    position: int


@dataclass(frozen=True)
class ProcessResult:
    bus_id: str
    frames: int
    active: int
    bypassed: int
    flushing: int
    latency_frames: int


class EffectInsertChain:
    """Own per-bus ordered effect slots plus a global effect registry."""

    def __init__(self, *, slots_per_bus: int = 4, max_buses: int = 8,
                 block_size: int = 64, max_tail_frames: int = 200) -> None:
        self.slots_per_bus = slots_per_bus
        self.max_buses = max_buses
        self.block_size = block_size
        self.max_tail_frames = max_tail_frames
        self._lock = threading.RLock()

    def add_bus(self, bus_id: str) -> None:
        raise NotImplementedError

    def register_effect(self, effect_id: str, *, latency_frames: int,
                        tail_frames: int = 0) -> None:
        raise NotImplementedError

    def insert(self, bus_id: str, effect_id: str, *,
               position: int | None = None) -> SlotView:
        raise NotImplementedError

    def remove(self, bus_id: str, effect_id: str, *,
               force: bool = False) -> RemoveResult:
        raise NotImplementedError

    def move(self, bus_id: str, effect_id: str, new_position: int) -> list[str]:
        raise NotImplementedError

    def bypass(self, bus_id: str, effect_id: str,
               bypassed: bool = True) -> SlotView:
        raise NotImplementedError

    def flush_tail(self, bus_id: str, *, effect_id: str | None = None,
                   frames: int = 0) -> int:
        raise NotImplementedError

    def process_block(self, bus_id: str, frames: int) -> ProcessResult:
        raise NotImplementedError

    def order(self, bus_id: str) -> list[str]:
        raise NotImplementedError

    def latency(self, bus_id: str) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
