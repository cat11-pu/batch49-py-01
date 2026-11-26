"""Hidden achievement hint gating shared across the characters of one account."""

from __future__ import annotations

import threading
from collections.abc import Sequence
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


ACH_INVALID = "ACH_INVALID"
ACH_DUPLICATE = "ACH_DUPLICATE"
ACH_CAPACITY = "ACH_CAPACITY"
ACH_BAD_STAGE = "ACH_BAD_STAGE"
ACH_UNKNOWN = "ACH_UNKNOWN"
ACH_SNAPSHOT_INVALID = "ACH_SNAPSHOT_INVALID"
ACH_PENDING_LIMIT = "ACH_PENDING_LIMIT"

_STAT_KEYS = (
    "achieved",
    "hints_revealed",
    "rewards_granted",
    "pending_rewards",
    "skipped",
)


def _is_real_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class HiddenAchievementHints:
    """Staged clue reveals, account wide progress and delayed reward reissue."""

    def __init__(self, *, max_achievements: int = 64,
                 max_pending_rewards: int = 8) -> None:
        self.max_achievements = max_achievements
        self.max_pending_rewards = max_pending_rewards
        self._lock = threading.RLock()
        self._achievements: dict[str, dict] = {}
        self._accounts: dict[str, dict] = {}

    def register_hidden(self, achievement_id: str, name: str, hints,
                        *, reward_id: str = "",
                        reward_delay_seconds: int = 0) -> None:
        if not _valid_text(achievement_id):
            raise AchievementError(ACH_INVALID, "achievement_id is required")
        if not _valid_text(name):
            raise AchievementError(ACH_INVALID, "name is required")
        if (not isinstance(hints, Sequence)
                or isinstance(hints, (str, bytes, bytearray))
                or len(hints) == 0):
            raise AchievementError(ACH_INVALID, "hints must be a non-empty sequence")

        normalized_hints = []
        for hint in hints:
            if not _valid_text(hint):
                raise AchievementError(ACH_INVALID, "hints must not contain blank values")
            normalized_hints.append(hint)

        if not isinstance(reward_id, str):
            raise AchievementError(ACH_INVALID, "reward_id must be a string")
        if not _is_real_int(reward_delay_seconds) or reward_delay_seconds < 0:
            raise AchievementError(ACH_INVALID, "reward_delay_seconds must be non-negative")

        with self._lock:
            if achievement_id in self._achievements:
                raise AchievementError(ACH_DUPLICATE, achievement_id)
            if len(self._achievements) >= self.max_achievements:
                raise AchievementError(ACH_CAPACITY, self.max_achievements)

            self._achievements[achievement_id] = {
                "achievement_id": achievement_id,
                "name": name,
                "hints": tuple(normalized_hints),
                "reward_id": reward_id,
                "reward_delay_seconds": reward_delay_seconds,
            }

    def reveal_hint(self, account_id: str, achievement_id: str, stage: int,
                    *, role_id: str = "main") -> dict:
        del role_id
        with self._lock:
            self._require_account_id(account_id)
            achievement = self._require_achievement(achievement_id)
            if not _is_real_int(stage):
                raise AchievementError(ACH_BAD_STAGE, stage)

            total_hints = len(achievement["hints"])
            if stage < 0 or stage >= total_hints:
                raise AchievementError(ACH_BAD_STAGE, stage)

            account = self._get_or_create_account(account_id)
            progress = account["progress"].get(achievement_id, 0)

            if stage < progress:
                return {
                    "revealed": False,
                    "stage": stage,
                    "progress": progress,
                    "reason": "already",
                }

            if stage > progress:
                account["skipped"] += 1
                return {
                    "revealed": False,
                    "stage": stage,
                    "progress": progress,
                    "reason": "skipped",
                }

            account["progress"][achievement_id] = progress + 1
            account["hints_revealed"] += 1
            return {
                "revealed": True,
                "stage": stage,
                "progress": progress + 1,
            }

    def progress_of(self, account_id: str, achievement_id: str) -> int:
        with self._lock:
            self._require_achievement(achievement_id)
            account = self._accounts.get(account_id)
            if account is None:
                return 0
            return int(account["progress"].get(achievement_id, 0))

    def unlock(self, account_id: str, achievement_id: str,
               *, role_id: str = "main") -> dict:
        del role_id
        with self._lock:
            self._require_account_id(account_id)
            achievement = self._require_achievement(achievement_id)
            reward_id = achievement["reward_id"]
            account = self._accounts.get(account_id)

            if account is not None and account["achieved"].get(achievement_id):
                return {
                    "unlocked": True,
                    "reward_id": reward_id,
                    "reward_state": "already_unlocked",
                }

            progress = 0 if account is None else account["progress"].get(achievement_id, 0)
            if progress < len(achievement["hints"]):
                return {"unlocked": False, "reward_id": ""}

            delay = achievement["reward_delay_seconds"]
            if delay > 0:
                pending_count = 0 if account is None else self._pending_count(account)
                if pending_count >= self.max_pending_rewards:
                    raise AchievementError(
                        ACH_PENDING_LIMIT,
                        {
                            "pending_rewards": pending_count,
                            "max_pending_rewards": self.max_pending_rewards,
                        },
                    )

            account = self._get_or_create_account(account_id)
            account["achieved"][achievement_id] = True

            if delay > 0:
                account["rewards"][achievement_id] = "pending"
                reward_state = "pending"
            else:
                account["rewards"][achievement_id] = "granted"
                account["rewards_granted"] += 1
                reward_state = "granted"

            return {
                "unlocked": True,
                "reward_id": reward_id,
                "reward_state": reward_state,
            }

    def visible_for(self, account_id: str) -> list:
        with self._lock:
            account = self._accounts.get(account_id)
            views = []
            for achievement_id in sorted(self._achievements):
                achievement = self._achievements[achievement_id]
                progress = 0 if account is None else account["progress"].get(achievement_id, 0)
                achieved = (
                    account is not None
                    and bool(account["achieved"].get(achievement_id))
                )
                if progress <= 0 and not achieved:
                    continue

                views.append(AchievementView(
                    achievement_id=achievement_id,
                    name=achievement["name"],
                    total_hints=len(achievement["hints"]),
                    revealed_hints=tuple(achievement["hints"][:progress]),
                    achieved=achieved,
                    reward_id=achievement["reward_id"],
                ))
            return views

    def reissue_reward(self, account_id: str, achievement_id: str) -> dict:
        with self._lock:
            self._require_account_id(account_id)
            achievement = self._require_achievement(achievement_id)
            account = self._accounts.get(account_id)
            reward_state = None if account is None else account["rewards"].get(achievement_id)

            if reward_state != "pending":
                return {"reissued": False, "reason": "nothing_pending"}

            account["rewards"][achievement_id] = "granted"
            account["rewards_granted"] += 1
            return {
                "reissued": True,
                "reward_id": achievement["reward_id"],
                "reward_state": "granted",
            }

    def pending_rewards(self, account_id: str) -> list:
        with self._lock:
            account = self._accounts.get(account_id)
            if account is None:
                return []
            return sorted(
                achievement_id
                for achievement_id, state in account["rewards"].items()
                if state == "pending"
            )

    def total_hints(self, achievement_id: str) -> int:
        with self._lock:
            achievement = self._require_achievement(achievement_id)
            return len(achievement["hints"])

    def is_achieved(self, account_id: str, achievement_id: str) -> bool:
        with self._lock:
            self._require_achievement(achievement_id)
            account = self._accounts.get(account_id)
            return account is not None and bool(account["achieved"].get(achievement_id))

    def stats(self, account_id: str) -> dict:
        with self._lock:
            account = self._accounts.get(account_id)
            if account is None:
                return {key: 0 for key in _STAT_KEYS}
            return self._stats_locked(account)

    def snapshot(self) -> dict:
        with self._lock:
            achievements = {}
            for achievement_id in sorted(self._achievements):
                achievement = self._achievements[achievement_id]
                achievements[achievement_id] = {
                    "achievement_id": achievement_id,
                    "name": achievement["name"],
                    "hints": list(achievement["hints"]),
                    "reward_id": achievement["reward_id"],
                    "reward_delay_seconds": achievement["reward_delay_seconds"],
                }

            accounts = {}
            for account_id in sorted(self._accounts):
                account = self._accounts[account_id]
                accounts[account_id] = {
                    "progress": {
                        achievement_id: account["progress"][achievement_id]
                        for achievement_id in sorted(account["progress"])
                    },
                    "achieved": sorted(account["achieved"]),
                    "rewards": {
                        achievement_id: account["rewards"][achievement_id]
                        for achievement_id in sorted(account["rewards"])
                    },
                    "stats": self._stats_locked(account),
                }

            return {"achievements": achievements, "accounts": accounts}

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                achievements, accounts = self._parse_snapshot(state)
            except AchievementError:
                raise
            except Exception as exc:
                raise AchievementError(
                    ACH_SNAPSHOT_INVALID,
                    "snapshot contains invalid data",
                ) from exc
            self._achievements = achievements
            self._accounts = accounts

    def _require_account_id(self, account_id: str) -> None:
        if not _valid_text(account_id):
            raise AchievementError(ACH_INVALID, "account_id is required")

    def _require_achievement(self, achievement_id: str) -> dict:
        if not isinstance(achievement_id, str):
            raise AchievementError(ACH_UNKNOWN, achievement_id)
        achievement = self._achievements.get(achievement_id)
        if achievement is None:
            raise AchievementError(ACH_UNKNOWN, achievement_id)
        return achievement

    def _get_or_create_account(self, account_id: str) -> dict:
        account = self._accounts.get(account_id)
        if account is None:
            account = {
                "progress": {},
                "achieved": {},
                "rewards": {},
                "hints_revealed": 0,
                "rewards_granted": 0,
                "skipped": 0,
            }
            self._accounts[account_id] = account
        return account

    @staticmethod
    def _pending_count(account: dict) -> int:
        return sum(state == "pending" for state in account["rewards"].values())

    def _stats_locked(self, account: dict) -> dict:
        return {
            "achieved": len(account["achieved"]),
            "hints_revealed": account["hints_revealed"],
            "rewards_granted": account["rewards_granted"],
            "pending_rewards": self._pending_count(account),
            "skipped": account["skipped"],
        }

    def _parse_snapshot(self, state: object) -> tuple[dict[str, dict], dict[str, dict]]:
        if not isinstance(state, dict):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "snapshot must be a dict")
        if "achievements" not in state or "accounts" not in state:
            raise AchievementError(ACH_SNAPSHOT_INVALID, "snapshot top-level keys are missing")

        raw_achievements = state["achievements"]
        raw_accounts = state["accounts"]
        if not isinstance(raw_achievements, dict) or not isinstance(raw_accounts, dict):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "snapshot sections have wrong types")

        achievements = self._parse_achievements(raw_achievements)
        accounts = self._parse_accounts(raw_accounts, achievements)
        return achievements, accounts

    def _parse_achievements(self, raw_achievements: dict) -> dict[str, dict]:
        achievements = {}
        required = {
            "achievement_id",
            "name",
            "hints",
            "reward_id",
            "reward_delay_seconds",
        }

        for achievement_id, raw in raw_achievements.items():
            if not _valid_text(achievement_id) or not isinstance(raw, dict):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid achievement record")
            if not required.issubset(raw):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "achievement keys are missing")
            if raw["achievement_id"] != achievement_id:
                raise AchievementError(ACH_SNAPSHOT_INVALID, "achievement id mismatch")
            if not _valid_text(raw["name"]):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid achievement name")
            if not isinstance(raw["hints"], list) or not raw["hints"]:
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid hints")
            if any(not _valid_text(hint) for hint in raw["hints"]):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid hint")
            if not isinstance(raw["reward_id"], str):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid reward_id")
            delay = raw["reward_delay_seconds"]
            if not _is_real_int(delay) or delay < 0:
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid reward delay")

            achievements[achievement_id] = {
                "achievement_id": achievement_id,
                "name": raw["name"],
                "hints": tuple(raw["hints"]),
                "reward_id": raw["reward_id"],
                "reward_delay_seconds": delay,
            }

        return achievements

    def _parse_accounts(
        self,
        raw_accounts: dict,
        achievements: dict[str, dict],
    ) -> dict[str, dict]:
        accounts = {}
        required = {"progress", "achieved", "rewards", "stats"}

        for account_id, raw_account in raw_accounts.items():
            if not _valid_text(account_id) or not isinstance(raw_account, dict):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid account record")
            if not required.issubset(raw_account):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "account keys are missing")

            progress = self._parse_progress(raw_account["progress"], achievements)
            achieved = self._parse_achieved(raw_account["achieved"], achievements)
            rewards = self._parse_rewards(raw_account["rewards"], achieved)
            counters = self._parse_stats(raw_account["stats"], progress, achieved, rewards)

            accounts[account_id] = {
                "progress": progress,
                "achieved": {achievement_id: True for achievement_id in achieved},
                "rewards": rewards,
                "hints_revealed": counters["hints_revealed"],
                "rewards_granted": counters["rewards_granted"],
                "skipped": counters["skipped"],
            }

        return accounts

    def _parse_progress(self, raw_progress: object, achievements: dict[str, dict]) -> dict:
        if not isinstance(raw_progress, dict):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "progress must be a dict")

        progress = {}
        for achievement_id, value in raw_progress.items():
            if achievement_id not in achievements:
                raise AchievementError(ACH_SNAPSHOT_INVALID, "unknown progress achievement")
            total = len(achievements[achievement_id]["hints"])
            if not _is_real_int(value) or value < 0 or value > total:
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid progress value")
            if value:
                progress[achievement_id] = value
        return progress

    def _parse_achieved(self, raw_achieved: object, achievements: dict[str, dict]) -> set[str]:
        if not isinstance(raw_achieved, list):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "achieved must be a list")

        achieved = set()
        for achievement_id in raw_achieved:
            if (not isinstance(achievement_id, str)
                    or achievement_id not in achievements
                    or achievement_id in achieved):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid achieved achievement")
            achieved.add(achievement_id)
        return achieved

    def _parse_rewards(self, raw_rewards: object, achieved: set[str]) -> dict[str, str]:
        if not isinstance(raw_rewards, dict):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "rewards must be a dict")

        rewards = {}
        for achievement_id, state in raw_rewards.items():
            if achievement_id not in achieved or state not in ("pending", "granted"):
                raise AchievementError(ACH_SNAPSHOT_INVALID, "invalid reward state")
            rewards[achievement_id] = state

        if set(rewards) != achieved:
            raise AchievementError(ACH_SNAPSHOT_INVALID, "achieved reward states are missing")
        return rewards

    def _parse_stats(
        self,
        raw_stats: object,
        progress: dict[str, int],
        achieved: set[str],
        rewards: dict[str, str],
    ) -> dict[str, int]:
        if not isinstance(raw_stats, dict) or any(key not in raw_stats for key in _STAT_KEYS):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "stats are incomplete")
        if any(not _is_real_int(raw_stats[key]) or raw_stats[key] < 0 for key in _STAT_KEYS):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "stats must be non-negative integers")

        pending_count = sum(state == "pending" for state in rewards.values())
        granted_count = sum(state == "granted" for state in rewards.values())
        revealed_count = sum(progress.values())

        if raw_stats["achieved"] != len(achieved):
            raise AchievementError(ACH_SNAPSHOT_INVALID, "achieved count mismatch")
        if raw_stats["pending_rewards"] != pending_count:
            raise AchievementError(ACH_SNAPSHOT_INVALID, "pending count mismatch")
        if raw_stats["rewards_granted"] != granted_count:
            raise AchievementError(ACH_SNAPSHOT_INVALID, "granted count mismatch")
        if raw_stats["hints_revealed"] != revealed_count:
            raise AchievementError(ACH_SNAPSHOT_INVALID, "revealed count mismatch")

        return {
            "hints_revealed": revealed_count,
            "rewards_granted": granted_count,
            "skipped": raw_stats["skipped"],
        }
