"""Geomip LOD morph guard: block-level blending and shared seam coefficients."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class MorphError(ValueError):
    """Rejection raised by the morph guard; ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class BlockState:
    """Snapshot of one terrain block's morph state."""

    block_id: str
    level: int
    morph_from: int
    progress: int
    visible: bool
    vertices: tuple


class GeomipMorphGuard:
    """Drive block LOD changes through a distance driven morph ramp."""

    def __init__(self, *, max_blocks: int = 64, max_level: int = 4,
                 morph_frames: int = 4, step_limit: int = 2,
                 max_concurrent: int = 4) -> None:
        self.max_blocks = max_blocks
        self.max_level = max_level
        self.morph_frames = morph_frames
        self.step_limit = step_limit
        self.max_concurrent = max_concurrent
        self._lock = threading.RLock()

    def register_block(self, block_id: str, *, vertices=(), level: int = 0) -> BlockState:
        raise NotImplementedError

    def set_block_level(self, block_id: str, level: int) -> BlockState:
        raise NotImplementedError

    def block_state(self, block_id: str) -> BlockState:
        raise NotImplementedError

    def morph_factor(self, block_id: str) -> float:
        raise NotImplementedError

    def blend_positions(self, block_id: str, high, low) -> tuple:
        raise NotImplementedError

    def seam_vertices(self, block_a: str, block_b: str) -> tuple:
        raise NotImplementedError

    def common_morph_factor(self, block_ids) -> float:
        raise NotImplementedError

    def advance(self, frames: int = 1) -> dict:
        raise NotImplementedError

    def unload_block(self, block_id: str) -> None:
        raise NotImplementedError

    def reload_block(self, block_id: str, *, level: int) -> BlockState:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
