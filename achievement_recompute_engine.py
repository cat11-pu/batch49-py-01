"""Achievement rule versions, batch recompute with resumable checkpoints."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AchievementError(ValueError):
    """Raised when an achievement operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: int
    metric: str
    threshold: int
    reward: str
    window_seconds: int | None


@dataclass(frozen=True)
class GrantRecord:
    player: str
    rule_id: str
    version: int
    reward: str


@dataclass(frozen=True)
class RecomputeReport:
    processed: int
    granted: int
    remaining: int
    batches: int


class AchievementRecomputeEngine:
    """Evaluate versioned achievement rules over player event history."""

    def __init__(self, *, batch_size: int = 100) -> None:
        self.batch_size = batch_size
        self._lock = threading.RLock()

    def register_rule(self, rule_id: str, *, version: int, metric: str,
                      threshold: int, reward: str,
                      window_seconds: int | None = None) -> Rule:
        raise NotImplementedError

    def record_event(self, *, player: str, metric: str, amount: int,
                     occurred_at: str) -> None:
        raise NotImplementedError

    def progress(self, *, player: str, rule_id: str) -> int:
        raise NotImplementedError

    def evaluate(self, *, player: str) -> list:
        raise NotImplementedError

    def grant(self, *, player: str, rule_ids) -> list:
        raise NotImplementedError

    def recompute(self, *, players, max_batches: int | None = None
                  ) -> RecomputeReport:
        raise NotImplementedError

    def achievements_of(self, player: str) -> dict:
        raise NotImplementedError

    def pending_players(self) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
