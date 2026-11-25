"""Tiled image import pipeline: stage registration, tile planning, drain and resume."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class TilePipelineError(ValueError):
    """Refused pipeline operation; ``code`` carries a TILE_* identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StageSpec:
    name: str
    halo: int
    edge_mode: str
    cost_per_pixel: int


@dataclass(frozen=True)
class TileSpec:
    tile_id: int
    x: int
    y: int
    width: int
    height: int
    read_x: int
    read_y: int
    read_width: int
    read_height: int
    halo: int


@dataclass(frozen=True)
class TileResult:
    tile_id: int
    width: int
    height: int
    digest: str
    stage_digests: tuple[str, ...]


class TileImagePipeline:
    """Plan and execute tiled operators over an oversized image."""

    def __init__(self, tile_width: int, tile_height: int, *,
                 max_inflight: int = 4, max_buffer_bytes: int = 1048576,
                 max_tiles: int = 256, clock=None) -> None:
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.max_inflight = max_inflight
        self.max_buffer_bytes = max_buffer_bytes
        self.max_tiles = max_tiles
        self._clock = clock
        self._lock = threading.RLock()

    def register_stage(self, name: str, func, *, halo: int,
                       edge_mode: str = "clamp",
                       cost_per_pixel: int = 1) -> int:
        """Register an operator and return its 1-based position in the pipeline."""
        raise NotImplementedError

    def stages(self) -> tuple[str, ...]:
        """Return the registered operator names in execution order."""
        raise NotImplementedError

    def plan_tiles(self, image_width: int,
                   image_height: int) -> list[TileSpec]:
        """Split the image into row-major tiles with halo-expanded read extents."""
        raise NotImplementedError

    def enqueue(self, image_width: int, image_height: int) -> int:
        """Queue every planned tile and return how many were newly queued."""
        raise NotImplementedError

    def run_tile(self, tile_id: int) -> TileResult:
        """Execute the operator chain for one tile and return its result."""
        raise NotImplementedError

    def drain(self) -> list[TileResult]:
        """Run all queued tiles in order, honouring the in-flight limit."""
        raise NotImplementedError

    def resume(self) -> int:
        """Return how many tiles still need work after a restore."""
        raise NotImplementedError

    def cancel(self) -> int:
        """Drop tiles that have not been executed yet; return the count."""
        raise NotImplementedError

    def tile_spec(self, tile_id: int) -> TileSpec:
        """Return the planned geometry of one tile."""
        raise NotImplementedError

    def tile_result(self, tile_id: int) -> TileResult | None:
        """Return the stored result of a tile, or None when it is not done."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Capture stages, plans, queue, results and counters as a detached dict."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Replace the live state with a snapshot, continuing from finished tiles."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return pipeline counters."""
        raise NotImplementedError
