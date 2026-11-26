"""Crafting mastery: batch exp settlement, crit bonus output, daily exp cap."""

from __future__ import annotations

import copy
import random
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone


_SNAPSHOT_KEYS = {
    "version",
    "recipes",
    "players",
    "inventories",
    "daily",
    "stats",
    "mail",
}
_STAT_KEYS = {"crafted", "crits", "exp_granted", "mailed", "claimed"}
_MAX_LEVEL = 20
_DAILY_TOKEN = "double_exp_token"


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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class CraftMasteryEngine:
    """Track recipes, materials, mastery exp, backpack and overflow mail."""

    def __init__(self, *, daily_exp_cap: int = 1000,
                 backpack_capacity: int = 20, clock=None) -> None:
        if not _is_int(daily_exp_cap) or daily_exp_cap < 0:
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid daily_exp_cap")
        if not _is_int(backpack_capacity) or backpack_capacity < 0:
            raise CraftError("CRAFT_INVALID_REQUEST",
                             "invalid backpack_capacity")

        self.daily_exp_cap = daily_exp_cap
        self.backpack_capacity = backpack_capacity
        self._clock = clock

        self._recipes: dict[str, dict] = {}
        self._players: dict[str, int] = {}
        self._inventories: dict[str, dict[str, int]] = {}
        self._daily: dict[str, dict] = {}
        self._mail: dict[str, list[dict]] = {}
        self._stats = {key: 0 for key in _STAT_KEYS}

        self._recipes_lock = threading.RLock()
        self._stats_lock = threading.RLock()
        self._player_locks_lock = threading.RLock()
        self._player_locks: dict[str, threading.RLock] = {}

    # ------------------------------------------------------------------
    # Validation and shared helpers

    def _player_lock(self, player: str) -> threading.RLock:
        with self._player_locks_lock:
            lock = self._player_locks.get(player)
            if lock is None:
                lock = threading.RLock()
                self._player_locks[player] = lock
            return lock

    @staticmethod
    def _recipe_record(materials: dict, base_exp: int,
                       bonus_item: str | None) -> dict:
        if not isinstance(materials, dict) or not materials:
            raise CraftError("CRAFT_INVALID_RECIPE",
                             "materials must be a non-empty dict")
        clean_materials: dict[str, int] = {}
        for item, amount in materials.items():
            if not _nonempty_string(item):
                raise CraftError("CRAFT_INVALID_RECIPE", "invalid material")
            if not _is_int(amount) or amount <= 0:
                raise CraftError("CRAFT_INVALID_RECIPE",
                                 "material amount must be positive")
            clean_materials[item] = amount

        if not _is_int(base_exp) or base_exp <= 0:
            raise CraftError("CRAFT_INVALID_RECIPE",
                             "base_exp must be positive")
        if bonus_item is not None and not _nonempty_string(bonus_item):
            raise CraftError("CRAFT_INVALID_RECIPE", "invalid bonus_item")

        return {
            "materials": clean_materials,
            "base_exp": base_exp,
            "bonus_item": bonus_item,
        }

    @staticmethod
    def _level_for_exp(exp: int) -> int:
        return min(_MAX_LEVEL, max(0, exp) // 100)

    def _resolve_day(self, now) -> str:
        value = now
        if value is None:
            if self._clock is None:
                value = datetime.now(timezone.utc)
            elif callable(self._clock):
                value = self._clock()
            elif hasattr(self._clock, "now"):
                value = self._clock.now()
            else:
                value = self._clock

        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise CraftError("CRAFT_INVALID_REQUEST", "invalid now")
            try:
                return datetime.fromisoformat(
                    text[:-1] + "+00:00" if text.endswith("Z") else text
                ).date().isoformat()
            except ValueError:
                try:
                    return date.fromisoformat(text).isoformat()
                except ValueError as exc:
                    raise CraftError("CRAFT_INVALID_REQUEST",
                                     "invalid now") from exc
        raise CraftError("CRAFT_INVALID_REQUEST", "invalid now")

    @staticmethod
    def _inventory_usage(inventory: dict[str, int]) -> int:
        return sum(inventory.values())

    @staticmethod
    def _remove_if_empty(inventory: dict[str, int], item: str) -> None:
        if inventory.get(item) == 0:
            inventory.pop(item, None)

    def _bump_stats(self, **increments: int) -> None:
        with self._stats_lock:
            for key, amount in increments.items():
                self._stats[key] += amount

    @staticmethod
    def _valid_player(player: object) -> bool:
        return _nonempty_string(player)

    @staticmethod
    def _valid_item(item: object) -> bool:
        return _nonempty_string(item)

    # ------------------------------------------------------------------
    # Recipes and inventory

    def register_recipe(self, recipe_id: str, *, materials: dict,
                        base_exp: int, bonus_item: str | None = None) -> None:
        if not _nonempty_string(recipe_id):
            raise CraftError("CRAFT_INVALID_RECIPE", "invalid recipe_id")
        record = self._recipe_record(materials, base_exp, bonus_item)
        with self._recipes_lock:
            self._recipes[recipe_id] = record

    def add_item(self, player: str, item: str, count: int) -> None:
        if not self._valid_player(player) or not self._valid_item(item):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player or item")
        if not _is_int(count) or count <= 0:
            raise CraftError("CRAFT_INVALID_REQUEST",
                             "count must be positive")

        with self._player_lock(player):
            inventory = self._inventories.setdefault(player, {})
            inventory[item] = inventory.get(item, 0) + count

    def inventory_of(self, player: str) -> dict:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        with self._player_lock(player):
            return copy.deepcopy(self._inventories.get(player, {}))

    # ------------------------------------------------------------------
    # Crafting and mastery

    def craft_batch(self, *, player: str, recipe_id: str, count: int,
                    now: str | None = None, double_exp: bool = False,
                    crit_rolls=None) -> CraftBatchResult:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        if not _nonempty_string(recipe_id):
            raise CraftError("CRAFT_UNKNOWN_RECIPE", recipe_id)
        if not _is_int(count) or count <= 0:
            raise CraftError("CRAFT_INVALID_REQUEST",
                             "count must be positive")
        if not isinstance(double_exp, bool):
            raise CraftError("CRAFT_INVALID_REQUEST",
                             "double_exp must be a bool")

        day = self._resolve_day(now)

        with self._recipes_lock:
            recipe = self._recipes.get(recipe_id)
            if recipe is not None:
                recipe = copy.deepcopy(recipe)
        if recipe is None:
            raise CraftError("CRAFT_UNKNOWN_RECIPE", recipe_id)

        rolls = None
        if crit_rolls is not None:
            if not isinstance(crit_rolls, (list, tuple)):
                raise CraftError("CRAFT_INVALID_ROLLS",
                                 "crit_rolls must be a sequence")
            if len(crit_rolls) != count:
                raise CraftError("CRAFT_INVALID_ROLLS",
                                 "crit_rolls length must equal count")
            rolls = list(crit_rolls)
            for roll in rolls:
                if (isinstance(roll, bool)
                        or not isinstance(roll, (int, float))
                        or not 0.0 <= float(roll) <= 1.0):
                    raise CraftError("CRAFT_INVALID_ROLLS",
                                     "invalid crit roll")

        material_spec = recipe["materials"]
        base_exp = recipe["base_exp"]
        bonus_item = recipe["bonus_item"]

        with self._player_lock(player):
            inventory = dict(self._inventories.get(player, {}))

            craftable = count
            for item, amount in material_spec.items():
                craftable = min(
                    craftable, inventory.get(item, 0) // amount
                )
            successful = craftable
            failed = count - successful

            exp = self._players.get(player, 0)
            daily_record = self._daily.get(player)
            if daily_record is None or daily_record.get("date") != day:
                daily_used = 0
            else:
                daily_used = daily_record.get("used", 0)

            staged_inventory = dict(inventory)
            for item, amount in material_spec.items():
                staged_inventory[item] = (
                    staged_inventory.get(item, 0) - amount * successful
                )
                self._remove_if_empty(staged_inventory, item)

            expires_double = (
                double_exp
                and successful > 0
                and daily_used < self.daily_exp_cap
            )
            if expires_double:
                if staged_inventory.get(_DAILY_TOKEN, 0) < 1:
                    raise CraftError("CRAFT_NO_DOUBLE_TOKEN")
                staged_inventory[_DAILY_TOKEN] -= 1
                self._remove_if_empty(staged_inventory, _DAILY_TOKEN)

            staged_mail = copy.deepcopy(self._mail.get(player, []))
            with self._stats_lock:
                mailed_total = self._stats["mailed"]
            next_mail_id = (
                max(
                    mailed_total,
                    max((entry["id"] for entry in staged_mail), default=0),
                ) + 1
            )

            bonuses: dict[str, int] = {}
            exp_gained = 0
            mailed = 0
            crits = 0
            capped = False

            for index in range(successful):
                level = self._level_for_exp(exp)
                piece_exp = max(1, base_exp // (level + 1))
                if expires_double:
                    piece_exp *= 2

                remaining = max(0, self.daily_exp_cap - daily_used)
                gained = min(piece_exp, remaining)
                if gained < piece_exp:
                    capped = True

                exp += gained
                daily_used += gained
                exp_gained += gained

                level = self._level_for_exp(exp)
                rate = min(0.25, 0.05 + level * 0.01)
                roll = rolls[index] if rolls is not None else random.random()
                if roll >= rate:
                    continue

                crits += 1
                if bonus_item is None:
                    continue

                bonuses[bonus_item] = bonuses.get(bonus_item, 0) + 1
                if (self._inventory_usage(staged_inventory)
                        < self.backpack_capacity):
                    staged_inventory[bonus_item] = (
                        staged_inventory.get(bonus_item, 0) + 1
                    )
                else:
                    staged_mail.append({
                        "id": next_mail_id,
                        "item": bonus_item,
                        "count": 1,
                    })
                    next_mail_id += 1
                    mailed += 1

            # Everything is staged so a refused batch cannot lose materials,
            # tokens, exp, or generated bonus items.
            if staged_inventory:
                self._inventories[player] = staged_inventory
            else:
                self._inventories.pop(player, None)

            if exp:
                self._players[player] = exp
            else:
                self._players.pop(player, None)

            if daily_used or successful:
                self._daily[player] = {"date": day, "used": daily_used}
            elif daily_record is not None and daily_record.get("date") != day:
                self._daily[player] = {"date": day, "used": 0}

            if staged_mail:
                self._mail[player] = staged_mail
            else:
                self._mail.pop(player, None)

            self._bump_stats(
                crafted=successful,
                crits=crits,
                exp_granted=exp_gained,
                mailed=mailed,
            )

            return CraftBatchResult(
                crafted=successful,
                failed=failed,
                exp_gained=exp_gained,
                bonuses=bonuses,
                mailed=mailed,
                capped=capped,
            )

    def grant_exp(self, *, player: str, amount: int,
                  now: str | None = None) -> int:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        if not _is_int(amount) or amount <= 0:
            raise CraftError("CRAFT_INVALID_REQUEST",
                             "amount must be positive")
        day = self._resolve_day(now)

        with self._player_lock(player):
            current_exp = self._players.get(player, 0)
            record = self._daily.get(player)
            if record is None or record.get("date") != day:
                used = 0
            else:
                used = record.get("used", 0)

            gained = min(amount, max(0, self.daily_exp_cap - used))
            if gained:
                self._players[player] = current_exp + gained
            self._daily[player] = {
                "date": day,
                "used": used + gained,
            }
            self._bump_stats(exp_granted=gained)
            return gained

    def level_of(self, *, player: str, now: str | None = None) -> int:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        self._resolve_day(now)
        with self._player_lock(player):
            return self._level_for_exp(self._players.get(player, 0))

    def exp_of(self, player: str) -> int:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        with self._player_lock(player):
            return self._players.get(player, 0)

    def crit_rate(self, *, player: str, now: str | None = None) -> float:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        self._resolve_day(now)
        with self._player_lock(player):
            level = self._level_for_exp(self._players.get(player, 0))
            return min(0.25, 0.05 + level * 0.01)

    def bonus_output(self, player: str) -> dict:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")

        with self._recipes_lock:
            bonus_items = {
                recipe["bonus_item"]
                for recipe in self._recipes.values()
                if recipe["bonus_item"] is not None
            }

        with self._player_lock(player):
            output = {item: 0 for item in bonus_items}
            for item, amount in self._inventories.get(player, {}).items():
                if item in output:
                    output[item] += amount
            for entry in self._mail.get(player, []):
                if entry["item"] in output:
                    output[entry["item"]] += entry["count"]
        return {item: amount for item, amount in output.items() if amount}

    # ------------------------------------------------------------------
    # Overflow mail

    def mail_of(self, player: str) -> list:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        with self._player_lock(player):
            return copy.deepcopy(self._mail.get(player, []))

    def claim_mail(self, *, player: str, mail_id: int) -> dict:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        if not _is_int(mail_id) or mail_id <= 0:
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid mail_id")

        with self._player_lock(player):
            mail = self._mail.get(player, [])
            index = next(
                (i for i, entry in enumerate(mail)
                 if entry["id"] == mail_id),
                None,
            )
            if index is None:
                raise CraftError("CRAFT_MAIL_NOT_FOUND", mail_id)

            entry = mail[index]
            inventory = dict(self._inventories.get(player, {}))
            if (self._inventory_usage(inventory) + entry["count"]
                    > self.backpack_capacity):
                raise CraftError("CRAFT_BACKPACK_FULL", mail_id)

            inventory[entry["item"]] = (
                inventory.get(entry["item"], 0) + entry["count"]
            )
            self._inventories[player] = inventory
            remaining = list(mail)
            del remaining[index]
            if remaining:
                self._mail[player] = remaining
            else:
                self._mail.pop(player, None)

            self._bump_stats(claimed=1)
            return copy.deepcopy(entry)

    def daily_exp_used(self, *, player: str, now: str | None = None) -> int:
        if not self._valid_player(player):
            raise CraftError("CRAFT_INVALID_REQUEST", "invalid player")
        day = self._resolve_day(now)
        with self._player_lock(player):
            record = self._daily.get(player)
            if record is None or record.get("date") != day:
                if record is not None:
                    self._daily[player] = {"date": day, "used": 0}
                return 0
            return record.get("used", 0)

    # ------------------------------------------------------------------
    # Statistics and persistence

    def stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    def snapshot(self) -> dict:
        with self._player_locks_lock:
            locks = [
                self._player_locks[player]
                for player in sorted(self._player_locks)
            ]
            for lock in locks:
                lock.acquire()
            try:
                with self._recipes_lock:
                    recipes = copy.deepcopy(self._recipes)
                with self._stats_lock:
                    stats = dict(self._stats)
                return {
                    "version": 1,
                    "recipes": recipes,
                    "players": copy.deepcopy(self._players),
                    "inventories": copy.deepcopy(self._inventories),
                    "daily": copy.deepcopy(self._daily),
                    "stats": stats,
                    "mail": copy.deepcopy(self._mail),
                }
            finally:
                for lock in reversed(locks):
                    lock.release()

    @classmethod
    def _validate_snapshot(cls, payload: object) -> dict:
        if not isinstance(payload, dict) or set(payload) != _SNAPSHOT_KEYS:
            raise CraftError("CRAFT_SNAPSHOT_INVALID",
                             "snapshot must contain exactly seven keys")
        if not _is_int(payload["version"]) or payload["version"] != 1:
            raise CraftError("CRAFT_SNAPSHOT_INVALID", "unsupported version")

        recipes = payload["recipes"]
        if not isinstance(recipes, dict):
            raise CraftError("CRAFT_SNAPSHOT_INVALID", "invalid recipes")
        for recipe_id, spec in recipes.items():
            if not _nonempty_string(recipe_id):
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid recipe id")
            if not isinstance(spec, dict) or set(spec) != {
                "materials", "base_exp", "bonus_item"
            }:
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid recipe record")
            try:
                cls._recipe_record(
                    spec["materials"], spec["base_exp"], spec["bonus_item"]
                )
            except CraftError as exc:
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid recipe record") from exc

        players = payload["players"]
        if not isinstance(players, dict):
            raise CraftError("CRAFT_SNAPSHOT_INVALID", "invalid players")
        for player, exp in players.items():
            if not _nonempty_string(player):
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid player")
            if not _is_int(exp) or exp < 0:
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid player exp")

        inventories = payload["inventories"]
        if not isinstance(inventories, dict):
            raise CraftError("CRAFT_SNAPSHOT_INVALID",
                             "invalid inventories")
        for player, inventory in inventories.items():
            if not _nonempty_string(player) or not isinstance(inventory, dict):
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid inventory")
            for item, amount in inventory.items():
                if (not _nonempty_string(item) or not _is_int(amount)
                        or amount <= 0):
                    raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                     "invalid inventory item")

        daily = payload["daily"]
        if not isinstance(daily, dict):
            raise CraftError("CRAFT_SNAPSHOT_INVALID", "invalid daily")
        for player, record in daily.items():
            if not _nonempty_string(player):
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid daily player")
            if (not isinstance(record, dict)
                    or set(record) != {"date", "used"}):
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid daily record")
            try:
                date.fromisoformat(record["date"])
            except (TypeError, ValueError) as exc:
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid daily date") from exc
            if not _is_int(record["used"]) or record["used"] < 0:
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid daily count")

        stats = payload["stats"]
        if not isinstance(stats, dict) or set(stats) != _STAT_KEYS:
            raise CraftError("CRAFT_SNAPSHOT_INVALID", "invalid stats")
        for amount in stats.values():
            if not _is_int(amount) or amount < 0:
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid stat count")

        mail = payload["mail"]
        if not isinstance(mail, dict):
            raise CraftError("CRAFT_SNAPSHOT_INVALID", "invalid mail")
        for player, entries in mail.items():
            if not _nonempty_string(player) or not isinstance(entries, list):
                raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                 "invalid mail box")
            seen_ids: set[int] = set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {
                    "id", "item", "count"
                }:
                    raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                     "invalid mail entry")
                if (not _is_int(entry["id"]) or entry["id"] <= 0
                        or entry["id"] in seen_ids
                        or not _nonempty_string(entry["item"])
                        or not _is_int(entry["count"])
                        or entry["count"] <= 0):
                    raise CraftError("CRAFT_SNAPSHOT_INVALID",
                                     "invalid mail entry")
                seen_ids.add(entry["id"])

        return payload

    def restore(self, state: dict) -> None:
        try:
            candidate = copy.deepcopy(state)
            candidate = self._validate_snapshot(candidate)
        except CraftError:
            raise
        except Exception as exc:
            raise CraftError("CRAFT_SNAPSHOT_INVALID",
                             "unreadable snapshot") from exc

        with self._player_locks_lock:
            locks = [
                self._player_locks[player]
                for player in sorted(self._player_locks)
            ]
            for lock in locks:
                lock.acquire()
            try:
                self._recipes = candidate["recipes"]
                self._players = candidate["players"]
                self._inventories = candidate["inventories"]
                self._daily = candidate["daily"]
                self._mail = candidate["mail"]
                with self._stats_lock:
                    self._stats = candidate["stats"]
            finally:
                for lock in reversed(locks):
                    lock.release()
