"""Pan law mixer: mono sources placed on a stereo bus with per-sample gain ramps."""

from __future__ import annotations

import threading

PAN_LAWS = ("linear", "equal_power", "minus_6db")

# Condition -> error code carried by PanLawError.code.
ERROR_CODES = {
    "argument": "PAN_ARGUMENT_INVALID",      # empty/non-text id, unknown law name, bad ramp/fade
    "source_exists": "PAN_SOURCE_EXISTS",    # same id added twice
    "source_missing": "PAN_SOURCE_MISSING",  # unknown id on set/remove/meter
    "capacity": "PAN_CAPACITY_EXCEEDED",     # more than max_sources
    "range": "PAN_RANGE_INVALID",            # pan outside [-1, 1] or negative gain
    "frame": "PAN_FRAME_INVALID",            # render frames negative or not an int
    "snapshot": "PAN_SNAPSHOT_INVALID",      # restore payload is not a snapshot dict
}

STATS_KEYS = ("sources", "rendered_frames", "law_switches", "ramps_started",
              "removed", "rejected")


class PanLawError(ValueError):
    """Raised when a mixer operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class PanLawMixer:
    """Route mono sources into a stereo bus using one of three pan laws."""

    def __init__(self, *, max_sources: int = 16,
                 max_ramp_samples: int = 4096) -> None:
        self.max_sources = max_sources
        self.max_ramp_samples = max_ramp_samples
        self._lock = threading.RLock()

    def set_pan_law(self, law: str, *, fade_samples: int = 0) -> str:
        """Switch the pan law; return the new law name."""
        raise NotImplementedError

    def add_source(self, source_id: str, *, pan: float = 0.0,
                   gain: float = 1.0) -> dict:
        """Return {"source_id", "pan", "gain", "pan_law"}."""
        raise NotImplementedError

    def remove_source(self, source_id: str, *, immediate: bool = False) -> int:
        """Remove a source; return the ramp samples still outstanding."""
        raise NotImplementedError

    def set_pan(self, source_id: str, pan: float, *, ramp_samples: int = 0) -> int:
        """Start a pan change; return the ramp length in samples."""
        raise NotImplementedError

    def set_gain(self, source_id: str, gain: float, *, ramp_samples: int = 0) -> int:
        """Start a gain change; return the ramp length in samples."""
        raise NotImplementedError

    def ramp_remaining(self, source_id: str) -> int:
        """Return the samples left in the running ramp."""
        raise NotImplementedError

    def render(self, frames: int) -> list[tuple[float, float]]:
        """Return exactly `frames` (left, right) samples."""
        raise NotImplementedError

    def meter(self, source_id: str) -> dict:
        """Return {"source_id", "pan", "gain", "left", "right",
        "ramp_remaining", "pan_law"}."""
        raise NotImplementedError

    def sources(self) -> list[str]:
        """Return source ids in ascending order."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached state dict accepted by restore()."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore a snapshot produced by snapshot()."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return counters keyed by STATS_KEYS."""
        raise NotImplementedError
