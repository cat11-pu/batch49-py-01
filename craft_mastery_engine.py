"""Crafting mastery: batch exp settlement, crit bonus output, daily exp cap."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class CraftError(ValueError):
    """Raised when a crafting operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CraftBatchResult:
    crafted: int
    failed: int
    exp_gained: int
    bonuses: dict
    mailed: int
    capped: bool


class CraftMasteryEngine:
    """Track recipes, materials, mastery exp, backpack and overflow mail."""

    def __init__(self, *, daily_exp_cap: int = 1000,
                 backpack_capacity: int = 20, clock=None) -> None:
        self.daily_exp_cap = daily_exp_cap
        self.backpack_capacity = backpack_capacity
        self._clock = clock
        self._lock = threading.RLock()

    def register_recipe(self, recipe_id: str, *, materials: dict,
                        base_exp: int, bonus_item: str | None = None) -> None:
        raise NotImplementedError

    def add_item(self, player: str, item: str, count: int) -> None:
        raise NotImplementedError

    def inventory_of(self, player: str) -> dict:
        raise NotImplementedError

    def craft_batch(self, *, player: str, recipe_id: str, count: int,
                    now: str | None = None, double_exp: bool = False,
                    crit_rolls=None) -> CraftBatchResult:
        raise NotImplementedError

    def grant_exp(self, *, player: str, amount: int,
                  now: str | None = None) -> int:
        raise NotImplementedError

    def level_of(self, *, player: str, now: str | None = None) -> int:
        raise NotImplementedError

    def exp_of(self, player: str) -> int:
        raise NotImplementedError

    def crit_rate(self, *, player: str, now: str | None = None) -> float:
        raise NotImplementedError

    def bonus_output(self, player: str) -> dict:
        raise NotImplementedError

    def mail_of(self, player: str) -> list:
        raise NotImplementedError

    def claim_mail(self, *, player: str, mail_id: int) -> dict:
        raise NotImplementedError

    def daily_exp_used(self, *, player: str, now: str | None = None) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
