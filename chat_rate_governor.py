"""Chat rate governor: per channel token buckets, repeat folding and mutes."""

from __future__ import annotations

import threading


class GovernorError(ValueError):
    """Raised when a rate governor operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ChatRateGovernor:
    """Token buckets keyed by player and channel, plus sliding fold windows."""

    def __init__(self, *, capacity: float = 5.0,
                 refill_per_second: float = 1.0,
                 fold_window_seconds: float = 30.0,
                 max_tracked: int = 256) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.fold_window_seconds = fold_window_seconds
        self.max_tracked = max_tracked
        self._lock = threading.RLock()

    def allow(self, player_id: str, channel: str, *, at: float,
              is_admin: bool = False) -> dict:
        raise NotImplementedError

    def record(self, player_id: str, channel: str, body: str, *,
               at: float) -> dict:
        raise NotImplementedError

    def fold_count(self, channel: str, player_id: str) -> int:
        raise NotImplementedError

    def mute(self, player_id: str, *, until: float, reason: str = "") -> dict:
        raise NotImplementedError

    def unmute(self, player_id: str) -> bool:
        raise NotImplementedError

    def is_muted(self, player_id: str, *, at: float) -> bool:
        raise NotImplementedError

    def state(self, player_id: str, channel: str) -> dict:
        raise NotImplementedError

    def set_capacity(self, value: float) -> None:
        raise NotImplementedError

    def set_refill_rate(self, value: float) -> None:
        raise NotImplementedError

    def reset_player(self, player_id: str) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
