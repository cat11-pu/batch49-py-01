"""Wide-gamut to sRGB mapping: transfer decode, chroma compression, quantize, dither."""

from __future__ import annotations

import threading


class GamutError(ValueError):
    """Raised when a gamut mapping request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class GamutMapChain:
    """Map linear display-P3 pixels onto a whitelisted target gamut."""

    def __init__(self, *, target: str = "srgb", bit_depth: int = 8,
                 dither_strength: float = 0.5, block_size: int = 64,
                 max_pixels: int = 1000000) -> None:
        self._target = target
        self._bit_depth = bit_depth
        self._dither_strength = dither_strength
        self._block_size = block_size
        self._max_pixels = max_pixels
        self._lock = threading.RLock()

    def set_target(self, target: str, bit_depth: int | None = None) -> None:
        """Switch the target gamut and optionally the output bit depth."""
        raise NotImplementedError

    def params(self) -> dict:
        """Return the active conversion parameters as one dict."""
        raise NotImplementedError

    def decode(self, value: float, transfer: str = "srgb") -> float:
        """Decode one channel from the named transfer function to linear light."""
        raise NotImplementedError

    def encode(self, value: float, transfer: str = "srgb") -> float:
        """Encode one linear channel with the named transfer function."""
        raise NotImplementedError

    def compress_chroma(self, rgb) -> tuple:
        """Compress one linear pixel into the target gamut, keeping luminance."""
        raise NotImplementedError

    def quantize(self, value: float, *, x: int = 0, y: int = 0,
                 frame: int = 0) -> float:
        """Quantize one channel to the target bit depth with deterministic dither."""
        raise NotImplementedError

    def convert(self, pixels) -> list:
        """Convert a batch of encoded P3 pixels through the whole chain."""
        raise NotImplementedError

    def out_of_gamut(self) -> int:
        """Return how many converted pixels left the target gamut before compression."""
        raise NotImplementedError

    def clamp_count(self) -> int:
        """Return how many channels were clamped before transfer decoding."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return converted, out_of_gamut and clamped counters."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached snapshot of parameters and counters."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore state produced by snapshot, rejecting malformed input."""
        raise NotImplementedError
