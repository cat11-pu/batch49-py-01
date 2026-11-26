"""Spark exchange vault: per-type spark accounting, exchange and carry-over."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass


class SparkError(ValueError):
    """Raised when a spark operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PullResult:
    pool_id: str
    index: int
    rarity: str
    sparks_awarded: int
    pity_after: int


@dataclass(frozen=True)
class SparkQuote:
    request_id: str
    pool_type: str
    item_id: str
    cost: int
    sparks: int
    affordable: bool


@dataclass(frozen=True)
class ExchangeReceipt:
    request_id: str
    pool_type: str
    item_id: str
    cost: int
    sparks_after: int
    status: str


@dataclass
class _Batch:
    pool_id: str
    pool_type: str
    count: int
    expires_at: int


_STAT_KEYS = (
    "pulls",
    "sparks_granted",
    "sparks_spent",
    "sparks_expired",
    "exchanges",
    "duplicates",
    "carried",
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class SparkExchangeVault:
    """Track spark vouchers per pool type and settle exchange requests."""

    _SCHEMA = 1

    def __init__(self, *, catalog=None, spark_ttl: int = 604800,
                 pity_threshold: int = 90, clock=None) -> None:
        self.catalog = copy.deepcopy(catalog or {})
        self.spark_ttl = spark_ttl
        self.pity_threshold = pity_threshold
        self._clock = clock
        self._lock = threading.RLock()

        self._pools: dict[str, str] = {}
        self._batches: list[_Batch] = []
        self._pity: dict[str, int] = {}
        self._requests: dict[str, ExchangeReceipt] = {}
        self._stats = {key: 0 for key in _STAT_KEYS}

    def _now(self, now: int | None) -> int:
        if now is None:
            return int(self._clock() if self._clock is not None else time.time())
        if not _is_int(now):
            raise SparkError("SPARK_INVALID_REQUEST", "now must be an integer")
        return now

    @staticmethod
    def _require_id(value: object, field: str) -> str:
        if not isinstance(value, str) or not value:
            raise SparkError("SPARK_INVALID_REQUEST", f"{field} must be a string")
        return value

    def _pool_type(self, pool_id: str) -> str:
        self._require_id(pool_id, "pool_id")
        try:
            return self._pools[pool_id]
        except KeyError:
            raise SparkError("SPARK_UNKNOWN_POOL", pool_id) from None

    def _item_cost(self, pool_type: str, item_id: str) -> int:
        self._require_id(item_id, "item_id")
        items = self.catalog.get(pool_type)
        if not isinstance(items, dict) or item_id not in items:
            raise SparkError("SPARK_UNKNOWN_ITEM", item_id)
        return items[item_id]

    @staticmethod
    def _valid_at(batch: _Batch, now: int | None) -> bool:
        return now is None or batch.expires_at > now

    def _sparks_at(self, pool_type: str, now: int | None) -> int:
        return sum(
            batch.count
            for batch in self._batches
            if batch.pool_type == pool_type and self._valid_at(batch, now)
        )

    def _purge_expired(self, now: int) -> int:
        alive: list[_Batch] = []
        expired = 0
        for batch in self._batches:
            if batch.expires_at <= now:
                expired += batch.count
            else:
                alive.append(batch)
        self._batches = alive
        return expired

    def register_pool(self, pool_id: str, pool_type: str) -> None:
        self._require_id(pool_id, "pool_id")
        self._require_id(pool_type, "pool_type")
        with self._lock:
            existing = self._pools.get(pool_id)
            if existing is not None:
                if existing != pool_type:
                    raise SparkError("SPARK_POOL_CONFLICT", pool_id)
                return
            self._pools[pool_id] = pool_type

    def pull(self, pool_id: str, *, count: int = 1,
             now: int | None = None) -> list[PullResult]:
        with self._lock:
            pool_type = self._pool_type(pool_id)
            if not _is_int(count) or not 1 <= count <= 50:
                raise SparkError(
                    "SPARK_INVALID_REQUEST", "count must be an integer from 1 to 50")
            timestamp = self._now(now)

            pity = self._pity.get(pool_type, 0)
            results: list[PullResult] = []
            for index in range(count):
                pity += 1
                if pity >= self.pity_threshold:
                    rarity = "featured"
                    pity = 0
                else:
                    rarity = "normal"
                results.append(PullResult(
                    pool_id=pool_id,
                    index=index,
                    rarity=rarity,
                    sparks_awarded=1,
                    pity_after=pity,
                ))

            self._pity[pool_type] = pity
            self._batches.append(_Batch(
                pool_id=pool_id,
                pool_type=pool_type,
                count=count,
                expires_at=timestamp + self.spark_ttl,
            ))
            self._stats["pulls"] += count
            self._stats["sparks_granted"] += count
            return results

    def sparks_of(self, pool_type: str, *, now: int | None = None) -> int:
        if now is not None and not _is_int(now):
            raise SparkError("SPARK_INVALID_REQUEST", "now must be an integer")
        with self._lock:
            return self._sparks_at(pool_type, now)

    def pity_count(self, pool_type: str) -> int:
        with self._lock:
            return self._pity.get(pool_type, 0)

    def quote(self, request_id: str, pool_id: str, item_id: str,
              *, now: int | None = None) -> SparkQuote:
        with self._lock:
            pool_type = self._pool_type(pool_id)
            cost = self._item_cost(pool_type, item_id)
            self._require_id(request_id, "request_id")
            timestamp = self._now(now)
            sparks = self._sparks_at(pool_type, timestamp)
            return SparkQuote(
                request_id=request_id,
                pool_type=pool_type,
                item_id=item_id,
                cost=cost,
                sparks=sparks,
                affordable=sparks >= cost,
            )

    def exchange(self, request_id: str, pool_id: str, item_id: str,
                 *, now: int | None = None) -> ExchangeReceipt:
        with self._lock:
            pool_type = self._pool_type(pool_id)
            cost = self._item_cost(pool_type, item_id)
            self._require_id(request_id, "request_id")

            existing = self._requests.get(request_id)
            if existing is not None:
                self._stats["duplicates"] += 1
                return existing

            timestamp = self._now(now)
            sparks = self._sparks_at(pool_type, timestamp)
            if sparks < cost:
                raise SparkError("SPARK_INSUFFICIENT_SPARKS", cost - sparks)

            remaining = cost
            for batch in sorted(self._batches, key=lambda row: row.expires_at):
                if remaining == 0:
                    break
                if batch.pool_type != pool_type or not self._valid_at(batch, timestamp):
                    continue
                taken = min(batch.count, remaining)
                batch.count -= taken
                remaining -= taken
            self._batches = [batch for batch in self._batches if batch.count > 0]

            receipt = ExchangeReceipt(
                request_id=request_id,
                pool_type=pool_type,
                item_id=item_id,
                cost=cost,
                sparks_after=self._sparks_at(pool_type, timestamp),
                status="ok",
            )
            self._requests[request_id] = receipt
            self._stats["sparks_spent"] += cost
            self._stats["exchanges"] += 1
            return receipt

    def carry_over(self, source_pool_id: str, target_pool_id: str,
                   *, now: int | None = None) -> int:
        with self._lock:
            source_type = self._pool_type(source_pool_id)
            target_type = self._pool_type(target_pool_id)
            if source_type != target_type:
                raise SparkError("SPARK_POOL_TYPE_MISMATCH", {
                    "source": source_type,
                    "target": target_type,
                })

            timestamp = self._now(now)
            self._stats["sparks_expired"] += self._purge_expired(timestamp)

            moved = 0
            for batch in self._batches:
                if batch.pool_id == source_pool_id:
                    batch.pool_id = target_pool_id
                    moved += batch.count
            self._stats["carried"] += moved
            return moved

    def expire(self, now: int | None = None) -> int:
        with self._lock:
            timestamp = self._now(now)
            expired = self._purge_expired(timestamp)
            self._stats["sparks_expired"] += expired
            return expired

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy({
                "schema": self._SCHEMA,
                "pools": self._pools,
                "batches": [
                    {
                        "pool_id": batch.pool_id,
                        "pool_type": batch.pool_type,
                        "count": batch.count,
                        "expires_at": batch.expires_at,
                    }
                    for batch in self._batches
                ],
                "pity": self._pity,
                "requests": {
                    request_id: {
                        "request_id": receipt.request_id,
                        "pool_type": receipt.pool_type,
                        "item_id": receipt.item_id,
                        "cost": receipt.cost,
                        "sparks_after": receipt.sparks_after,
                        "status": receipt.status,
                    }
                    for request_id, receipt in self._requests.items()
                },
                "stats": self._stats,
            })

    @staticmethod
    def _state_error(detail: object) -> SparkError:
        return SparkError("SPARK_STATE_INVALID", detail)

    @classmethod
    def _parse_state(cls, state: dict) -> tuple[
            dict[str, str], list[_Batch], dict[str, int],
            dict[str, ExchangeReceipt], dict[str, int]]:
        if not isinstance(state, dict):
            raise cls._state_error("unsupported schema")
        schema = state.get("schema")
        if not _is_int(schema) or schema != cls._SCHEMA:
            raise cls._state_error("unsupported schema")

        required = ("pools", "batches", "pity", "requests", "stats")
        if any(key not in state for key in required):
            raise cls._state_error("missing fields")

        raw_pools = state["pools"]
        if not isinstance(raw_pools, dict):
            raise cls._state_error("pools must be a dict")
        pools: dict[str, str] = {}
        for pool_id, pool_type in raw_pools.items():
            if (not isinstance(pool_id, str) or not pool_id
                    or not isinstance(pool_type, str) or not pool_type):
                raise cls._state_error("invalid pool")
            pools[pool_id] = pool_type

        raw_batches = state["batches"]
        if not isinstance(raw_batches, list):
            raise cls._state_error("batches must be a list")
        batches: list[_Batch] = []
        for raw in raw_batches:
            if not isinstance(raw, dict):
                raise cls._state_error("invalid batch")
            pool_id = raw.get("pool_id")
            pool_type = raw.get("pool_type")
            count = raw.get("count")
            expires_at = raw.get("expires_at")
            if (not isinstance(pool_id, str) or pool_id not in pools
                    or not isinstance(pool_type, str)
                    or pools[pool_id] != pool_type
                    or not _is_int(count) or count <= 0
                    or not _is_int(expires_at)):
                raise cls._state_error("invalid batch")
            batches.append(_Batch(pool_id, pool_type, count, expires_at))

        raw_pity = state["pity"]
        if not isinstance(raw_pity, dict):
            raise cls._state_error("pity must be a dict")
        pity: dict[str, int] = {}
        for pool_type, count in raw_pity.items():
            if (not isinstance(pool_type, str)
                    or not _is_int(count) or count < 0):
                raise cls._state_error("invalid pity")
            pity[pool_type] = count

        raw_requests = state["requests"]
        if not isinstance(raw_requests, dict):
            raise cls._state_error("requests must be a dict")
        requests: dict[str, ExchangeReceipt] = {}
        for request_id, raw in raw_requests.items():
            if not isinstance(request_id, str) or not request_id:
                raise cls._state_error("invalid request id")
            if not isinstance(raw, dict):
                raise cls._state_error("invalid request")
            if raw.get("request_id") != request_id:
                raise cls._state_error("request id mismatch")
            fields = (
                raw.get("pool_type"),
                raw.get("item_id"),
                raw.get("cost"),
                raw.get("sparks_after"),
                raw.get("status"),
            )
            pool_type, item_id, cost, sparks_after, status = fields
            if (not isinstance(pool_type, str) or not pool_type
                    or not isinstance(item_id, str) or not item_id
                    or not _is_int(cost) or cost < 0
                    or not _is_int(sparks_after) or sparks_after < 0
                    or status != "ok"):
                raise cls._state_error("invalid request")
            requests[request_id] = ExchangeReceipt(
                request_id=request_id,
                pool_type=pool_type,
                item_id=item_id,
                cost=cost,
                sparks_after=sparks_after,
                status=status,
            )

        raw_stats = state["stats"]
        if not isinstance(raw_stats, dict):
            raise cls._state_error("stats must be a dict")
        stats: dict[str, int] = {}
        for key in _STAT_KEYS:
            value = raw_stats.get(key)
            if not _is_int(value) or value < 0:
                raise cls._state_error("invalid stats")
            stats[key] = value

        return pools, batches, pity, requests, stats

    def restore(self, state: dict) -> None:
        try:
            parsed = self._parse_state(state)
        except SparkError:
            raise
        except Exception as exc:
            raise self._state_error(exc) from None
        with self._lock:
            self._pools, self._batches, self._pity, self._requests, self._stats = parsed

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)
