"""Shader variant precompilation: keyword folding, deterministic queueing and batching."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ShaderVariantError(ValueError):
    """Refused variant operation; ``code`` carries a SHADER_* identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Material:
    material_id: str
    keywords: tuple[str, ...]


class ShaderVariantPrecompiler:
    """Fold material keyword sets into variants and feed a bounded compile queue."""

    def __init__(self, *, max_keywords: int = 8, batch_size: int = 4,
                 total_budget: int = 32) -> None:
        self.max_keywords = max_keywords
        self.batch_size = batch_size
        self.total_budget = total_budget
        self._lock = threading.RLock()

    def normalize_keywords(self, keywords) -> tuple[str, ...]:
        """Return the canonical sorted keyword tuple of one material."""
        raise NotImplementedError

    def plan_from_materials(self, materials) -> tuple[str, ...]:
        """Enumerate, fold and order every variant the materials need."""
        raise NotImplementedError

    def next_batch(self) -> tuple[str, ...]:
        """Hand out the next batch of uncompiled variants without repeats."""
        raise NotImplementedError

    def mark_compiled(self, key: str, ok: bool = True,
                      error: str | None = None) -> None:
        """Record the compile result of one variant."""
        raise NotImplementedError

    def fallback_for(self, key: str) -> str:
        """Return the cheaper variant used when one variant is missing."""
        raise NotImplementedError

    def progress(self) -> dict:
        """Return planned, queued, compiled, failed and remaining counts."""
        raise NotImplementedError

    def errors(self) -> dict:
        """Return the compile error per failed variant key."""
        raise NotImplementedError

    def variants(self) -> tuple[str, ...]:
        """Return the planned variant keys in ascending order."""
        raise NotImplementedError

    def cancel_for_material(self, material_id: str) -> int:
        """Drop queued but uncompiled variants that only the material needed."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Capture plan, queue, results and counters as a detached dict."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Replace the live state with a snapshot."""
        raise NotImplementedError
