"""Match avoid registry: bidirectional avoid edges with expiry and wait-based relaxing."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class MatchAvoidError(ValueError):
    """Raised when a match-avoid operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AvoidEdge:
    owner: str
    target: str
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class MatchTicket:
    ticket_id: str
    player: str
    enqueued_at: int


class MatchAvoidRegistry:
    """Track avoid relations between players and filter match candidates."""

    def __init__(self, *, max_avoid_per_player: int = 32, default_ttl: int = 1800,
                 relax_thresholds=(60, 180, 300), clock=None) -> None:
        self.max_avoid_per_player = max_avoid_per_player
        self.default_ttl = default_ttl
        self.relax_thresholds = tuple(relax_thresholds)
        self._clock = clock
        self._lock = threading.RLock()

    def add_avoid(self, owner: str, target: str, *, ttl: int | None = None,
                  now: int | None = None) -> AvoidEdge:
        raise NotImplementedError

    def remove_avoid(self, owner: str, target: str, *, now: int | None = None) -> bool:
        raise NotImplementedError

    def has_avoid(self, left: str, right: str) -> bool:
        raise NotImplementedError

    def avoid_pairs(self, player: str | None = None) -> list[tuple[str, str]]:
        raise NotImplementedError

    def filter_candidates(self, ticket: MatchTicket, candidates,
                          *, now: int | None = None) -> list[MatchTicket]:
        raise NotImplementedError

    def relax_level(self, wait_seconds: int) -> int:
        raise NotImplementedError

    def expire(self, now: int | None = None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
