"""Global lock-ordering guard: ranked resources, all-or-nothing acquisition, upgrades."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Hashable
from typing import Any


class LockOrderError(ValueError):
    """Refused lock-table operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LockOrderDeadlockGuard:
    """Serialize multi-resource work behind one global resource order."""

    _STATS_KEYS = (
        "requests",
        "acquired",
        "reentrant",
        "released",
        "upgrades",
        "timeouts",
        "violations",
    )

    def __init__(self, *, max_locks_per_request: int = 8, max_locks: int = 64,
                 clock=None) -> None:
        self.max_locks_per_request = max_locks_per_request
        self.max_locks = max_locks
        self._clock = clock
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._order_version = 0
        self._order: dict[str, int] = {}
        self._holders: dict[str, dict[Hashable, dict[str, Any]]] = {}
        self._waiting: dict[Hashable, set[Hashable]] = {}
        self._stats: dict[str, int] = {key: 0 for key in self._STATS_KEYS}

    def _now(self) -> float:
        clock = self._clock
        if clock is None:
            return time.monotonic()
        if callable(clock):
            return float(clock())
        for method_name in ("monotonic", "now", "time"):
            method = getattr(clock, method_name, None)
            if callable(method):
                return float(method())
        return time.monotonic()

    @staticmethod
    def _is_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def _sorted_resources_locked(self, order: dict[str, int]) -> list[str]:
        return sorted(order, key=lambda name: (order[name], name))

    def _check_order_violation_locked(
        self,
        owner: Hashable,
        requested: str,
        order: dict[str, int],
    ) -> None:
        requested_rank = order[requested]
        violating: list[tuple[int, str]] = []
        for held_resource, owners in self._holders.items():
            if owner not in owners or held_resource not in order:
                continue
            held_rank = order[held_resource]
            if held_rank > requested_rank:
                violating.append((held_rank, held_resource))
        if violating:
            held_resource = max(violating, key=lambda item: (item[0], item[1]))[1]
            self._stats["violations"] += 1
            raise LockOrderError(
                "ORDER_VIOLATION",
                {"held": held_resource, "requested": requested},
            )

    def _conflict_locked(
        self,
        resources: list[str],
        owner: Hashable,
        mode: str,
    ) -> tuple[str | None, list[Hashable]]:
        seen: set[str] = set()
        for resource in resources:
            if resource in seen:
                continue
            seen.add(resource)
            owners = self._holders.get(resource)
            if not owners:
                continue
            blockers: list[Hashable] = []
            exclusive_held = False
            for other_owner, record in owners.items():
                if other_owner == owner:
                    continue
                blockers.append(other_owner)
                if record["mode"] == "exclusive":
                    exclusive_held = True
            if not blockers:
                continue
            if exclusive_held or mode == "exclusive":
                exclusive_owner = next(
                    (
                        other_owner
                        for other_owner in blockers
                        if owners[other_owner]["mode"] == "exclusive"
                    ),
                    None,
                )
                if exclusive_owner is not None:
                    blockers = [exclusive_owner]
                return resource, sorted(blockers, key=repr)
        return None, []

    def register_resource(self, name: str, rank: int | None = None) -> int:
        if not isinstance(name, str):
            raise LockOrderError("INVALID_RESOURCE", name)
        if rank is not None and not self._is_int(rank):
            raise LockOrderError("INVALID_RANK", rank)

        with self._lock:
            if name in self._order:
                raise LockOrderError("RESOURCE_EXISTS", name)
            if len(self._order) >= self.max_locks:
                raise LockOrderError("LOCK_TABLE_FULL", {"resource": name})
            if rank is None:
                rank = max(self._order.values(), default=-1) + 1
            self._order[name] = rank
            self._order_version += 1
            return self._order_version

    def order_version(self) -> int:
        with self._lock:
            return self._order_version

    def acquire_many(self, resources, *, owner=None, timeout: float = 0.0,
                     mode: str = "shared") -> tuple[str, ...]:
        with self._lock:
            self._stats["requests"] += 1
            try:
                requested = tuple(resources)
            except TypeError as exc:
                raise LockOrderError("INVALID_RESOURCES", resources) from exc

            if len(requested) > self.max_locks_per_request:
                raise LockOrderError(
                    "TOO_MANY_LOCKS",
                    {
                        "requested": len(requested),
                        "limit": self.max_locks_per_request,
                    },
                )

            if mode not in ("shared", "exclusive"):
                raise LockOrderError("INVALID_MODE", mode)
            if timeout is not None and not self._is_int(timeout) and not isinstance(
                timeout, float
            ):
                raise LockOrderError("INVALID_TIMEOUT", timeout)

            order_at_start = dict(self._order)
            for resource in requested:
                if not isinstance(resource, str) or resource not in order_at_start:
                    raise LockOrderError("UNKNOWN_RESOURCE", resource)

            ordered = sorted(
                requested,
                key=lambda resource: (order_at_start[resource], resource),
            )
            if ordered:
                self._check_order_violation_locked(
                    owner, ordered[0], order_at_start
                )

            real_deadline: float | None
            clock_deadline: float | None
            if timeout is None:
                real_deadline = None
                clock_deadline = None
            else:
                timeout_seconds = max(0.0, float(timeout))
                real_deadline = time.monotonic() + timeout_seconds
                clock_deadline = self._now() + timeout_seconds

            while True:
                resource, blockers = self._conflict_locked(ordered, owner, mode)
                if resource is None:
                    for locked_resource in ordered:
                        owners = self._holders.setdefault(locked_resource, {})
                        record = owners.get(owner)
                        if record is None:
                            owners[owner] = {"count": 1, "mode": mode}
                            self._stats["acquired"] += 1
                        else:
                            record["count"] += 1
                            self._stats["reentrant"] += 1
                            if mode == "exclusive":
                                record["mode"] = "exclusive"
                    self._waiting.pop(owner, None)
                    return tuple(ordered)

                if timeout is not None:
                    assert real_deadline is not None
                    assert clock_deadline is not None
                    real_remaining = real_deadline - time.monotonic()
                    clock_remaining = clock_deadline - self._now()
                    remaining = min(real_remaining, clock_remaining)
                    if remaining <= 0:
                        self._waiting.pop(owner, None)
                        self._stats["timeouts"] += 1
                        raise LockOrderError(
                            "LOCK_TIMEOUT", {"resource": resource}
                        )
                else:
                    remaining = None

                self._waiting[owner] = set(blockers)
                wait_time = None if remaining is None else max(0.0, remaining)
                if wait_time is not None:
                    wait_time = min(wait_time, 0.1)
                self._condition.wait(wait_time)
                self._waiting.pop(owner, None)

                if ordered:
                    self._check_order_violation_locked(
                        owner, ordered[0], order_at_start
                    )

    def release(self, resource: str, *, owner=None, count: int = 1) -> int:
        if not self._is_int(count) or count <= 0:
            raise LockOrderError("INVALID_COUNT", {"count": count})

        with self._lock:
            owners = self._holders.get(resource)
            record = owners.get(owner) if owners is not None else None
            if record is None or record["count"] <= 0:
                raise LockOrderError("NOT_HELD", {"resource": resource})

            record["count"] = max(0, record["count"] - count)

            if record["count"] == 0:
                del owners[owner]
                if not owners:
                    del self._holders[resource]
                self._stats["released"] += 1
                self._condition.notify_all()

            return record["count"]

    def release_all(self, *, owner=None) -> int:
        with self._lock:
            resources = [
                resource
                for resource, owners in self._holders.items()
                if owner in owners
            ]
            if not resources:
                self._waiting.pop(owner, None)
                return 0

            for resource in resources:
                owners = self._holders[resource]
                del owners[owner]
                if not owners:
                    del self._holders[resource]

            self._waiting.pop(owner, None)
            self._stats["released"] += len(resources)
            self._condition.notify_all()
            return len(resources)

    def try_upgrade(self, resource: str, *, owner=None) -> bool:
        with self._lock:
            owners = self._holders.get(resource)
            record = owners.get(owner) if owners is not None else None
            if record is None or record["count"] <= 0:
                raise LockOrderError("NOT_HELD", {"resource": resource})

            other_holders = [
                other_owner
                for other_owner in owners
                if other_owner != owner
            ]
            if other_holders:
                return False

            record["mode"] = "exclusive"
            self._stats["upgrades"] += 1
            return True

    def held(self, *, owner=None) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            ordered_names = self._sorted_resources_locked(self._order)
            for resource in ordered_names:
                record = self._holders.get(resource, {}).get(owner)
                if record is not None and record["count"] > 0:
                    result[resource] = record["count"]
            for resource, owners in self._holders.items():
                if resource in self._order:
                    continue
                record = owners.get(owner)
                if record is not None and record["count"] > 0:
                    result[resource] = record["count"]
            return result

    def wait_graph(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                waiter: sorted(blockers, key=repr)
                for waiter, blockers in self._waiting.items()
            }

    def snapshot(self) -> dict:
        with self._lock:
            holders: dict[str, dict[Hashable, dict[str, Any]]] = {}
            for resource, owners in self._holders.items():
                holder_snapshot: dict[Hashable, dict[str, Any]] = {}
                for owner, record in owners.items():
                    holder_snapshot[copy.deepcopy(owner)] = copy.deepcopy(record)
                holders[copy.deepcopy(resource)] = holder_snapshot
            return {
                "order_version": self._order_version,
                "order": copy.deepcopy(self._order),
                "holders": holders,
                "stats": dict(self._stats),
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                new_version, new_order, new_holders, new_stats = (
                    self._validate_snapshot(state)
                )
            except LockOrderError:
                raise
            except Exception as exc:
                raise LockOrderError("SNAPSHOT_INVALID", None) from exc

            self._order_version = new_version
            self._order = new_order
            self._holders = new_holders
            self._stats = new_stats
            self._waiting.clear()
            self._condition.notify_all()

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def _validate_snapshot(
        self, state: object
    ) -> tuple[
        int,
        dict[str, int],
        dict[str, dict[Hashable, dict[str, Any]]],
        dict[str, int],
    ]:
        if not isinstance(state, dict):
            raise LockOrderError("SNAPSHOT_INVALID", "state")
        expected_keys = {"order_version", "order", "holders", "stats"}
        if set(state) != expected_keys:
            raise LockOrderError("SNAPSHOT_INVALID", "keys")

        version = state["order_version"]
        if not self._is_int(version) or version < 0:
            raise LockOrderError("SNAPSHOT_INVALID", "order_version")

        order_raw = state["order"]
        if not isinstance(order_raw, dict):
            raise LockOrderError("SNAPSHOT_INVALID", "order")
        order: dict[str, int] = {}
        for resource, rank in order_raw.items():
            if not isinstance(resource, str) or not self._is_int(rank):
                raise LockOrderError("SNAPSHOT_INVALID", "order")
            order[resource] = rank
        if len(order) > self.max_locks:
            raise LockOrderError("SNAPSHOT_INVALID", "order")

        holders_raw = state["holders"]
        if not isinstance(holders_raw, dict):
            raise LockOrderError("SNAPSHOT_INVALID", "holders")
        holders: dict[str, dict[Hashable, dict[str, Any]]] = {}
        for resource, owners_raw in holders_raw.items():
            if not isinstance(resource, str) or resource not in order:
                raise LockOrderError("SNAPSHOT_INVALID", "holders")
            if not isinstance(owners_raw, dict):
                raise LockOrderError("SNAPSHOT_INVALID", "holders")
            owners: dict[Hashable, dict[str, Any]] = {}
            for owner, record_raw in owners_raw.items():
                if not isinstance(record_raw, dict):
                    raise LockOrderError("SNAPSHOT_INVALID", "holders")
                if set(record_raw) != {"count", "mode"}:
                    raise LockOrderError("SNAPSHOT_INVALID", "holders")
                count = record_raw["count"]
                mode = record_raw["mode"]
                if not self._is_int(count) or count <= 0:
                    raise LockOrderError("SNAPSHOT_INVALID", "holders")
                if mode not in ("shared", "exclusive"):
                    raise LockOrderError("SNAPSHOT_INVALID", "holders")
                owners[copy.deepcopy(owner)] = {
                    "count": count,
                    "mode": mode,
                }
            if owners:
                holders[resource] = owners

        stats_raw = state["stats"]
        if not isinstance(stats_raw, dict) or set(stats_raw) != set(
            self._STATS_KEYS
        ):
            raise LockOrderError("SNAPSHOT_INVALID", "stats")
        stats: dict[str, int] = {}
        for key in self._STATS_KEYS:
            value = stats_raw[key]
            if not self._is_int(value) or value < 0:
                raise LockOrderError("SNAPSHOT_INVALID", "stats")
            stats[key] = value

        return version, order, holders, stats
