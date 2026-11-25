"""Multi-view LOD arbitration for main, shadow and reflection passes."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class LodError(ValueError):
    """Rejection raised by the arbiter; ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ViewSpec:
    """Registered view and its arbitration weight."""

    view_id: str
    priority: int


@dataclass(frozen=True)
class LodDecision:
    """Outcome of one arbitrate() call for a single object."""

    object_id: str
    demanded: int
    resident: int
    switched: str
    deferred: bool


class MultiViewLodArbiter:
    """Merge per-view LOD demands into one resident level per object."""

    def __init__(self, *, max_views: int = 8, max_lod: int = 3,
                 frame_upgrade_budget: int = 2, cooldown_frames: int = 2,
                 hysteresis: int = 1) -> None:
        self.max_views = max_views
        self.max_lod = max_lod
        self.frame_upgrade_budget = frame_upgrade_budget
        self.cooldown_frames = cooldown_frames
        self.hysteresis = hysteresis
        self._lock = threading.RLock()

    def register_view(self, view_id: str, priority: int = 0) -> ViewSpec:
        raise NotImplementedError

    def unregister_view(self, view_id: str) -> int:
        raise NotImplementedError

    def set_view_priority(self, view_id: str, priority: int) -> None:
        raise NotImplementedError

    def request(self, view_id: str, object_id: str, lod: int, frame: int) -> None:
        raise NotImplementedError

    def arbitrate(self, object_id: str, frame: int) -> LodDecision:
        raise NotImplementedError

    def arbitrate_frame(self, frame: int) -> dict:
        raise NotImplementedError

    def resident_lod(self, object_id: str) -> int:
        raise NotImplementedError

    def upgrade_budget(self, frame: int) -> int:
        raise NotImplementedError

    def decision_log(self) -> tuple:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
