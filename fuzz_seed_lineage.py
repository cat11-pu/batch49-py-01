"""Fuzz seed lineage: mutation parentage, root tracing, deletion tombstones."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class LineageError(ValueError):
    """Raised when a lineage operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LineageNode:
    """One corpus seed in the mutation graph."""

    node_id: str
    digest: str
    parent_id: object = None
    mutation: str = ""
    depth: int = 0
    deleted: bool = False


class FuzzSeedLineage:
    """Track which seed produced which, and keep the graph bounded."""

    def __init__(self, *, max_depth: int = 64, max_children: int = 8) -> None:
        self.max_depth = max_depth
        self.max_children = max_children
        self._lock = threading.RLock()

    def register_root(self, seed_id: str, data: bytes) -> str:
        raise NotImplementedError

    def mutate(self, parent_id: str, child_id: str, data: bytes,
               mutation: str) -> str:
        raise NotImplementedError

    def node(self, seed_id: str) -> LineageNode:
        raise NotImplementedError

    def ancestors(self, seed_id: str) -> tuple:
        raise NotImplementedError

    def descendants(self, seed_id: str) -> tuple:
        raise NotImplementedError

    def roots(self) -> tuple:
        raise NotImplementedError

    def delete(self, seed_id: str) -> bool:
        raise NotImplementedError

    def lineage_for(self, seed_id: str) -> tuple:
        raise NotImplementedError

    def truncated_children(self, seed_id: str) -> tuple:
        raise NotImplementedError

    def merge(self, other_state: dict) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
