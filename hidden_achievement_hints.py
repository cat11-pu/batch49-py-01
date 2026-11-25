"""Hidden achievement hint gating shared across the characters of one account."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AchievementError(ValueError):
    """Raised when a hidden achievement operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AchievementView:
    achievement_id: str
    name: str
    total_hints: int
    revealed_hints: tuple
    achieved: bool
    reward_id: str


class HiddenAchievementHints:
    """Staged clue reveals, account wide progress and delayed reward reissue."""

    def __init__(self, *, max_achievements: int = 64,
                 max_pending_rewards: int = 8) -> None:
        self.max_achievements = max_achievements
        self.max_pending_rewards = max_pending_rewards
        self._lock = threading.RLock()

    def register_hidden(self, achievement_id: str, name: str, hints,
                        *, reward_id: str = "",
                        reward_delay_seconds: int = 0) -> None:
        raise NotImplementedError

    def reveal_hint(self, account_id: str, achievement_id: str, stage: int,
                    *, role_id: str = "main") -> dict:
        raise NotImplementedError

    def progress_of(self, account_id: str, achievement_id: str) -> int:
        raise NotImplementedError

    def unlock(self, account_id: str, achievement_id: str,
               *, role_id: str = "main") -> dict:
        raise NotImplementedError

    def visible_for(self, account_id: str) -> list:
        raise NotImplementedError

    def reissue_reward(self, account_id: str, achievement_id: str) -> dict:
        raise NotImplementedError

    def pending_rewards(self, account_id: str) -> list:
        raise NotImplementedError

    def total_hints(self, achievement_id: str) -> int:
        raise NotImplementedError

    def is_achieved(self, account_id: str, achievement_id: str) -> bool:
        raise NotImplementedError

    def stats(self, account_id: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
