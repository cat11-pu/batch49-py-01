"""Party pity isolator: per-member pity counters with team drop bonus."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PartyPityError(ValueError):
    """Raised when a party pity operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class MemberState:
    party_id: str
    member_id: str
    order: int
    pity: int
    kills: int
    offline: bool


@dataclass(frozen=True)
class MemberDrop:
    member_id: str
    rarity: str | None
    pity_before: int
    pity_after: int


class PartyPityIsolator:
    """Settle party kills against independent per-member pity counters."""

    def __init__(self, *, pity_threshold: int = 20, base_rate: int = 200,
                 per_member_bonus: int = 50, max_rate: int = 500,
                 max_counter: int = 999, clock=None) -> None:
        self.pity_threshold = pity_threshold
        self.base_rate = base_rate
        self.per_member_bonus = per_member_bonus
        self.max_rate = max_rate
        self.max_counter = max_counter
        self._clock = clock
        self._lock = threading.RLock()

    def register_member(self, party_id: str, member_id: str) -> None:
        raise NotImplementedError

    def set_offline(self, party_id: str, member_id: str, offline: bool = True) -> None:
        raise NotImplementedError

    def reset_member(self, party_id: str, member_id: str) -> int:
        raise NotImplementedError

    def record_kill(self, kill_id: str, party_id: str, *, rolls=None,
                    now: int | None = None) -> list[MemberDrop]:
        raise NotImplementedError

    def roll_for(self, kill_id: str, member_id: str) -> MemberDrop | None:
        raise NotImplementedError

    def bonus_rate(self, party_id: str) -> int:
        raise NotImplementedError

    def member_state(self, party_id: str, member_id: str) -> MemberState:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
