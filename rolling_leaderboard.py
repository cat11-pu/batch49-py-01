"""Rolling daily/weekly leaderboards with fixed whole-hour windows."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class RollingError(ValueError):
    """Raised when a rolling leaderboard operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ScoreEntry:
    player: str
    score: int
    achieved_at: str
    board: str
    window: str
    seq: int


@dataclass(frozen=True)
class SubmitOutcome:
    window: str
    improved: bool
    best: int


@dataclass(frozen=True)
class WindowResult:
    board: str
    window: str
    winner: str | None
    best_score: int | None
    entries: int


class RollingLeaderboard:
    """Keep per-window best scores for day and week boards."""

    def __init__(self, *, timezone_name: str = "UTC", max_entries: int = 1000,
                 clock=None) -> None:
        self.timezone_name = timezone_name
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.RLock()

    def submit(self, *, player: str, score: int,
               achieved_at: str) -> SubmitOutcome:
        raise NotImplementedError

    def best_of(self, *, player: str, board: str = "day",
                window: str) -> int | None:
        raise NotImplementedError

    def top(self, *, board: str = "day", window: str,
            limit: int = 10) -> list:
        raise NotImplementedError

    def roll_window(self, *, board: str = "day", now: str) -> list:
        raise NotImplementedError

    def purge_expired(self, *, now: str) -> int:
        raise NotImplementedError

    def window_of(self, *, achieved_at: str, board: str = "day") -> str:
        raise NotImplementedError

    def history_best(self, player: str) -> int | None:
        raise NotImplementedError

    def scores_of(self, player: str) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
