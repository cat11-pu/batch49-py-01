"""Patch rollout in asset groups with wave gating, rollback and replay."""

from __future__ import annotations

from dataclasses import dataclass, field


class PatchWaveError(ValueError):
    """Raised when a wave operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Asset:
    asset_id: str
    group: str
    patch: str


@dataclass
class WaveState:
    index: int
    group: str
    status: str = "pending"
    applied: tuple = ()
    attempts: int = 0


@dataclass(frozen=True)
class WaveResult:
    index: int
    status: str
    applied: tuple
    health: dict


class PatchWaveOrchestrator:
    """Apply patch assets wave by wave with health gates and compensation."""

    def __init__(self, apply, revert, *, max_waves: int = 8) -> None:
        self._apply = apply
        self._revert = revert
        self.max_waves = max_waves

    def plan(self, assets) -> list:
        raise NotImplementedError

    def run_next(self, health_for) -> WaveResult:
        raise NotImplementedError

    def run_all(self, health_for) -> list:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def resume(self) -> None:
        raise NotImplementedError

    def status(self) -> dict:
        raise NotImplementedError

    def rollback(self, wave_index=None) -> tuple:
        raise NotImplementedError

    def replay(self, wave_index) -> WaveResult:
        raise NotImplementedError

    def applied_order(self) -> list:
        raise NotImplementedError

    def pending(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
