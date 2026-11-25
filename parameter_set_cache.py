"""Parameter set cache: SPS/PPS/VPS storage with versioned activation."""

from __future__ import annotations

import threading

PS_KINDS = {
    "sps": (0, 31),
    "pps": (0, 255),
    "vps": (0, 15),
}

# Condition -> error code carried by ParameterSetError.code.
ERROR_CODES = {
    "argument": "PS_ARGUMENT_INVALID",       # non-text kind/stream, bool or str id, non-bytes payload
    "kind_unknown": "PS_KIND_UNKNOWN",       # kind not in PS_KINDS
    "id_range": "PS_ID_OUT_OF_RANGE",        # id outside the range of its kind
    "payload_empty": "PS_PAYLOAD_EMPTY",     # zero length payload
    "payload_large": "PS_PAYLOAD_TOO_LARGE",  # longer than max_payload_bytes
    "id_missing": "PS_ID_MISSING",           # unknown id on get/activate
    "version_missing": "PS_VERSION_MISSING",  # version dropped by retention
    "cache_full": "PS_CACHE_FULL",           # new id while the kind is full
    "snapshot_invalid": "PS_SNAPSHOT_INVALID",
}

STATS_KEYS = ("puts", "changes", "activations", "pending", "invalidated",
              "rejected")


class ParameterSetError(ValueError):
    """Raised when a parameter set operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ParameterSetCache:
    """Store parameter sets by kind and id, activate them per stream."""

    def __init__(self, *, max_entries_per_kind: int = 32,
                 max_payload_bytes: int = 1024, max_versions: int = 4) -> None:
        self.max_entries_per_kind = max_entries_per_kind
        self.max_payload_bytes = max_payload_bytes
        self.max_versions = max_versions
        self._lock = threading.RLock()

    def put(self, kind: str, ps_id: int, payload: bytes) -> dict:
        """Return {"kind", "ps_id", "version", "changed"}."""
        raise NotImplementedError

    def get(self, kind: str, ps_id: int, *, version: int | None = None) -> dict:
        """Return {"kind", "ps_id", "version", "payload"}."""
        raise NotImplementedError

    def activate(self, stream_id: str, kind: str, ps_id: int) -> dict:
        """Return {"stream_id", "kind", "ps_id", "version", "applied"}."""
        raise NotImplementedError

    def bind_frame(self, stream_id: str, *, keyframe: bool = False) -> dict:
        """Return {"stream_id", "keyframe", "active", "generation"}; active maps
        kind -> {"ps_id", "version"}."""
        raise NotImplementedError

    def invalidate(self, kind: str, ps_id: int) -> bool:
        """Drop the id; return True when something was removed."""
        raise NotImplementedError

    def generation(self, stream_id: str) -> int:
        """Return the stream generation counter."""
        raise NotImplementedError

    def entries(self, kind: str) -> list[dict]:
        """Return [{"ps_id", "version", "size"}] in ascending id order."""
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
