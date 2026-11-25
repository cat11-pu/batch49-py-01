"""Boss fight contribution settlement: windows, overkill trimming, reward eligibility."""

from __future__ import annotations

import threading


class CombatSettlementError(ValueError):
    """Raised when a settlement operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CombatContributionSettler:
    """Aggregate per-player damage windows into account-level contribution results."""

    def __init__(self, *, min_damage: int = 1, gold_share: float = 0.5,
                 silver_share: float = 0.25) -> None:
        self.min_damage = min_damage
        self.gold_share = gold_share
        self.silver_share = silver_share
        self._lock = threading.RLock()

    def open_window(self, battle_id: str, boss_hp: int,
                    window_label: str = "w1") -> str:
        raise NotImplementedError

    def window_state(self, battle_id: str) -> str:
        raise NotImplementedError

    def record_damage(self, battle_id: str, player_id: str, account_id: str,
                      damage: int) -> bool:
        raise NotImplementedError

    def report_healing(self, battle_id: str, player_id: str, account_id: str,
                       amount: int) -> None:
        raise NotImplementedError

    def report_taken(self, battle_id: str, player_id: str, account_id: str,
                     amount: int) -> None:
        raise NotImplementedError

    def close_window(self, battle_id: str) -> dict:
        raise NotImplementedError

    def settle(self, battle_id: str) -> dict:
        raise NotImplementedError

    def eligibility(self, battle_id: str) -> dict:
        raise NotImplementedError

    def ledger(self, battle_id: str) -> list:
        raise NotImplementedError

    def remaining_hp(self, battle_id: str) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
