"""Perishable stack ledger: batch expiry, FEFO consumption, tolerance merges."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass


class LedgerError(ValueError):
    """Raised when a ledger operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Batch:
    cell: str
    item_id: str
    expires_at: int
    quantity: int
    batch_id: str


class PerishableStackLedger:
    """Track perishable stacks per cell as expiry-stamped batches."""

    def __init__(self, *, stack_limit: int = 20, cell_capacity: int = 8,
                 tolerance: int = 2) -> None:
        self.stack_limit = stack_limit
        self.cell_capacity = cell_capacity
        self.tolerance = tolerance
        self._lock = threading.RLock()

    def add_batch(self, cell: str, item_id: str, quantity: int,
                  expires_at: int) -> Batch:
        raise NotImplementedError

    def consume(self, cell: str, item_id: str, quantity: int, *,
                now: int) -> list[tuple[int, int]]:
        raise NotImplementedError

    def split_batch(self, cell: str, item_id: str, expires_at: int,
                    quantity: int) -> Batch:
        raise NotImplementedError

    def merge_batches(self, cell: str, item_id: str, expires_a: int,
                      expires_b: int) -> Batch:
        raise NotImplementedError

    def expire_due(self, now: int) -> list[tuple[str, str, int]]:
        raise NotImplementedError

    def inspect(self, cell: str, item_id: str | None = None) -> list[Batch]:
        raise NotImplementedError

    def cell_usage(self) -> int:
        raise NotImplementedError

    def total_quantity(self, cell: str, item_id: str) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
