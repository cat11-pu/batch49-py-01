"""UI quad batching: texture pages, clip breaks and stable layer order."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class UiError(ValueError):
    """Rejection raised by the composer; ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Quad:
    """One pending UI element in submission order."""

    element_id: str
    page: str
    layer: int
    rect: tuple
    clip: object
    mask: object


@dataclass(frozen=True)
class UiBatch:
    """A composed batch of consecutive, batchable UI elements."""

    batch_id: str
    page: str
    clip: object
    mask: object
    elements: tuple


class UiBatchComposer:
    """Compose UI quads into batches while preserving draw order."""

    def __init__(self, *, max_quads_per_batch: int = 4, max_pages: int = 8,
                 max_batches: int = 8) -> None:
        self.max_quads_per_batch = max_quads_per_batch
        self.max_pages = max_pages
        self.max_batches = max_batches
        self._lock = threading.RLock()

    def push_quad(self, element_id: str, *, page: str, layer: int, rect,
                  clip=None, mask=None) -> None:
        raise NotImplementedError

    def push_clip(self, element_id: str, *, page: str, layer: int, rect,
                  clip) -> None:
        raise NotImplementedError

    def push_mask(self, element_id: str, *, page: str, layer: int, rect,
                  clip=None, mask_op: str = "") -> None:
        raise NotImplementedError

    def flush(self) -> tuple:
        raise NotImplementedError

    def batches(self) -> tuple:
        raise NotImplementedError

    def pending_elements(self) -> tuple:
        raise NotImplementedError

    def reorder(self, element_id: str, new_layer: int) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
