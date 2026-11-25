"""Player trade negotiation: versioned offers, turn rotation, atomic swap."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class TradeError(ValueError):
    """Raised when a trade operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OfferVersion:
    version: int
    proposer: str
    give: tuple
    want: tuple
    created_at: str
    expires_at: str


@dataclass(frozen=True)
class TradeResult:
    status: str
    version: int


class TradeOfferNegotiation:
    """Run two-party offer/counter sessions with atomic inventory exchange."""

    def __init__(self, *, offer_ttl: int = 300, max_versions: int = 20,
                 clock=None) -> None:
        self.offer_ttl = offer_ttl
        self.max_versions = max_versions
        self._clock = clock
        self._lock = threading.RLock()

    def open_session(self, *, session_id: str, player_a: str, player_b: str,
                     now: str) -> None:
        raise NotImplementedError

    def set_inventory(self, player: str, items: dict) -> None:
        raise NotImplementedError

    def inventory_of(self, player: str) -> dict:
        raise NotImplementedError

    def create_offer(self, *, session_id: str, proposer: str, give: dict,
                     want: dict, now: str) -> OfferVersion:
        raise NotImplementedError

    def counter_offer(self, *, session_id: str, proposer: str, give: dict,
                      want: dict, now: str) -> OfferVersion:
        raise NotImplementedError

    def accept(self, *, session_id: str, player: str, version: int,
               now: str) -> TradeResult:
        raise NotImplementedError

    def reject(self, *, session_id: str, player: str, version: int | None = None,
               now: str | None = None) -> None:
        raise NotImplementedError

    def cancel(self, *, session_id: str, player: str,
               now: str | None = None) -> None:
        raise NotImplementedError

    def state(self, *, session_id: str, now: str | None = None) -> str:
        raise NotImplementedError

    def versions_of(self, session_id: str) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
