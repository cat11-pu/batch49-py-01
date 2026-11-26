"""软硬双阈值资源治理。

对外暴露 SoftlimitGraceGovernor。所有时间参数 now 均为调用方传入的
单调秒数，实现不得读取系统墙钟。
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass


class LimitError(Exception):
    """治理器统一异常，带机器可读的 code 与 detail。"""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail if detail is not None else {}
        super().__init__(code)


@dataclass
class _CategoryState:
    soft: object
    hard: object
    usage: object = 0
    status: str = "normal"
    alert_window_start: object | None = None
    soft_crossed_at: object | None = None
    below_soft_since: object | None = None
    alert_count: int = 0
    hard_rejections: int = 0
    soft_throttles: int = 0


class SoftlimitGraceGovernor:
    """按资源类别维护软限、硬限、限速状态与告警窗口。"""

    def __init__(self, grace_seconds=30, hysteresis_seconds=60):
        self.grace_seconds = grace_seconds
        self.hysteresis_seconds = hysteresis_seconds
        self._lock = threading.RLock()
        self._categories: dict[object, _CategoryState] = {}
        self._callbacks = []

    def define(self, category, soft, hard):
        with self._lock:
            if category in self._categories:
                raise LimitError(
                    "duplicate_category", {"category": category}
                )
            try:
                valid_limits = soft < hard
            except TypeError:
                valid_limits = False
            if not valid_limits:
                raise LimitError(
                    "bad_limits", {"category": category, "soft": soft,
                                   "hard": hard}
                )
            state = _CategoryState(soft=soft, hard=hard)
            self._categories[category] = state
            return {
                "category": category,
                "soft": soft,
                "hard": hard,
            }

    def admit(self, category, amount, now):
        with self._lock:
            state = self._require_category(category)
            if self._is_invalid_amount(amount):
                raise LimitError(
                    "bad_amount", {"category": category, "amount": amount}
                )

            self._refresh_state_locked(state, now)

            if state.usage + amount > state.hard:
                state.hard_rejections += 1
                return {
                    "allowed": False,
                    "reason": "hard_limit",
                    "usage": state.usage,
                    "remaining": state.hard - state.usage,
                    "throttled": state.status == "throttled",
                }

            was_over_soft = state.usage > state.soft
            state.usage += amount
            is_over_soft = state.usage > state.soft
            alert = False

            if is_over_soft and not was_over_soft:
                state.soft_crossed_at = now

            if is_over_soft:
                if state.status == "normal":
                    state.status = "throttled"
                state.below_soft_since = None

                if amount > 0 and self._alert_window_expired(
                        state, now):
                    state.alert_window_start = now
                    state.alert_count += 1
                    alert = True
            elif state.status == "throttled":
                if state.below_soft_since is None:
                    state.below_soft_since = now

            if state.status == "throttled":
                state.soft_throttles += 1

            decision = {
                "allowed": True,
                "reason": "ok",
                "usage": state.usage,
                "remaining": state.hard - state.usage,
                "throttled": state.status == "throttled",
            }
            callbacks = tuple(self._callbacks) if alert else ()

        for callback in callbacks:
            try:
                callback(category, dict(decision))
            except Exception:
                pass
        return decision

    def release(self, category, amount, now):
        with self._lock:
            state = self._require_category(category)
            if self._is_invalid_amount(amount):
                raise LimitError(
                    "bad_amount", {"category": category, "amount": amount}
                )

            self._refresh_state_locked(state, now)
            if amount >= state.usage:
                state.usage = 0
            else:
                state.usage -= amount

            if state.status == "throttled":
                if state.usage > state.soft:
                    state.below_soft_since = None
                elif state.below_soft_since is None:
                    state.below_soft_since = now
            else:
                state.below_soft_since = None

            return state.usage

    def observe(self, category):
        with self._lock:
            return self._require_category(category).usage

    def state(self, category, now):
        with self._lock:
            state = self._require_category(category)
            self._refresh_state_locked(state, now)
            return state.status

    def alert_count(self, category):
        with self._lock:
            return self._require_category(category).alert_count

    def on_alert(self, callback):
        with self._lock:
            if not callable(callback):
                raise LimitError(
                    "bad_callback", {"callback": callback}
                )
            self._callbacks.append(callback)

    def hard_rejections(self, category):
        with self._lock:
            return self._require_category(category).hard_rejections

    def soft_throttles(self, category):
        with self._lock:
            return self._require_category(category).soft_throttles

    def snapshot(self):
        with self._lock:
            categories = {}
            for category, state in self._categories.items():
                categories[category] = {
                    "soft": state.soft,
                    "hard": state.hard,
                    "usage": state.usage,
                    "state": state.status,
                    "alert_window_start": state.alert_window_start,
                    "soft_crossed_at": state.soft_crossed_at,
                    "below_soft_since": state.below_soft_since,
                    "alert_count": state.alert_count,
                    "hard_rejections": state.hard_rejections,
                    "soft_throttles": state.soft_throttles,
                }
            return {
                "version": 1,
                "grace_seconds": self.grace_seconds,
                "hysteresis_seconds": self.hysteresis_seconds,
                "categories": categories,
            }

    def restore(self, data):
        with self._lock:
            parsed, grace_seconds, hysteresis_seconds = (
                self._parse_snapshot(data)
            )
            self.grace_seconds = grace_seconds
            self.hysteresis_seconds = hysteresis_seconds
            self._categories = parsed

    def _require_category(self, category):
        try:
            return self._categories[category]
        except KeyError:
            raise LimitError(
                "unknown_category", {"category": category}
            ) from None

    @staticmethod
    def _is_invalid_amount(amount):
        try:
            if amount < 0:
                return True
        except (TypeError, ValueError, ArithmeticError):
            return True
        if isinstance(amount, float):
            return not math.isfinite(amount)
        return False

    def _alert_window_expired(self, state, now):
        return (
            state.alert_window_start is None
            or now - state.alert_window_start >= self.grace_seconds
        )

    def _refresh_state_locked(self, state, now):
        if state.status == "throttled":
            if state.usage > state.soft:
                state.below_soft_since = None
                return
            if state.below_soft_since is None:
                state.below_soft_since = now
                return
            if now - state.below_soft_since >= self.hysteresis_seconds:
                state.status = "normal"
                state.below_soft_since = None
        elif state.usage > state.soft:
            state.status = "throttled"
            state.below_soft_since = None

    def _parse_snapshot(self, data):
        if not isinstance(data, dict):
            raise LimitError("bad_snapshot", {"reason": "not a dict"})

        version = data.get("version", 1)
        if version != 1:
            raise LimitError(
                "bad_snapshot", {"reason": "unsupported version",
                                 "version": version}
            )

        raw_categories = data.get("categories")
        if not isinstance(raw_categories, dict):
            raise LimitError(
                "bad_snapshot", {"reason": "categories must be a dict"}
            )

        parsed = {}
        for category, raw in raw_categories.items():
            if not isinstance(raw, dict):
                raise LimitError(
                    "bad_snapshot",
                    {"reason": "category must be a dict",
                     "category": category},
                )

            for field in ("soft", "hard", "usage"):
                if field not in raw:
                    raise LimitError(
                        "bad_snapshot",
                        {"reason": "missing field", "category": category,
                         "field": field},
                    )

            soft = raw["soft"]
            hard = raw["hard"]
            usage = raw["usage"]
            try:
                valid = soft < hard and 0 <= usage <= hard
            except TypeError:
                valid = False
            if not valid:
                raise LimitError(
                    "bad_snapshot",
                    {"reason": "invalid limits or usage",
                     "category": category},
                )

            status = raw.get("state", "normal")
            if status not in ("normal", "throttled"):
                raise LimitError(
                    "bad_snapshot",
                    {"reason": "invalid state", "category": category,
                     "state": status},
                )

            state = _CategoryState(
                soft=soft,
                hard=hard,
                usage=usage,
                status=status,
                alert_window_start=raw.get("alert_window_start"),
                soft_crossed_at=raw.get("soft_crossed_at"),
                below_soft_since=raw.get("below_soft_since"),
                alert_count=raw.get("alert_count", 0),
                hard_rejections=raw.get("hard_rejections", 0),
                soft_throttles=raw.get("soft_throttles", 0),
            )

            for counter in ("alert_count", "hard_rejections",
                            "soft_throttles"):
                value = getattr(state, counter)
                if (not isinstance(value, int) or isinstance(value, bool)
                        or value < 0):
                    raise LimitError(
                        "bad_snapshot",
                        {"reason": "invalid counter",
                         "category": category, "field": counter},
                    )
            parsed[category] = state

        grace_seconds = data.get("grace_seconds", self.grace_seconds)
        hysteresis_seconds = data.get(
            "hysteresis_seconds", self.hysteresis_seconds
        )
        try:
            valid_windows = grace_seconds >= 0 and hysteresis_seconds >= 0
        except TypeError:
            valid_windows = False
        if not valid_windows:
            raise LimitError(
                "bad_snapshot", {"reason": "invalid time windows"}
            )

        return parsed, grace_seconds, hysteresis_seconds
