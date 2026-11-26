"""Layered lockout ladder across account, source and device dimensions."""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping
from dataclasses import dataclass

DIMENSIONS = ("account", "source", "device")

DEFAULT_LADDERS = {
    "account": ((3, 2), (5, 6), (8, 12)),
    "source": ((6, 4), (10, 10)),
    "device": ((5, 3), (9, 9)),
}

COUNTER_NAMES = (
    "failures",
    "successes",
    "locks",
    "unlocks",
    "exemptions",
    "appointments",
)


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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class LayeredLockoutLadder:
    """Accumulate failures per dimension and lock each one on its own ladder."""

    def __init__(self, *, ladders=None, max_subjects: int = 64,
                 max_appointments: int = 8, decay_after: int = 20) -> None:
        source = DEFAULT_LADDERS if ladders is None else ladders
        if not isinstance(source, Mapping):
            raise LockoutError("LOCKOUT_INVALID", "ladders must be a mapping")
        if not _is_int(max_subjects) or max_subjects < 0:
            raise LockoutError("LOCKOUT_INVALID", "max_subjects must be non-negative")
        if not _is_int(max_appointments) or max_appointments < 0:
            raise LockoutError("LOCKOUT_INVALID",
                               "max_appointments must be non-negative")
        if not _is_int(decay_after) or decay_after < 0:
            raise LockoutError("LOCKOUT_INVALID", "decay_after must be non-negative")

        self.ladders = {}
        for dimension in DIMENSIONS:
            tiers = source.get(dimension, ())
            if not isinstance(tiers, (list, tuple)):
                raise LockoutError("LOCKOUT_INVALID",
                                   f"{dimension} ladder must be a sequence")
            parsed = []
            for tier in tiers:
                if not isinstance(tier, (list, tuple)) or len(tier) != 2:
                    raise LockoutError("LOCKOUT_INVALID",
                                       f"invalid {dimension} ladder tier")
                threshold, duration = tier
                if (not _is_int(threshold) or threshold <= 0
                        or not _is_int(duration) or duration <= 0):
                    raise LockoutError("LOCKOUT_INVALID",
                                       f"invalid {dimension} ladder tier")
                parsed.append((threshold, duration))
            self.ladders[dimension] = tuple(parsed)

        self.max_subjects = max_subjects
        self.max_appointments = max_appointments
        self.decay_after = decay_after
        self._lock = threading.RLock()
        self._now = 0
        self._index = {dimension: {} for dimension in DIMENSIONS}
        self._locks = {dimension: {} for dimension in DIMENSIONS}
        self._exemptions = {}
        self._appointments = []
        self._counters = {name: 0 for name in COUNTER_NAMES}

    @staticmethod
    def _subject(value: object, name: str) -> str:
        if not isinstance(value, str):
            raise LockoutError("LOCKOUT_INVALID", f"{name} must be a string")
        normalized = value.strip()
        if not normalized:
            raise LockoutError("LOCKOUT_INVALID", f"{name} must not be empty")
        return normalized

    def _time(self, now: int | None) -> int:
        if now is None:
            return self._now
        if not _is_int(now) or now < 0:
            raise LockoutError("LOCKOUT_INVALID", "now must be a non-negative integer")
        return now

    @staticmethod
    def _positive_ticks(ticks: object) -> int:
        if not _is_int(ticks) or ticks <= 0:
            raise LockoutError("LOCKOUT_INVALID", "ticks must be a positive integer")
        return ticks

    def _counts(self, account: str, source: str, device: str) -> Counts:
        def count_for(dimension: str, subject: str) -> int:
            record = self._index[dimension].get(subject)
            return 0 if record is None else record["count"]

        return Counts(count_for("account", account),
                      count_for("source", source),
                      count_for("device", device))

    def _active_lock(self, dimension: str, subject: str,
                     now: int) -> dict | None:
        record = self._locks[dimension].get(subject)
        if record is not None and record["until"] > now:
            return record
        return None

    def _is_exempt(self, account: str, now: int) -> bool:
        record = self._exemptions.get(account)
        return record is not None and record["expires_at"] > now

    def _selected_lock(self, account: str, source: str, device: str,
                       now: int) -> tuple[str | None, dict | None]:
        exempt = self._is_exempt(account, now)
        selected_dimension = None
        selected_lock = None
        selected_until = None
        for dimension, subject in (("account", account), ("source", source),
                                   ("device", device)):
            if dimension == "account" and exempt:
                continue
            lock = self._active_lock(dimension, subject, now)
            if lock is None:
                continue
            if selected_until is None or lock["until"] > selected_until:
                selected_dimension = dimension
                selected_lock = lock
                selected_until = lock["until"]
        return selected_dimension, selected_lock

    def _status(self, account: str, source: str, device: str,
                now: int) -> LockStatus:
        dimension, lock = self._selected_lock(account, source, device, now)
        return LockStatus(
            locked=lock is not None,
            dimension=dimension,
            until=None if lock is None else lock["until"],
            counts=self._counts(account, source, device),
            exempt=self._is_exempt(account, now),
        )

    def _reached_tier(self, dimension: str, count: int) -> int | None:
        reached = None
        for index, (threshold, _duration) in enumerate(self.ladders[dimension]):
            if count >= threshold:
                reached = index
        return reached

    def record_failure(self, account: str, source: str, device: str, *,
                       now: int | None = None) -> FailureOutcome:
        account = self._subject(account, "account")
        source = self._subject(source, "source")
        device = self._subject(device, "device")
        with self._lock:
            current = self._time(now)
            subjects = {"account": account, "source": source, "device": device}
            for dimension, subject in subjects.items():
                if (subject not in self._index[dimension]
                        and len(self._index[dimension]) >= self.max_subjects):
                    raise LockoutError("LOCKOUT_INDEX_FULL",
                                       {"dimension": dimension, "subject": subject})

            previous_active = {
                dimension: self._active_lock(dimension, subject, current) is not None
                for dimension, subject in subjects.items()
            }
            next_counts = {}
            for dimension, subject in subjects.items():
                record = self._index[dimension].get(subject)
                if (record is None or record["last"] is None
                        or current - record["last"] >= self.decay_after):
                    next_counts[dimension] = 1
                else:
                    next_counts[dimension] = record["count"] + 1

            for dimension, subject in subjects.items():
                self._index[dimension][subject] = {
                    "count": next_counts[dimension],
                    "last": current,
                }
            self._counters["failures"] += 1

            for dimension, subject in subjects.items():
                tier = self._reached_tier(dimension, next_counts[dimension])
                if tier is None:
                    continue
                existing = self._locks[dimension].get(subject)
                active = existing is not None and existing["until"] > current
                duration = self.ladders[dimension][tier][1]
                until = current + duration
                if active:
                    if tier > existing["tier"]:
                        existing["tier"] = tier
                        existing["until"] = max(existing["until"], until)
                else:
                    self._locks[dimension][subject] = {
                        "until": until,
                        "tier": tier,
                    }

            account_lock = self._locks["account"].get(account)
            if account_lock is not None and account_lock["until"] > current:
                for appointment in self._appointments:
                    if (appointment["account"] == account
                            and appointment["scheduled_at"] < account_lock["until"]):
                        appointment["scheduled_at"] = account_lock["until"]

            dimension, lock = self._selected_lock(account, source, device, current)
            if lock is not None and not previous_active[dimension]:
                self._counters["locks"] += 1
            return FailureOutcome(
                locked=lock is not None,
                dimension=dimension,
                until=None if lock is None else lock["until"],
                counts=self._counts(account, source, device),
                tier=None if lock is None else lock["tier"],
            )

    def record_success(self, account: str, source: str, device: str, *,
                       now: int | None = None) -> LockStatus:
        account = self._subject(account, "account")
        source = self._subject(source, "source")
        device = self._subject(device, "device")
        with self._lock:
            current = self._time(now)
            self._counters["successes"] += 1
            account_record = self._index["account"].get(account)
            if account_record is not None:
                account_record["count"] = 0
                account_record["last"] = None
            self._locks["account"].pop(account, None)
            return self._status(account, source, device, current)

    def lock_state(self, account: str, source: str, device: str, *,
                   now: int | None = None) -> LockStatus:
        account = self._subject(account, "account")
        source = self._subject(source, "source")
        device = self._subject(device, "device")
        with self._lock:
            current = self._time(now)
            return self._status(account, source, device, current)

    def dimension_config(self, dimension: str) -> tuple:
        if not isinstance(dimension, str):
            raise LockoutError("LOCKOUT_INVALID", "unknown dimension")
        normalized = dimension.strip()
        with self._lock:
            tiers = self.ladders.get(normalized)
        if tiers is None:
            raise LockoutError("LOCKOUT_INVALID", f"unknown dimension: {dimension}")
        return tiers

    def grant_exemption(self, account: str, *, reason: str, ticks: int,
                        now: int | None = None) -> Exemption:
        account = self._subject(account, "account")
        if not isinstance(reason, str):
            raise LockoutError("LOCKOUT_INVALID", "reason must be a string")
        reason = reason.strip()
        if not reason:
            raise LockoutError("LOCKOUT_INVALID", "reason must not be empty")
        ticks = self._positive_ticks(ticks)
        with self._lock:
            current = self._time(now)
            lock = self._active_lock("account", account, current)
            if lock is None or self._is_exempt(account, current):
                raise LockoutError("LOCKOUT_NOT_LOCKED", account)
            previous_until = lock["until"]
            del self._locks["account"][account]
            expires_at = current + ticks
            self._exemptions[account] = {
                "reason": reason,
                "expires_at": expires_at,
                "previous_until": previous_until,
            }
            self._counters["exemptions"] += 1
            return Exemption(account, reason, expires_at, previous_until)

    def schedule_unlock(self, account: str, *, ticks: int,
                        now: int | None = None) -> Appointment:
        account = self._subject(account, "account")
        ticks = self._positive_ticks(ticks)
        with self._lock:
            current = self._time(now)
            if self._active_lock("account", account, current) is None:
                raise LockoutError("LOCKOUT_NOT_LOCKED", account)
            if any(item["account"] == account for item in self._appointments):
                raise LockoutError("LOCKOUT_APPOINTMENT_DUPLICATE", account)
            if len(self._appointments) >= self.max_appointments:
                raise LockoutError("LOCKOUT_QUEUE_FULL", account)
            scheduled_at = current + ticks
            self._appointments.append({
                "account": account,
                "scheduled_at": scheduled_at,
            })
            self._counters["appointments"] += 1
            return Appointment(account, scheduled_at)

    def tick(self, steps: int = 1) -> int:
        if not _is_int(steps) or steps < 0:
            raise LockoutError("LOCKOUT_INVALID",
                               "steps must be a non-negative integer")
        with self._lock:
            current = self._now + steps
            remaining = []
            for appointment in self._appointments:
                if appointment["scheduled_at"] > current:
                    remaining.append(appointment)
                    continue
                if self._locks["account"].pop(appointment["account"], None) is not None:
                    self._counters["unlocks"] += 1
            self._appointments = remaining

            for dimension in DIMENSIONS:
                expired = [
                    subject
                    for subject, lock in self._locks[dimension].items()
                    if lock["until"] <= current
                ]
                for subject in expired:
                    del self._locks[dimension][subject]
                    self._counters["unlocks"] += 1
            self._now = current
            return self._now

    def stats(self) -> dict:
        with self._lock:
            return dict(self._counters)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy({
                "version": 1,
                "clock": self._now,
                "index": self._index,
                "locks": self._locks,
                "exemptions": self._exemptions,
                "appointments": self._appointments,
                "stats": self._counters,
            })

    def restore(self, state: dict) -> None:
        def invalid(detail: object) -> LockoutError:
            return LockoutError("LOCKOUT_SNAPSHOT_INVALID", detail)

        if not isinstance(state, dict):
            raise invalid("snapshot must be a mapping")
        try:
            candidate = copy.deepcopy(state)
        except Exception as exc:
            raise invalid(f"snapshot cannot be copied: {exc}") from exc
        version = candidate.get("version")
        if not _is_int(version) or version != 1:
            raise invalid("unsupported snapshot version")

        clock = candidate.get("clock")
        if not _is_int(clock) or clock < 0:
            raise invalid("clock must be a non-negative integer")

        raw_index = candidate.get("index")
        if not isinstance(raw_index, dict):
            raise invalid("index must be a mapping")
        index = {}
        for dimension in DIMENSIONS:
            records = raw_index.get(dimension)
            if not isinstance(records, dict):
                raise invalid(f"index.{dimension} must be a mapping")
            parsed = {}
            for subject, record in records.items():
                if not isinstance(subject, str) or not subject.strip():
                    raise invalid(f"invalid index subject for {dimension}")
                if subject != subject.strip():
                    raise invalid(f"unnormalized index subject for {dimension}")
                if not isinstance(record, dict):
                    raise invalid(f"invalid index record for {dimension}")
                count = record.get("count")
                last = record.get("last")
                if not _is_int(count) or count < 0:
                    raise invalid(f"invalid count for {dimension}")
                if last is not None and (not _is_int(last) or last < 0):
                    raise invalid(f"invalid last failure for {dimension}")
                parsed[subject] = {"count": count, "last": last}
            index[dimension] = parsed

        raw_locks = candidate.get("locks")
        if not isinstance(raw_locks, dict):
            raise invalid("locks must be a mapping")
        locks = {}
        for dimension in DIMENSIONS:
            records = raw_locks.get(dimension)
            if not isinstance(records, dict):
                raise invalid(f"locks.{dimension} must be a mapping")
            parsed = {}
            for subject, record in records.items():
                if not isinstance(subject, str) or not subject.strip():
                    raise invalid(f"invalid lock subject for {dimension}")
                if subject != subject.strip():
                    raise invalid(f"unnormalized lock subject for {dimension}")
                if not isinstance(record, dict):
                    raise invalid(f"invalid lock record for {dimension}")
                until = record.get("until")
                tier = record.get("tier")
                if not _is_int(until) or until < 0:
                    raise invalid(f"invalid lock until for {dimension}")
                if not _is_int(tier) or tier < 0:
                    raise invalid(f"invalid lock tier for {dimension}")
                parsed[subject] = {"until": until, "tier": tier}
            locks[dimension] = parsed

        raw_exemptions = candidate.get("exemptions")
        if not isinstance(raw_exemptions, dict):
            raise invalid("exemptions must be a mapping")
        exemptions = {}
        for account, record in raw_exemptions.items():
            if not isinstance(account, str) or not account.strip():
                raise invalid("invalid exemption account")
            if account != account.strip():
                raise invalid("unnormalized exemption account")
            if not isinstance(record, dict):
                raise invalid("invalid exemption record")
            reason = record.get("reason")
            expires_at = record.get("expires_at")
            previous_until = record.get("previous_until")
            if not isinstance(reason, str) or not reason.strip():
                raise invalid("invalid exemption reason")
            if not _is_int(expires_at) or expires_at < 0:
                raise invalid("invalid exemption expiration")
            if not _is_int(previous_until) or previous_until < 0:
                raise invalid("invalid previous lock expiration")
            exemptions[account] = {
                "reason": reason,
                "expires_at": expires_at,
                "previous_until": previous_until,
            }

        raw_appointments = candidate.get("appointments")
        if not isinstance(raw_appointments, list):
            raise invalid("appointments must be a list")
        appointments = []
        seen_accounts = set()
        for item in raw_appointments:
            if not isinstance(item, dict):
                raise invalid("invalid appointment record")
            account = item.get("account")
            scheduled_at = item.get("scheduled_at")
            if not isinstance(account, str) or not account.strip():
                raise invalid("invalid appointment account")
            if account != account.strip():
                raise invalid("unnormalized appointment account")
            if not _is_int(scheduled_at) or scheduled_at < 0:
                raise invalid("invalid appointment time")
            if account in seen_accounts:
                raise invalid("duplicate appointment account")
            seen_accounts.add(account)
            appointments.append({
                "account": account,
                "scheduled_at": scheduled_at,
            })

        raw_stats = candidate.get("stats")
        if not isinstance(raw_stats, dict):
            raise invalid("stats must be a mapping")
        if set(raw_stats) != set(COUNTER_NAMES):
            raise invalid("invalid stats fields")
        stats = {}
        for name in COUNTER_NAMES:
            value = raw_stats[name]
            if not _is_int(value) or value < 0:
                raise invalid(f"invalid stat: {name}")
            stats[name] = value

        for dimension in DIMENSIONS:
            for subject in locks[dimension]:
                record = index[dimension].get(subject)
                if record is None or record["count"] < 1:
                    raise invalid(f"lock subject missing from {dimension} index")
        for account in exemptions:
            if account not in index["account"]:
                raise invalid("exemption account missing from account index")
        for item in appointments:
            if item["account"] not in index["account"]:
                raise invalid("appointment account missing from account index")

        with self._lock:
            self._now = clock
            self._index = index
            self._locks = locks
            self._exemptions = exemptions
            self._appointments = appointments
            self._counters = stats
