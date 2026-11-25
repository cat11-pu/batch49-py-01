"""Atlas generation swapping: UV remap tables, in-flight pinning, whole-generation rollback."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AtlasGenerationError(ValueError):
    """Refused generation operation; ``code`` carries an ATLAS_* identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class UVMapping:
    generation: str
    slot_id: str
    page_id: str
    u0: float
    v0: float
    u1: float
    v1: float


@dataclass(frozen=True)
class GenerationState:
    generation: str
    pages: tuple[str, ...]
    slots: tuple[str, ...]
    committed: bool


class AtlasGenerationSwapper:
    """Build atlas generations, swap them atomically and pin in-flight frames."""

    def __init__(self, *, max_pages_per_generation: int = 8,
                 max_pins: int = 16) -> None:
        self.max_pages_per_generation = max_pages_per_generation
        self.max_pins = max_pins
        self._lock = threading.RLock()

    def begin_generation(self, generation: str) -> str:
        """Open a new staging generation and return its id."""
        raise NotImplementedError

    def stage_page(self, generation: str, page_id: str) -> None:
        """Attach one page to a staging generation."""
        raise NotImplementedError

    def stage_uv(self, generation: str, slot_id: str, page_id: str,
                 u0: float, v0: float, u1: float, v1: float) -> None:
        """Record the slot-to-page UV mapping inside a staging generation."""
        raise NotImplementedError

    def commit_generation(self, generation: str) -> int:
        """Atomically promote a staging generation and return its ordinal."""
        raise NotImplementedError

    def pin(self, generation: str | None = None) -> int:
        """Pin a generation for an in-flight frame and return a handle."""
        raise NotImplementedError

    def unpin(self, handle: int) -> int:
        """Release a pin handle and return the remaining pins of its generation."""
        raise NotImplementedError

    def active_generation(self) -> str:
        """Return the generation new requests must use."""
        raise NotImplementedError

    def lookup(self, slot_id: str,
               generation: str | None = None) -> UVMapping:
        """Resolve one slot to its page and UV rectangle."""
        raise NotImplementedError

    def collect(self) -> tuple[str, ...]:
        """Release pages of retired generations that no frame pins any more."""
        raise NotImplementedError

    def rollback(self) -> str:
        """Discard the active generation and return to the previous one."""
        raise NotImplementedError

    def generations(self) -> tuple[str, ...]:
        """Return the ids of the generations currently tracked."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Capture generations, mappings, pins and counters as a detached dict."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Replace the live state with a snapshot."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return swapper counters."""
        raise NotImplementedError
