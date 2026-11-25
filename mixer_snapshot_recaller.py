"""Mixer snapshot recaller: named snapshots with interpolated transitions."""

from __future__ import annotations

import threading

PARAM_KINDS = {
    "fader": "numeric",
    "pan": "numeric",
    "send": "numeric",
    "mute": "discrete",
    "bypass": "discrete",
}

# Condition -> error code carried by RecallError.code.
ERROR_CODES = {
    "argument": "RECALL_ARGUMENT_INVALID",       # empty/non-text names, bad params table
    "param_unknown": "RECALL_PARAM_UNKNOWN",     # name missing from PARAM_KINDS
    "type": "RECALL_TYPE_INVALID",               # bool/str where a number or bool is required
    "range": "RECALL_RANGE_INVALID",             # numeric value outside its range
    "capacity": "RECALL_CAPACITY_EXCEEDED",      # more than max_snapshots
    "snapshot_missing": "RECALL_SNAPSHOT_MISSING",  # recall/compare of an unknown name
    "transition": "RECALL_TRANSITION_INVALID",   # bad transition_ms or bad step
    "nothing_to_undo": "RECALL_NOTHING_TO_UNDO",  # undo without a previous recall
    "snapshot_invalid": "RECALL_SNAPSHOT_INVALID",  # restore payload is not a snapshot
}

STATS_KEYS = ("captures", "recalls", "applied", "undos", "rejected")


class RecallError(ValueError):
    """Raised when a recall operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MixerSnapshotRecaller:
    """Capture mixer parameters and recall them with a timed transition."""

    def __init__(self, *, max_snapshots: int = 8, max_params: int = 16,
                 max_transition_ms: int = 5000, params=None) -> None:
        self.max_snapshots = max_snapshots
        self.max_params = max_params
        self.max_transition_ms = max_transition_ms
        self._lock = threading.RLock()

    def set_param(self, name: str, value) -> dict:
        """Return {"name", "value", "kind"}."""
        raise NotImplementedError

    def capture(self, name: str) -> dict:
        """Return {"name", "values", "revision"}."""
        raise NotImplementedError

    def recall(self, name: str, *, transition_ms: int = 0) -> dict:
        """Return {"status", "duration"}; status is "applied" or "running"."""
        raise NotImplementedError

    def set_exclude(self, names) -> list[str]:
        """Replace the excluded set; return it sorted."""
        raise NotImplementedError

    def compare(self, first: str, second: str) -> list[dict]:
        """Return [{"param", "from", "to"}] sorted by param."""
        raise NotImplementedError

    def undo_recall(self) -> dict:
        """Return {"status", "restored"}; status is "undone"."""
        raise NotImplementedError

    def interpolate(self, ms: int) -> dict:
        """Advance the transition; return {"progress", "active", "values"}."""
        raise NotImplementedError

    def current_values(self) -> dict:
        """Return {param: value} in declaration order."""
        raise NotImplementedError

    def transition_state(self) -> dict:
        """Return {"active", "elapsed", "duration", "name"}."""
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
