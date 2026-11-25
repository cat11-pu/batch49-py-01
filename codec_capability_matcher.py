"""Codec capability matcher: profile/level intersection with fallback chains."""

from __future__ import annotations

import threading

LEVEL_LIMITS = {
    40: (1920, 1080, 30),
    41: (1920, 1080, 60),
    51: (3840, 2160, 30),
    52: (3840, 2160, 60),
}

PROFILE_ORDER = ("baseline", "main", "high")
CODECS = ("h264", "hevc")
PACKAGINGS = ("annexb", "avcc")

CAP_KEYS = ("codec", "profile", "level", "width", "height", "fps", "packaging")

# Condition -> error code carried by CodecError.code.
ERROR_CODES = {
    "argument": "CODEC_ARGUMENT_INVALID",    # empty/non-text endpoint or session id
    "cap_invalid": "CODEC_CAP_INVALID",      # capability entry malformed
    "capacity": "CODEC_CAPACITY_EXCEEDED",   # too many caps or sessions
    "endpoint_missing": "CODEC_ENDPOINT_MISSING",
    "session_missing": "CODEC_SESSION_MISSING",
    "snapshot_invalid": "CODEC_SNAPSHOT_INVALID",
}

# Rejection reasons reported by match()/explain().
REASON_CODES = ("CODEC_MISMATCH", "PACKAGING_MISMATCH", "PROFILE_MISMATCH",
                "LEVEL_TOO_LOW")

STATS_KEYS = ("endpoints", "registrations", "matches", "rejections",
              "negotiations", "cache_hits")


class CodecError(ValueError):
    """Raised when a codec negotiation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CodecCapabilityMatcher:
    """Match sender and receiver codec capabilities with ordered fallbacks."""

    def __init__(self, *, max_caps_per_endpoint: int = 8, max_chain: int = 6,
                 max_sessions: int = 16) -> None:
        self.max_caps_per_endpoint = max_caps_per_endpoint
        self.max_chain = max_chain
        self.max_sessions = max_sessions
        self._lock = threading.RLock()

    def register_endpoint(self, endpoint_id: str, caps) -> dict:
        """Return {"endpoint_id", "caps", "revision"}."""
        raise NotImplementedError

    def match(self, sender: str, receiver: str) -> dict:
        """Return {"status", "selected", "reasons"}; selected carries
        "profile", "level" and "required_level" and is None when rejected."""
        raise NotImplementedError

    def fallback_chain(self, sender: str, receiver: str) -> list[dict]:
        """Return [{"profile", "level", "usable"}] in priority order."""
        raise NotImplementedError

    def negotiate(self, session_id: str, sender: str, receiver: str) -> dict:
        """Return {"status", "selected", "reasons", "cached", "generation"}."""
        raise NotImplementedError

    def session_result(self, session_id: str) -> dict:
        """Return the pinned result for the session (same shape as negotiate)."""
        raise NotImplementedError

    def mark_keyframe(self, session_id: str) -> dict:
        """Apply any pending recomputation; return the session result."""
        raise NotImplementedError

    def explain(self, sender: str, receiver: str) -> dict:
        """Return {"required_level", "receiver_max_level", "reasons"}."""
        raise NotImplementedError

    def endpoints(self) -> list[str]:
        """Return endpoint ids in ascending order."""
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
