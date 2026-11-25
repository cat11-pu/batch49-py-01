"""Layered lockout ladder across account, source and device dimensions."""

from __future__ import annotations

import threading
from dataclasses import dataclass

DIMENSIONS = ("account", "source", "device")

DEFAULT_LADDERS = {
    "account": ((3, 2), (5, 6), (8, 12)),
    "source": ((6, 4), (10, 10)),
    "device": ((5, 3), (9, 9)),
}


class LockoutError(ValueError):
    """Raised when a lockout operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Counts:
    """Current failure counts for the three dimensions."""

    account: int
    source: int
    device: int


@dataclass(frozen=True)
class LockStatus:
    """Resolved lock state for one account/source/device triple."""

    locked: bool
    dimension: str | None
    until: int | None
    counts: Counts
    exempt: bool


@dataclass(frozen=True)
class FailureOutcome:
    """Outcome of recording one failure."""

    locked: bool
    dimension: str | None
    until: int | None
    counts: Counts
    tier: int | None


@dataclass(frozen=True)
class Appointment:
    """A scheduled unlock for one account."""

    account: str
    scheduled_at: int


@dataclass(frozen=True)
class Exemption:
    """An administrative exemption for one account."""

    account: str
    reason: str
    expires_at: int
    previous_until: int


class LayeredLockoutLadder:
    """Accumulate failures per dimension and lock each one on its own ladder."""

    def __init__(self, *, ladders=None, max_subjects: int = 64,
                 max_appointments: int = 8, decay_after: int = 20) -> None:
        source = DEFAULT_LADDERS if ladders is None else ladders
        self.ladders = {dimension: tuple(tuple(tier) for tier in tiers)
                        for dimension, tiers in source.items()}
        self.max_subjects = max_subjects
        self.max_appointments = max_appointments
        self.decay_after = decay_after
        self._lock = threading.RLock()
        self._now = 0
        self._index = {dimension: {} for dimension in DIMENSIONS}
        self._locks = {dimension: {} for dimension in DIMENSIONS}
        self._exemptions = {}
        self._appointments = []
        self._counters = {
            "failures": 0,
            "successes": 0,
            "locks": 0,
            "unlocks": 0,
            "exemptions": 0,
            "appointments": 0,
        }

    def record_failure(self, account: str, source: str, device: str, *,
                       now: int | None = None) -> FailureOutcome:
        raise NotImplementedError

    def record_success(self, account: str, source: str, device: str, *,
                       now: int | None = None) -> LockStatus:
        raise NotImplementedError

    def lock_state(self, account: str, source: str, device: str, *,
                   now: int | None = None) -> LockStatus:
        raise NotImplementedError

    def dimension_config(self, dimension: str) -> tuple:
        raise NotImplementedError

    def grant_exemption(self, account: str, *, reason: str, ticks: int,
                        now: int | None = None) -> Exemption:
        raise NotImplementedError

    def schedule_unlock(self, account: str, *, ticks: int,
                        now: int | None = None) -> Appointment:
        raise NotImplementedError

    def tick(self, steps: int = 1) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
