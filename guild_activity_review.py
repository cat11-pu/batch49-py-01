"""Guild weekly activity review: thresholds, probation, demotion and appeals."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ReviewError(ValueError):
    """Raised when an activity review operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ReviewRecord:
    week: str
    member_id: str
    total: int
    status: str
    missed: int


class GuildActivityReview:
    """Weekly contribution thresholds with observation windows and appeals."""

    def __init__(self, *, weekly_threshold: int = 100, grace_weeks: int = 1,
                 tz_offset_hours: int = 8, max_members: int = 200,
                 max_rank: int = 4) -> None:
        self.weekly_threshold = weekly_threshold
        self.grace_weeks = grace_weeks
        self.tz_offset_hours = tz_offset_hours
        self.max_members = max_members
        self.max_rank = max_rank
        self._lock = threading.RLock()

    def join_member(self, member_id: str, rank_level: int, *, week: str) -> dict:
        raise NotImplementedError

    def record_contribution(self, member_id: str, amount: int,
                            *, occurred_at: str) -> dict:
        raise NotImplementedError

    def weekly_review(self, week: str) -> dict:
        raise NotImplementedError

    def status(self, member_id: str) -> str:
        raise NotImplementedError

    def observe(self, member_id: str) -> dict:
        raise NotImplementedError

    def appeal(self, member_id: str, *, at: str) -> dict:
        raise NotImplementedError

    def restore_rank(self, member_id: str) -> dict:
        raise NotImplementedError

    def rank_of(self, member_id: str) -> int:
        raise NotImplementedError

    def week_of(self, occurred_at: str) -> str:
        raise NotImplementedError

    def history(self, member_id: str) -> list:
        raise NotImplementedError

    def set_threshold(self, value: int) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
