"""Indexed-colour quantization: median cut palette, serpentine error diffusion."""

from __future__ import annotations

import threading


class PaletteError(ValueError):
    """Raised when a palette quantization request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class PaletteQuantizer:
    """Quantize true-colour frames onto a fixed-capacity indexed palette."""

    def __init__(self, *, capacity: int = 16, dither_strength: float = 0.75,
                 max_pixels_per_frame: int = 4096) -> None:
        self.capacity = capacity
        self.dither_strength = dither_strength
        self.max_pixels_per_frame = max_pixels_per_frame
        self._lock = threading.RLock()

    def add_color(self, rgb, count: int = 1) -> None:
        """Add one colour to the histogram, accumulating repeats."""
        raise NotImplementedError

    def histogram(self) -> dict:
        """Return a copy of the colour histogram."""
        raise NotImplementedError

    def build_palette(self) -> tuple:
        """Rebuild the palette with median cut and return it."""
        raise NotImplementedError

    def palette(self) -> tuple:
        """Return the current palette, rebuilding it when the histogram changed."""
        raise NotImplementedError

    def nearest_index(self, rgb) -> int:
        """Return the palette index closest to one opaque colour."""
        raise NotImplementedError

    def remap_frame(self, pixels, width: int) -> list:
        """Map a row-major frame onto palette indices without error diffusion."""
        raise NotImplementedError

    def dither(self, pixels, width: int) -> list:
        """Map a frame onto palette indices with serpentine error diffusion."""
        raise NotImplementedError

    def quantize(self, pixels, width: int) -> list:
        """Map a frame, applying diffusion when the strength is positive."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return frame, colour, pixel, palette and discarded-error counters."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached snapshot of capacity, histogram, palette and counters."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore state produced by snapshot, rejecting malformed input."""
        raise NotImplementedError
