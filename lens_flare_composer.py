"""Lens flare composer: mirrored element chain, occlusion sampling, screen clipping."""

from __future__ import annotations

import threading


class LensFlareError(ValueError):
    """Raised when a flare composition request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LensFlareComposer:
    """Compose lens flare elements from a screen-space light source."""

    def __init__(self, *, width: int = 1920, height: int = 1080,
                 max_elements: int = 8, max_samples: int = 16,
                 max_flares_per_frame: int = 4) -> None:
        self.width = width
        self.height = height
        self.max_elements = max_elements
        self.max_samples = max_samples
        self.max_flares_per_frame = max_flares_per_frame
        self._lock = threading.RLock()

    def size(self) -> tuple[int, int]:
        """Return the current render target size as (width, height)."""
        raise NotImplementedError

    def resize(self, width: int, height: int) -> None:
        """Resize the render target, keeping normalized element positions valid."""
        raise NotImplementedError

    def set_source(self, x: float, y: float, depth: float) -> None:
        """Place the light source at a normalized screen position."""
        raise NotImplementedError

    def add_element(self, element_id: str, offset: float, weight: float,
                    radius: float) -> None:
        """Register one flare element along the source-to-center chain."""
        raise NotImplementedError

    def remove_element(self, element_id: str) -> bool:
        """Drop a registered element, returning whether it existed."""
        raise NotImplementedError

    def layout_elements(self) -> list:
        """Return (element_id, nx, ny) rows ordered by offset then id."""
        raise NotImplementedError

    def add_occluder(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Add an axis-aligned occluder rectangle in normalized space."""
        raise NotImplementedError

    def occlusion_sample(self, element_id: str) -> float:
        """Return the visible sample fraction of one element in [0.0, 1.0]."""
        raise NotImplementedError

    def clip_to_screen(self, element_id: str) -> tuple | None:
        """Return the element box clipped to the screen, or None when off-screen."""
        raise NotImplementedError

    def intensity(self, element_id: str) -> float:
        """Return the attenuated intensity of one element."""
        raise NotImplementedError

    def pixel_offset(self, element_id: str) -> tuple[float, float]:
        """Return the pixel offset of one element from the screen center."""
        raise NotImplementedError

    def compose_frame(self) -> list:
        """Select and return the flare entries for the current frame."""
        raise NotImplementedError

    def budget(self) -> dict:
        """Return per-frame and per-composer counters."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached snapshot of sizes, source, elements and counters."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore state produced by snapshot, rejecting malformed input."""
        raise NotImplementedError
