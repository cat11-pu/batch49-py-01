"""Spark exchange vault: per-type spark accounting, exchange and carry-over."""

from __future__ import annotations

import threading
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


class SparkExchangeVault:
    """Track spark vouchers per pool type and settle exchange requests."""

    def __init__(self, *, catalog=None, spark_ttl: int = 604800,
                 pity_threshold: int = 90, clock=None) -> None:
        self.catalog = catalog or {}
        self.spark_ttl = spark_ttl
        self.pity_threshold = pity_threshold
        self._clock = clock
        self._lock = threading.RLock()

    def register_pool(self, pool_id: str, pool_type: str) -> None:
        raise NotImplementedError

    def pull(self, pool_id: str, *, count: int = 1,
             now: int | None = None) -> list[PullResult]:
        raise NotImplementedError

    def sparks_of(self, pool_type: str, *, now: int | None = None) -> int:
        raise NotImplementedError

    def pity_count(self, pool_type: str) -> int:
        raise NotImplementedError

    def quote(self, request_id: str, pool_id: str, item_id: str,
              *, now: int | None = None) -> SparkQuote:
        raise NotImplementedError

    def exchange(self, request_id: str, pool_id: str, item_id: str,
                 *, now: int | None = None) -> ExchangeReceipt:
        raise NotImplementedError

    def carry_over(self, source_pool_id: str, target_pool_id: str,
                   *, now: int | None = None) -> int:
        raise NotImplementedError

    def expire(self, now: int | None = None) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
