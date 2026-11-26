"""Outbound connection pool governor with leases, reaping and wait queues."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class PoolError(RuntimeError):
    """Raised when a pool operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ConnView:
    conn_id: str
    target: str
    owner: str
    requests: int
    created_at: str
    leased_since: str | None


@dataclass(frozen=True)
class AcquireResult:
    status: str
    conn: ConnView | None
    reused: bool
    waiter_id: str | None
    position: int | None

    def keys(self):
        return ("status", "conn", "reused", "waiter_id", "position")

    def __getitem__(self, key: str):
        if key not in self.keys():
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        for key in self.keys():
            yield key

    def __len__(self) -> int:
        return 5


_STAT_KEYS = (
    "targets",
    "connections",
    "idle",
    "leased",
    "waiters",
    "acquired",
    "reused",
    "closed",
    "reclaimed",
    "leaks",
)

_LIMIT_KEYS = (
    "max_size",
    "max_idle",
    "max_requests",
    "max_lifetime_s",
    "idle_ttl_s",
    "lease_ttl_s",
    "max_waiters",
    "wait_timeout_s",
)


class PooledConnGovernor:
    """Govern per-target connection pools, idle reuse and borrow leases."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reset_state()

    def _reset_state(self) -> None:
        self._targets: dict[str, dict[str, Any]] = {}
        self._connections: dict[str, dict[str, Any]] = {}
        self._idle: dict[str, list[str]] = {}
        self._waiters: dict[str, dict[str, Any]] = {}
        self._waiter_queues: dict[str, list[str]] = {}
        self._conn_seqs: dict[str, int] = {}
        self._waiter_seq = 0
        self._stats = {key: 0 for key in _STAT_KEYS}
        self._last_now: str | None = None

    def configure(self, target: str, *, max_size: int = 4, max_idle: int | None = None,
                  max_requests: int = 100, max_lifetime_s: int = 300,
                  idle_ttl_s: int = 60, lease_ttl_s: int = 30,
                  max_waiters: int = 4, wait_timeout_s: int = 5) -> dict:
        if max_idle is None:
            max_idle = min(2, max_size) if isinstance(max_size, int) and not isinstance(max_size, bool) else 2
        limits = {
            "max_size": max_size,
            "max_idle": max_idle,
            "max_requests": max_requests,
            "max_lifetime_s": max_lifetime_s,
            "idle_ttl_s": idle_ttl_s,
            "lease_ttl_s": lease_ttl_s,
            "max_waiters": max_waiters,
            "wait_timeout_s": wait_timeout_s,
        }
        self._validate_limits(limits)

        with self._lock:
            self._targets[target] = dict(limits)
            self._idle.setdefault(target, [])
            self._waiter_queues.setdefault(target, [])
            self._conn_seqs.setdefault(target, 0)
            return {"target": target, **dict(limits)}

    @staticmethod
    def _is_real_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def _validate_limits(cls, limits: Any) -> None:
        if not isinstance(limits, dict):
            raise PoolError("POOL_INVALID_CONFIG", {"reason": "limits must be an object"})

        missing = [key for key in _LIMIT_KEYS if key not in limits]
        if missing:
            raise PoolError(
                "POOL_INVALID_CONFIG",
                {"reason": "missing limits", "fields": missing},
            )

        for key in ("max_size", "max_idle", "max_requests"):
            value = limits[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise PoolError(
                    "POOL_INVALID_CONFIG",
                    {"field": key, "reason": "must be a positive integer"},
                )

        max_waiters = limits["max_waiters"]
        if not isinstance(max_waiters, int) or isinstance(max_waiters, bool) or max_waiters < 0:
            raise PoolError(
                "POOL_INVALID_CONFIG",
                {"field": "max_waiters", "reason": "must be a non-negative integer"},
            )

        if limits["max_idle"] > limits["max_size"]:
            raise PoolError(
                "POOL_INVALID_CONFIG",
                {"field": "max_idle", "reason": "must not exceed max_size"},
            )

        for key in ("idle_ttl_s", "lease_ttl_s", "wait_timeout_s"):
            value = limits[key]
            if not cls._is_real_number(value) or value <= 0:
                raise PoolError(
                    "POOL_INVALID_CONFIG",
                    {"field": key, "reason": "must be a positive number"},
                )

        lifetime = limits["max_lifetime_s"]
        if not cls._is_real_number(lifetime):
            raise PoolError(
                "POOL_INVALID_CONFIG",
                {"field": "max_lifetime_s", "reason": "must be a number"},
            )

    @staticmethod
    def _as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            moment = value
        elif PooledConnGovernor._is_real_number(value):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif isinstance(value, str):
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            moment = datetime.fromisoformat(text)
        else:
            raise ValueError("time must be an ISO-8601 string, datetime, or number")

        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def _canonical_now(self, now: Any) -> str:
        return self._as_datetime(now).isoformat()

    def _current_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _touch_clock(self, now_iso: str) -> None:
        moment = self._as_datetime(now_iso)
        if self._last_now is None or moment > self._as_datetime(self._last_now):
            self._last_now = now_iso

    def _elapsed(self, started_at: Any, now_iso: str) -> float:
        return (self._as_datetime(now_iso) - self._as_datetime(started_at)).total_seconds()

    def _ttl_expired(self, started_at: Any, now_iso: str, ttl: Any) -> bool:
        return self._elapsed(started_at, now_iso) >= float(ttl)

    def _view(self, record: dict[str, Any]) -> ConnView:
        return ConnView(
            conn_id=record["conn_id"],
            target=record["target"],
            owner=record["owner"],
            requests=record["requests"],
            created_at=record["created_at"],
            leased_since=record["leased_since"],
        )

    def _target_connection_count(self, target: str) -> int:
        return sum(1 for record in self._connections.values() if record["target"] == target)

    def _new_connection_id(self, target: str) -> str:
        sequence = self._conn_seqs.get(target, 0)
        while True:
            sequence += 1
            conn_id = f"{target}#{sequence}"
            if conn_id not in self._connections:
                self._conn_seqs[target] = sequence
                return conn_id

    def _new_waiter_id(self) -> str:
        self._waiter_seq += 1
        return f"w{self._waiter_seq}"

    def _close_connection(self, conn_id: str) -> dict[str, Any] | None:
        record = self._connections.pop(conn_id, None)
        if record is None:
            return None

        idle_queue = self._idle.get(record["target"])
        if idle_queue is not None and conn_id in idle_queue:
            idle_queue.remove(conn_id)

        self._stats["closed"] += 1
        return record

    def _expire_waiters(self, target: str, now_iso: str) -> None:
        config = self._targets[target]
        queue = self._waiter_queues.setdefault(target, [])
        timeout_s = config["wait_timeout_s"]

        for waiter_id in list(queue):
            waiter = self._waiters.get(waiter_id)
            if waiter is None:
                queue.remove(waiter_id)
                continue
            if waiter.get("status") != "waiting":
                continue

            if self._ttl_expired(waiter["enqueued_at"], now_iso, timeout_s):
                queue.remove(waiter_id)
                waiter["status"] = "timeout"
                waiter["conn_id"] = None

    def _queue_position_result(
        self, target: str, now_iso: str, owner: str, config: dict[str, Any]
    ) -> AcquireResult:
        queue = self._waiter_queues.setdefault(target, [])
        self._expire_waiters(target, now_iso)

        if len(queue) >= config["max_waiters"]:
            raise PoolError(
                "POOL_QUEUE_FULL",
                {"retry_after_s": config["wait_timeout_s"]},
            )

        waiter_id = self._new_waiter_id()
        self._waiters[waiter_id] = {
            "waiter_id": waiter_id,
            "target": target,
            "owner": owner,
            "enqueued_at": now_iso,
            "status": "waiting",
            "conn_id": None,
            "timeout_reported": False,
        }
        queue.append(waiter_id)
        return AcquireResult("queued", None, False, waiter_id, len(queue))

    def _promote_to_waiter(
        self, record: dict[str, Any], waiter: dict[str, Any], now_iso: str
    ) -> None:
        conn_id = record["conn_id"]
        idle_queue = self._idle.get(record["target"], [])
        if conn_id in idle_queue:
            idle_queue.remove(conn_id)

        record["status"] = "leased"
        record["owner"] = waiter["owner"]
        record["requests"] += 1
        record["leased_since"] = now_iso
        record["idle_since"] = None

        waiter["status"] = "ready"
        waiter["conn_id"] = conn_id
        self._stats["acquired"] += 1
        self._stats["reused"] += 1

    def _serve_idle_waiters(self, target: str, now_iso: str) -> None:
        queue = self._waiter_queues.setdefault(target, [])
        idle_queue = self._idle.setdefault(target, [])
        config = self._targets[target]

        while queue and idle_queue:
            conn_id = idle_queue[0]
            record = self._connections.get(conn_id)
            if record is None:
                idle_queue.pop(0)
                continue

            idle_since = record.get("idle_since") or record["created_at"]
            idle_expired = self._ttl_expired(idle_since, now_iso, config["idle_ttl_s"])
            lifetime_expired = self._ttl_expired(
                record["created_at"], now_iso, config["max_lifetime_s"]
            )
            if idle_expired or lifetime_expired:
                idle_queue.pop(0)
                self._close_connection(conn_id)
                continue

            waiter_id = queue[0]
            waiter = self._waiters.get(waiter_id)
            if waiter is None:
                queue.pop(0)
                continue

            queue.pop(0)
            idle_queue.pop(0)
            self._promote_to_waiter(record, waiter, now_iso)

    def _take_idle(self, target: str, now_iso: str) -> dict[str, Any] | None:
        config = self._targets[target]
        idle_queue = self._idle.setdefault(target, [])

        while idle_queue:
            conn_id = idle_queue.pop(0)
            record = self._connections.get(conn_id)
            if record is None:
                continue

            idle_since = record.get("idle_since") or record["created_at"]
            idle_expired = self._ttl_expired(idle_since, now_iso, config["idle_ttl_s"])
            lifetime_expired = self._ttl_expired(
                record["created_at"], now_iso, config["max_lifetime_s"]
            )
            if idle_expired or lifetime_expired:
                self._close_connection(conn_id)
                continue

            record["status"] = "leased"
            record["idle_since"] = None
            return record

        return None

    def acquire(self, target: str, now: str, owner: str = "") -> AcquireResult:
        with self._lock:
            config = self._targets.get(target)
            if config is None:
                raise PoolError("POOL_UNKNOWN_TARGET", {"target": target})

            now_iso = self._canonical_now(now)
            self._touch_clock(now_iso)

            self._expire_waiters(target, now_iso)
            self._serve_idle_waiters(target, now_iso)

            queue = self._waiter_queues.setdefault(target, [])
            if queue:
                return self._queue_position_result(target, now_iso, owner, config)

            record = self._take_idle(target, now_iso)
            if record is not None:
                record["owner"] = owner
                record["requests"] += 1
                record["leased_since"] = now_iso
                self._stats["acquired"] += 1
                self._stats["reused"] += 1
                return AcquireResult(
                    "acquired", self._view(record), True, None, None
                )

            if self._target_connection_count(target) < config["max_size"]:
                conn_id = self._new_connection_id(target)
                record = {
                    "conn_id": conn_id,
                    "target": target,
                    "owner": owner,
                    "requests": 1,
                    "created_at": now_iso,
                    "leased_since": now_iso,
                    "idle_since": None,
                    "status": "leased",
                }
                self._connections[conn_id] = record
                self._stats["acquired"] += 1
                return AcquireResult(
                    "acquired", self._view(record), False, None, None
                )

            return self._queue_position_result(target, now_iso, owner, config)

    def _requeue_ready_waiter(self, conn_id: str) -> None:
        for waiter in self._waiters.values():
            if waiter.get("conn_id") != conn_id or waiter.get("status") != "ready":
                continue

            waiter["status"] = "waiting"
            waiter["conn_id"] = None
            queue = self._waiter_queues.setdefault(waiter["target"], [])
            waiter_id = waiter["waiter_id"]
            if waiter_id not in queue:
                queue.insert(0, waiter_id)

    def poll_waiter(self, waiter_id: str, now: str) -> dict:
        with self._lock:
            now_iso = self._canonical_now(now)
            self._touch_clock(now_iso)

            waiter = self._waiters.get(waiter_id)
            if waiter is None:
                raise PoolError("POOL_WAIT_TIMEOUT", {"waiter_id": waiter_id})

            if waiter.get("status") == "timeout":
                if waiter.get("timeout_reported"):
                    raise PoolError("POOL_WAIT_TIMEOUT", {"waiter_id": waiter_id})
                waiter["timeout_reported"] = True
                return {"status": "timeout", "conn": None}

            if waiter.get("status") == "ready":
                record = self._connections.get(waiter.get("conn_id"))
                if record is not None:
                    result = {"status": "acquired", "conn": self._view(record)}
                    self._waiters.pop(waiter_id, None)
                    return result
                self._requeue_ready_waiter(waiter["conn_id"])

            target = waiter["target"]
            config = self._targets[target]
            queue = self._waiter_queues.setdefault(target, [])
            if waiter_id not in queue and waiter.get("status") == "waiting":
                queue.append(waiter_id)

            if self._ttl_expired(waiter["enqueued_at"], now_iso, config["wait_timeout_s"]):
                if waiter_id in queue:
                    queue.remove(waiter_id)
                waiter["status"] = "timeout"
                waiter["conn_id"] = None
                waiter["timeout_reported"] = True
                return {"status": "timeout", "conn": None}

            self._serve_idle_waiters(target, now_iso)
            waiter = self._waiters.get(waiter_id, waiter)
            if waiter.get("status") == "ready":
                record = self._connections.get(waiter.get("conn_id"))
                if record is not None:
                    result = {"status": "acquired", "conn": self._view(record)}
                    self._waiters.pop(waiter_id, None)
                    return result

            return {"status": "waiting", "conn": None}

    def release(self, conn_id: str, healthy: bool = True,
                now: str | None = None) -> dict:
        with self._lock:
            record = self._connections.get(conn_id)
            if record is None:
                raise PoolError("POOL_UNKNOWN_CONN", {"conn_id": conn_id})

            now_iso = self._current_now() if now is None else self._canonical_now(now)
            self._touch_clock(now_iso)
            config = self._targets[record["target"]]

            if not healthy:
                self._close_connection(conn_id)
                return {"conn_id": conn_id, "outcome": "closed", "reason": "unhealthy"}

            if record["requests"] >= config["max_requests"]:
                self._close_connection(conn_id)
                return {"conn_id": conn_id, "outcome": "closed", "reason": "max_requests"}

            if self._ttl_expired(record["created_at"], now_iso, config["max_lifetime_s"]):
                self._close_connection(conn_id)
                return {"conn_id": conn_id, "outcome": "closed", "reason": "max_lifetime"}

            target = record["target"]
            self._expire_waiters(target, now_iso)
            queue = self._waiter_queues.setdefault(target, [])

            if queue:
                waiter_id = queue.pop(0)
                waiter = self._waiters.get(waiter_id)
                if waiter is None:
                    queue.append(waiter_id)
                else:
                    self._promote_to_waiter(record, waiter, now_iso)
                    return {
                        "conn_id": conn_id,
                        "outcome": "handed_off",
                        "promoted": waiter_id,
                    }

            idle_queue = self._idle.setdefault(target, [])
            if conn_id not in idle_queue:
                idle_queue.append(conn_id)
            record["status"] = "idle"
            record["owner"] = record.get("owner", "")
            record["leased_since"] = None
            record["idle_since"] = now_iso
            return {"conn_id": conn_id, "outcome": "idle", "promoted": None}

    def discard(self, conn_id: str, reason: str = "manual") -> bool:
        with self._lock:
            record = self._close_connection(conn_id)
            if record is None:
                return False

            self._requeue_ready_waiter(conn_id)
            return True

    def reap(self, now: str) -> dict:
        with self._lock:
            now_iso = self._canonical_now(now)
            self._touch_clock(now_iso)

            idle_closed = 0
            for target in list(self._targets):
                self._expire_waiters(target, now_iso)
                config = self._targets[target]
                idle_queue = self._idle.setdefault(target, [])

                for conn_id in list(idle_queue):
                    record = self._connections.get(conn_id)
                    if record is None:
                        idle_queue.remove(conn_id)
                        continue

                    idle_since = record.get("idle_since") or record["created_at"]
                    if self._ttl_expired(idle_since, now_iso, config["idle_ttl_s"]):
                        self._close_connection(conn_id)
                        idle_closed += 1

            leases_reclaimed = 0
            leaks = 0
            for conn_id in list(self._connections):
                record = self._connections[conn_id]
                if record.get("status") != "leased" or record.get("leased_since") is None:
                    continue

                config = self._targets[record["target"]]
                if not self._ttl_expired(
                    record["leased_since"], now_iso, config["lease_ttl_s"]
                ):
                    continue

                self._requeue_ready_waiter(conn_id)
                self._close_connection(conn_id)
                leases_reclaimed += 1
                leaks += 1
                self._stats["reclaimed"] += 1
                self._stats["leaks"] += 1

            return {
                "idle_closed": idle_closed,
                "leases_reclaimed": leases_reclaimed,
                "leaks": leaks,
            }

    def leak_report(self, now: str | None = None) -> list:
        with self._lock:
            if now is not None:
                check_iso = self._canonical_now(now)
                self._touch_clock(check_iso)
            elif self._last_now is not None:
                check_iso = self._last_now
            else:
                check_iso = self._current_now()

            reports = []
            for record in self._connections.values():
                if record.get("status") != "leased" or record.get("leased_since") is None:
                    continue
                config = self._targets[record["target"]]
                if self._ttl_expired(
                    record["leased_since"], check_iso, config["lease_ttl_s"]
                ):
                    reports.append(self._view(record))
            return reports

    def stats(self) -> dict:
        with self._lock:
            idle = sum(1 for record in self._connections.values() if record.get("status") == "idle")
            leased = sum(1 for record in self._connections.values() if record.get("status") == "leased")
            waiters = sum(len(queue) for queue in self._waiter_queues.values())
            return {
                "targets": len(self._targets),
                "connections": len(self._connections),
                "idle": idle,
                "leased": leased,
                "waiters": waiters,
                "acquired": self._stats["acquired"],
                "reused": self._stats["reused"],
                "closed": self._stats["closed"],
                "reclaimed": self._stats["reclaimed"],
                "leaks": self._stats["leaks"],
            }

    def snapshot(self) -> dict:
        with self._lock:
            state = {
                "version": 1,
                "targets": deepcopy(self._targets),
                "connections": deepcopy(self._connections),
                "idle": deepcopy(self._idle),
                "waiters": deepcopy(self._waiters),
                "waiter_queues": deepcopy(self._waiter_queues),
                "conn_seqs": deepcopy(self._conn_seqs),
                "waiter_seq": self._waiter_seq,
                "stats": deepcopy(self._stats),
                "last_now": self._last_now,
            }
            return deepcopy(state)

    def _validate_snapshot(self, state: Any) -> None:
        if not isinstance(state, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"reason": "snapshot must be an object"})
        if state.get("version") != 1 or isinstance(state.get("version"), bool):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "version"})

        targets = state.get("targets")
        if not isinstance(targets, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "targets"})
        for target, config in targets.items():
            if not isinstance(target, str) or not isinstance(config, dict):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "targets"})
            self._validate_limits(config)

        connections = state.get("connections")
        if not isinstance(connections, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "connections"})

        required_conn_fields = (
            "conn_id", "target", "owner", "requests", "created_at",
            "leased_since", "idle_since", "status",
        )
        for conn_id, record in connections.items():
            if not isinstance(conn_id, str) or not isinstance(record, dict):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "connections"})
            if any(field not in record for field in required_conn_fields):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "connections"})
            if record["conn_id"] != conn_id or record["target"] not in targets:
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "connections"})
            if not isinstance(record["owner"], str) or not isinstance(record["requests"], int):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "connections"})
            if record["status"] not in ("idle", "leased"):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "status"})
            self._as_datetime(record["created_at"])
            if record["leased_since"] is not None:
                self._as_datetime(record["leased_since"])
            if record["idle_since"] is not None:
                self._as_datetime(record["idle_since"])

        idle = state.get("idle")
        if not isinstance(idle, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "idle"})
        for target, conn_ids in idle.items():
            if target not in targets or not isinstance(conn_ids, list):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "idle"})
            if len(conn_ids) != len(set(conn_ids)):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "idle"})
            for conn_id in conn_ids:
                if conn_id not in connections:
                    raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "idle"})
                if connections[conn_id]["status"] != "idle":
                    raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "idle"})

        waiters = state.get("waiters")
        if not isinstance(waiters, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiters"})
        for waiter_id, waiter in waiters.items():
            if not isinstance(waiter_id, str) or not isinstance(waiter, dict):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiters"})
            required_waiter_fields = (
                "waiter_id", "target", "owner", "enqueued_at", "status", "conn_id"
            )
            if any(field not in waiter for field in required_waiter_fields):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiters"})
            if waiter["waiter_id"] != waiter_id or waiter["target"] not in targets:
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiters"})
            if waiter["status"] not in ("waiting", "ready", "timeout"):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiters"})
            self._as_datetime(waiter["enqueued_at"])
            if waiter.get("conn_id") is not None and waiter["conn_id"] not in connections:
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiters"})

        waiter_queues = state.get("waiter_queues")
        if not isinstance(waiter_queues, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiter_queues"})
        for target, waiter_ids in waiter_queues.items():
            if target not in targets or not isinstance(waiter_ids, list):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiter_queues"})
            if len(waiter_ids) != len(set(waiter_ids)):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiter_queues"})
            for waiter_id in waiter_ids:
                if waiter_id not in waiters or waiters[waiter_id]["status"] != "waiting":
                    raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiter_queues"})

        stats = state.get("stats")
        if not isinstance(stats, dict) or any(key not in stats for key in _STAT_KEYS):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "stats"})
        for key in _STAT_KEYS:
            if not isinstance(stats[key], int) or isinstance(stats[key], bool) or stats[key] < 0:
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "stats"})

        conn_seqs = state.get("conn_seqs")
        if not isinstance(conn_seqs, dict):
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "conn_seqs"})
        for target, sequence in conn_seqs.items():
            if target not in targets or not isinstance(sequence, int) or sequence < 0:
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "conn_seqs"})

        waiter_seq = state.get("waiter_seq")
        if not isinstance(waiter_seq, int) or isinstance(waiter_seq, bool) or waiter_seq < 0:
            raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "waiter_seq"})

        if state.get("last_now") is not None:
            if not isinstance(state["last_now"], str):
                raise PoolError("POOL_SNAPSHOT_INVALID", {"field": "last_now"})
            self._as_datetime(state["last_now"])

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                candidate = deepcopy(state)
                self._validate_snapshot(candidate)
            except PoolError:
                raise
            except Exception as exc:
                raise PoolError(
                    "POOL_SNAPSHOT_INVALID",
                    {"reason": "snapshot could not be decoded"},
                ) from exc

            self._targets = candidate["targets"]
            self._connections = candidate["connections"]
            self._idle = candidate["idle"]
            self._waiters = candidate["waiters"]
            self._waiter_queues = candidate["waiter_queues"]
            self._conn_seqs = candidate["conn_seqs"]
            self._waiter_seq = candidate["waiter_seq"]
            self._stats = {key: candidate["stats"][key] for key in _STAT_KEYS}
            self._last_now = candidate["last_now"]
