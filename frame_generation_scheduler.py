"""Frame generation scheduling: slot planning, presentation, drops, latency budget."""

from __future__ import annotations

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


@dataclass(frozen=True)
class RealFrame:
    frame_id: str
    pts_ms: int
    slot: int


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

    def submit_real(self, frame_id: str, pts_ms: int) -> int:
        raise NotImplementedError

    def plan_slots(self, frame_id: str) -> list[int]:
        raise NotImplementedError

    def present(self, slot: int) -> str:
        raise NotImplementedError

    def drop(self, slot: int) -> bool:
        raise NotImplementedError

    def latency_budget(self) -> int:
        raise NotImplementedError

    def refresh_rate(self) -> int:
        raise NotImplementedError

    def set_refresh_rate(self, hz: int) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
