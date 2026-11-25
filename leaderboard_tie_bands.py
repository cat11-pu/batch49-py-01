"""Season leaderboard: dense ranks, time tie-break, ratio bands and settlement."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class LeaderboardError(ValueError):
    """Raised when a leaderboard operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Entry:
    player: str
    score: int
    achieved_at: str
    seq: int


@dataclass(frozen=True)
class Band:
    name: str
    min_rank: int
    max_rank: int
    reward: str


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    improved: bool
    best_score: int
    rank: int


@dataclass(frozen=True)
class Settlement:
    settlement_id: int
    season: str
    created_at: str
    entries: tuple
    bands: tuple


class LeaderboardTieBands:
    """Rank players per season with dense ranks and cumulative reward bands."""

    def __init__(self, *, bands=None, clock=None) -> None:
        self._bands = bands
        self._clock = clock
        self._lock = threading.RLock()

    def submit(self, *, season: str, player: str, score: int,
               achieved_at: str) -> SubmitResult:
        raise NotImplementedError

    def rank_of(self, *, season: str, player: str) -> int:
        raise NotImplementedError

    def top(self, *, season: str, limit: int) -> list:
        raise NotImplementedError

    def bands(self, *, season: str) -> list:
        raise NotImplementedError

    def recompute_bands(self, *, season: str, ratios) -> list:
        raise NotImplementedError

    def settle(self, *, season: str, now: str | None = None) -> Settlement:
        raise NotImplementedError

    def settlement(self, settlement_id: int) -> Settlement:
        raise NotImplementedError

    def rewards(self, settlement_id: int) -> dict:
        raise NotImplementedError

    def score_of(self, *, season: str, player: str) -> int | None:
        raise NotImplementedError

    def count(self, season: str) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
