"""Static mesh batching: material groups, index rebasing, 16-bit splits."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class BatchError(ValueError):
    """Rejection raised by the batch builder; ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Batch:
    """One drawable batch produced from a single material group."""

    batch_id: str
    group_key: str
    material: str
    lightmap: str
    vertices: tuple
    indices: tuple


class StaticBatchBuilder:
    """Merge static meshes per material and lightmap, then split on overflow."""

    def __init__(self, *, max_vertices_per_batch: int = 65535,
                 max_batches: int = 8, max_groups: int = 16) -> None:
        self.max_vertices_per_batch = max_vertices_per_batch
        self.max_batches = max_batches
        self.max_groups = max_groups
        self._lock = threading.RLock()

    def group_key(self, material: str, lightmap: str) -> str:
        raise NotImplementedError

    def add_mesh(self, mesh_id: str, *, material: str, lightmap: str,
                 vertices, indices) -> str:
        raise NotImplementedError

    def remove_mesh(self, mesh_id: str) -> int:
        raise NotImplementedError

    def build(self) -> tuple:
        raise NotImplementedError

    def split_overflow(self, group_key: str) -> tuple:
        raise NotImplementedError

    def bounds(self, batch_id: str) -> tuple:
        raise NotImplementedError

    def invalidate(self, group_key: str = None) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
