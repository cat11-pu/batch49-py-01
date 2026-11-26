"""Perishable stack ledger: batch expiry, FEFO consumption, tolerance merges."""

from __future__ import annotations

import copy
import threading
import uuid
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

    _STAT_KEYS = ("added", "consumed", "expired", "splits", "merges")

    def __init__(self, *, stack_limit: int = 20, cell_capacity: int = 8,
                 tolerance: int = 2) -> None:
        self.stack_limit = stack_limit
        self.cell_capacity = cell_capacity
        self.tolerance = tolerance
        self._lock = threading.RLock()
        # (cell, item_id) -> list of Batch, kept sorted by expires_at.
        self._batches: dict[tuple[str, str], list[Batch]] = {}
        self._stats = {key: 0 for key in self._STAT_KEYS}

    def _new_batch_id(self) -> str:
        return uuid.uuid4().hex

    def _groups(self, cell: str) -> list[tuple[str, list[Batch]]]:
        return [(item_id, group)
                for ((group_cell, item_id), group) in self._batches.items()
                if group_cell == cell and group]

    @staticmethod
    def _is_positive_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def _find_batch(self, cell: str, item_id: str,
                    expires_at: int) -> Batch | None:
        for batch in self._batches.get((cell, item_id), []):
            if batch.expires_at == expires_at:
                return batch
        return None

    def add_batch(self, cell: str, item_id: str, quantity: int,
                  expires_at: int) -> Batch:
        with self._lock:
            if not self._is_positive_int(quantity):
                raise LedgerError(
                    "LEDGER_INVALID_QUANTITY",
                    {"cell": cell, "item_id": item_id, "quantity": quantity})

            key = (cell, item_id)
            group = self._batches.setdefault(key, [])
            current_total = sum(batch.quantity for batch in group)
            if current_total + quantity > self.stack_limit:
                raise LedgerError(
                    "LEDGER_STACK_LIMIT",
                    {"cell": cell, "item_id": item_id,
                     "current": current_total, "requested": quantity,
                     "limit": self.stack_limit})

            if not group and len(self._occupied_cells()) >= self.cell_capacity:
                raise LedgerError(
                    "LEDGER_CAPACITY",
                    {"cell": cell, "capacity": self.cell_capacity})

            within_tolerance = [
                batch for batch in group
                if abs(batch.expires_at - expires_at) <= self.tolerance]
            if within_tolerance:
                target = min(within_tolerance, key=lambda b: b.expires_at)
                index = group.index(target)
                group[index] = Batch(
                    cell=target.cell,
                    item_id=target.item_id,
                    expires_at=target.expires_at,
                    quantity=target.quantity + quantity,
                    batch_id=target.batch_id)
                self._stats["added"] += quantity
                self._stats["merges"] += 1
                return group[index]

            batch = Batch(cell=cell, item_id=item_id,
                          expires_at=expires_at, quantity=quantity,
                          batch_id=self._new_batch_id())
            group.append(batch)
            group.sort(key=lambda b: b.expires_at)
            self._stats["added"] += quantity
            return batch

    def _occupied_cells(self) -> set[str]:
        return {cell for (cell, _item_id), group in self._batches.items()
                if group}

    def consume(self, cell: str, item_id: str, quantity: int, *,
                now: int) -> list[tuple[int, int]]:
        with self._lock:
            if not self._is_positive_int(quantity):
                raise LedgerError(
                    "LEDGER_INVALID_QUANTITY",
                    {"cell": cell, "item_id": item_id, "quantity": quantity})

            group = self._batches.get((cell, item_id), [])
            fresh = [batch for batch in group if batch.expires_at > now]
            available = sum(batch.quantity for batch in fresh)
            if available < quantity:
                raise LedgerError(
                    "LEDGER_SHORTAGE",
                    {"cell": cell, "item_id": item_id,
                     "requested": quantity, "available": available})

            remaining = quantity
            taken: list[tuple[int, int]] = []
            for batch in fresh:
                if remaining == 0:
                    break
                deducted = min(batch.quantity, remaining)
                taken.append((batch.expires_at, deducted))
                remaining -= deducted

            for expires_at, deducted in taken:
                for index, batch in enumerate(group):
                    if batch.expires_at != expires_at:
                        continue
                    new_quantity = batch.quantity - deducted
                    if new_quantity == 0:
                        group.pop(index)
                    else:
                        group[index] = Batch(
                            cell=batch.cell,
                            item_id=batch.item_id,
                            expires_at=batch.expires_at,
                            quantity=new_quantity,
                            batch_id=batch.batch_id)
                    break

            if not group:
                self._batches.pop((cell, item_id), None)
            self._stats["consumed"] += quantity
            return taken

    def split_batch(self, cell: str, item_id: str, expires_at: int,
                    quantity: int) -> Batch:
        with self._lock:
            if not self._is_positive_int(quantity):
                raise LedgerError(
                    "LEDGER_INVALID_SPLIT",
                    {"cell": cell, "item_id": item_id,
                     "quantity": quantity})

            group = self._batches.get((cell, item_id), [])
            for index, batch in enumerate(group):
                if batch.expires_at != expires_at:
                    continue
                if quantity >= batch.quantity:
                    raise LedgerError(
                        "LEDGER_INVALID_SPLIT",
                        {"cell": cell, "item_id": item_id,
                         "expires_at": expires_at,
                         "quantity": quantity,
                         "available": batch.quantity})
                group[index] = Batch(
                    cell=batch.cell,
                    item_id=batch.item_id,
                    expires_at=batch.expires_at,
                    quantity=batch.quantity - quantity,
                    batch_id=batch.batch_id)
                piece = Batch(cell=cell, item_id=item_id,
                              expires_at=expires_at, quantity=quantity,
                              batch_id=self._new_batch_id())
                group.append(piece)
                group.sort(key=lambda b: b.expires_at)
                self._stats["splits"] += 1
                return piece

            raise LedgerError(
                "LEDGER_BATCH_NOT_FOUND",
                {"cell": cell, "item_id": item_id,
                 "expires_at": expires_at})

    def merge_batches(self, cell: str, item_id: str, expires_a: int,
                      expires_b: int) -> Batch:
        with self._lock:
            if abs(expires_a - expires_b) > self.tolerance:
                raise LedgerError(
                    "LEDGER_MERGE_TOLERANCE",
                    {"cell": cell, "item_id": item_id,
                     "expires_a": expires_a, "expires_b": expires_b,
                     "tolerance": self.tolerance})

            group = self._batches.get((cell, item_id), [])
            if expires_a == expires_b:
                matching = [batch for batch in group
                            if batch.expires_at == expires_a]
                if len(matching) < 2:
                    raise LedgerError(
                        "LEDGER_BATCH_NOT_FOUND",
                        {"cell": cell, "item_id": item_id,
                         "expires_a": expires_a, "expires_b": expires_b})
                batch_a, batch_b = matching[0], matching[1]
            else:
                batch_a = next((batch for batch in group
                                if batch.expires_at == expires_a), None)
                batch_b = next((batch for batch in group
                                if batch.expires_at == expires_b), None)
                if batch_a is None or batch_b is None:
                    raise LedgerError(
                        "LEDGER_BATCH_NOT_FOUND",
                        {"cell": cell, "item_id": item_id,
                         "expires_a": expires_a, "expires_b": expires_b})
            if batch_a is batch_b:
                raise LedgerError(
                    "LEDGER_BATCH_NOT_FOUND",
                    {"cell": cell, "item_id": item_id,
                     "expires_a": expires_a, "expires_b": expires_b})

            total = batch_a.quantity + batch_b.quantity
            if total > self.stack_limit:
                raise LedgerError(
                    "LEDGER_STACK_LIMIT",
                    {"cell": cell, "item_id": item_id,
                     "total": total, "limit": self.stack_limit})

            target, donor = (batch_a, batch_b) if (
                batch_a.expires_at <= batch_b.expires_at
            ) else (batch_b, batch_a)
            merged = Batch(cell=target.cell,
                           item_id=target.item_id,
                           expires_at=target.expires_at,
                           quantity=total,
                           batch_id=target.batch_id)
            survivors = [
                merged if batch is target else batch
                for batch in group if batch is not donor]
            survivors.sort(key=lambda b: b.expires_at)
            self._batches[(cell, item_id)] = survivors
            self._stats["merges"] += 1
            return merged

    def expire_due(self, now: int) -> list[tuple[str, str, int]]:
        with self._lock:
            due: list[tuple[str, str, int]] = []
            for (cell, item_id), group in list(self._batches.items()):
                expired = [batch for batch in group
                           if batch.expires_at <= now]
                if not expired:
                    continue
                for batch in sorted(expired, key=lambda b: b.expires_at):
                    due.append((cell, item_id, batch.quantity))
                expired_ids = {id(batch) for batch in expired}
                survivors = [batch for batch in group
                             if id(batch) not in expired_ids]
                if survivors:
                    self._batches[(cell, item_id)] = survivors
                else:
                    self._batches.pop((cell, item_id), None)
                self._stats["expired"] += sum(
                    batch.quantity for batch in expired)
            due.sort(key=lambda row: (row[0], row[1]))
            return due

    def inspect(self, cell: str, item_id: str | None = None) -> list[Batch]:
        with self._lock:
            rows: list[Batch] = []
            for group_item_id, group in self._groups(cell):
                if item_id is not None and group_item_id != item_id:
                    continue
                rows.extend(copy.deepcopy(group))
            rows.sort(key=lambda batch: (batch.expires_at, batch.item_id))
            return rows

    def cell_usage(self) -> int:
        with self._lock:
            return len(self._occupied_cells())

    def total_quantity(self, cell: str, item_id: str) -> int:
        with self._lock:
            return sum(batch.quantity
                       for batch in self._batches.get((cell, item_id), []))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "config": {
                    "stack_limit": self.stack_limit,
                    "cell_capacity": self.cell_capacity,
                    "tolerance": self.tolerance,
                },
                "batches": [
                    {"cell": batch.cell, "item_id": batch.item_id,
                     "expires_at": batch.expires_at,
                     "quantity": batch.quantity,
                     "batch_id": batch.batch_id}
                    for group in self._batches.values()
                    for batch in group
                ],
                "stats": copy.deepcopy(self._stats),
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            if not isinstance(state, dict):
                raise LedgerError("LEDGER_SNAPSHOT_INVALID",
                                  {"reason": "state must be a dict"})
            raw_batches = state.get("batches")
            raw_stats = state.get("stats")
            if not isinstance(raw_batches, list) or not isinstance(
                    raw_stats, dict):
                raise LedgerError("LEDGER_SNAPSHOT_INVALID",
                                  {"reason": "missing batches or stats"})

            rebuilt: dict[tuple[str, str], list[Batch]] = {}
            for entry in raw_batches:
                if not isinstance(entry, dict):
                    raise LedgerError("LEDGER_SNAPSHOT_INVALID",
                                      {"reason": "batch entry must be a dict"})
                try:
                    cell = entry["cell"]
                    item_id = entry["item_id"]
                    expires_at = entry["expires_at"]
                    quantity = entry["quantity"]
                    batch_id = entry["batch_id"]
                except KeyError as missing:
                    raise LedgerError(
                        "LEDGER_SNAPSHOT_INVALID",
                        {"reason": f"missing field {missing.args[0]}"})
                if (not isinstance(cell, str) or not isinstance(item_id, str)
                        or not isinstance(batch_id, str) or not batch_id
                        or not isinstance(expires_at, int)
                        or isinstance(expires_at, bool)
                        or not isinstance(quantity, int)
                        or isinstance(quantity, bool) or quantity <= 0):
                    raise LedgerError("LEDGER_SNAPSHOT_INVALID",
                                      {"reason": "invalid batch field"})
                rebuilt.setdefault((cell, item_id), []).append(
                    Batch(cell=cell, item_id=item_id,
                          expires_at=expires_at, quantity=quantity,
                          batch_id=batch_id))

            for group in rebuilt.values():
                group.sort(key=lambda batch: batch.expires_at)

            stats: dict[str, int] = {}
            for key in self._STAT_KEYS:
                value = raw_stats.get(key)
                if (not isinstance(value, int) or isinstance(value, bool)
                        or value < 0):
                    raise LedgerError(
                        "LEDGER_SNAPSHOT_INVALID",
                        {"reason": f"invalid stat {key}"})
                stats[key] = value

            config = state.get("config", {})
            if not isinstance(config, dict):
                raise LedgerError("LEDGER_SNAPSHOT_INVALID",
                                  {"reason": "config must be a dict"})
            stack_limit = config.get("stack_limit", self.stack_limit)
            cell_capacity = config.get("cell_capacity", self.cell_capacity)
            tolerance = config.get("tolerance", self.tolerance)
            for value in (stack_limit, cell_capacity, tolerance):
                if not isinstance(value, int) or isinstance(value, bool) \
                        or value <= 0:
                    raise LedgerError("LEDGER_SNAPSHOT_INVALID",
                                      {"reason": "invalid config value"})

            self._batches = rebuilt
            self._stats = stats
            self.stack_limit = stack_limit
            self.cell_capacity = cell_capacity
            self.tolerance = tolerance

    def stats(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)
