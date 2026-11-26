"""扫描目标的切片规划与断点续扫台账。"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from dataclasses import replace
from typing import Sequence

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"

LEDGER_FORMAT = "scan-shard-ledger/1"
LEDGER_TMP_SUFFIX = ".part"


class ShardLimitExceeded(Exception):
    """目标规模超过分片总数上限。"""


class DuplicateTargetError(Exception):
    """目标清单里出现重复条目。"""


class UnknownShardError(Exception):
    """台账里没有该分片编号。"""


class LedgerCorruptError(Exception):
    """台账内容无法解析或格式版本不符。"""


@dataclass(frozen=True)
class ShardPlanConfig:
    max_shard_size: int = 100
    min_shard_size: int = 10
    max_shards: int = 64


@dataclass(frozen=True)
class Shard:
    index: int
    targets: tuple[str, ...]
    state: str = STATE_PENDING
    owner: str | None = None
    processed: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class ClaimResult:
    shard_index: int
    owner: str
    granted: bool
    holder: str | None = None


@dataclass
class ShardLedger:
    generation: int = 0
    shards: dict[int, Shard] = field(default_factory=dict)
    failed_reasons: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeReport:
    resumed_completed: tuple[int, ...] = ()
    resumed_pending: tuple[int, ...] = ()
    appended: tuple[int, ...] = ()
    generation: int = 0


class ScanShardResumer:
    """把目标清单切成有上限的分片，并把完成状态写入可恢复的台账。"""

    def __init__(self, config: ShardPlanConfig | None = None) -> None:
        self.config = config or ShardPlanConfig()
        self._ledger = ShardLedger()
        self._lock = threading.RLock()

    def _bump_generation(self) -> None:
        self._ledger.generation += 1

    def _shard_or_raise(self, index: int) -> Shard:
        try:
            return self._ledger.shards[index]
        except KeyError:
            raise UnknownShardError(f"unknown shard index: {index}") from None

    def _store(self, shard: Shard) -> None:
        self._ledger.shards[shard.index] = shard
        if shard.state == STATE_FAILED:
            self._ledger.failed_reasons[shard.index] = shard.reason or ""
        else:
            self._ledger.failed_reasons.pop(shard.index, None)

    @staticmethod
    def _check_duplicates(targets: Sequence[str], existing: set[str] | None = None) -> None:
        seen = set(existing) if existing else set()
        for target in targets:
            if target in seen:
                raise DuplicateTargetError(f"duplicate target: {target}")
            seen.add(target)

    def _known_targets(self) -> set[str]:
        known: set[str] = set()
        for shard in self._ledger.shards.values():
            known.update(shard.targets)
        return known

    def _append_shards(self, targets: Sequence[str]) -> tuple[Shard, ...]:
        size = self.config.max_shard_size
        needed = (len(targets) + size - 1) // size
        if len(self._ledger.shards) + needed > self.config.max_shards:
            raise ShardLimitExceeded(
                f"shard count would exceed max_shards={self.config.max_shards}"
            )
        base = max(self._ledger.shards, default=0)
        created = []
        for offset in range(needed):
            chunk = tuple(targets[offset * size:(offset + 1) * size])
            shard = Shard(index=base + 1 + offset, targets=chunk)
            self._store(shard)
            created.append(shard)
        self._bump_generation()
        return tuple(created)

    def plan(self, targets: Sequence[str]) -> tuple[Shard, ...]:
        targets = list(targets)
        with self._lock:
            self._check_duplicates(targets)
            self._ledger = ShardLedger(generation=self._ledger.generation)
            if not targets:
                self._bump_generation()
                return ()
            return self._append_shards(targets)

    def shard_of_target(self, target: str) -> int:
        with self._lock:
            for index in sorted(self._ledger.shards):
                if target in self._ledger.shards[index].targets:
                    return index
            return -1

    def mark_started(self, index: int) -> bool:
        with self._lock:
            shard = self._shard_or_raise(index)
            if shard.state != STATE_PENDING:
                return False
            self._store(replace(shard, state=STATE_RUNNING))
            self._bump_generation()
            return True

    def mark_completed(self, index: int, processed: int) -> None:
        with self._lock:
            shard = self._shard_or_raise(index)
            if shard.state not in (STATE_PENDING, STATE_RUNNING):
                raise ValueError(f"shard {index} cannot transition from {shard.state} to completed")
            self._store(replace(shard, state=STATE_COMPLETED, processed=processed))
            self._bump_generation()

    def mark_failed(self, index: int, reason: str) -> None:
        with self._lock:
            shard = self._shard_or_raise(index)
            if shard.state not in (STATE_PENDING, STATE_RUNNING):
                raise ValueError(f"shard {index} cannot transition from {shard.state} to failed")
            self._store(replace(shard, state=STATE_FAILED, reason=reason))
            self._bump_generation()

    def claim(self, index: int, owner: str) -> ClaimResult:
        with self._lock:
            shard = self._shard_or_raise(index)
            if shard.owner is not None and shard.owner != owner:
                return ClaimResult(shard_index=index, owner=owner, granted=False, holder=shard.owner)
            self._store(replace(shard, owner=owner))
            self._bump_generation()
            return ClaimResult(shard_index=index, owner=owner, granted=True, holder=owner)

    def release(self, index: int, owner: str) -> None:
        with self._lock:
            shard = self._shard_or_raise(index)
            if shard.owner is not None and shard.owner != owner:
                raise ValueError(f"shard {index} is held by {shard.owner}, not {owner}")
            if shard.owner is None:
                return
            self._store(replace(shard, owner=None))
            self._bump_generation()

    def pending_shards(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(
                index for index in sorted(self._ledger.shards)
                if self._ledger.shards[index].state == STATE_PENDING
            )

    def completed_shards(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(
                index for index in sorted(self._ledger.shards)
                if self._ledger.shards[index].state == STATE_COMPLETED
            )

    def failed_shards(self) -> dict[int, str]:
        with self._lock:
            return {
                index: self._ledger.shards[index].reason or ""
                for index in sorted(self._ledger.shards)
                if self._ledger.shards[index].state == STATE_FAILED
            }

    def refine(self, index: int) -> tuple[Shard, ...]:
        with self._lock:
            shard = self._shard_or_raise(index)
            if shard.state != STATE_FAILED:
                raise ValueError(f"only failed shards can be refined, shard {index} is {shard.state}")
            if len(shard.targets) <= self.config.min_shard_size:
                return (shard,)
            if len(self._ledger.shards) + 2 > self.config.max_shards:
                return (shard,)
            half = len(shard.targets) // 2
            base = max(self._ledger.shards)
            first = Shard(index=base + 1, targets=shard.targets[:half])
            second = Shard(index=base + 2, targets=shard.targets[half:])
            self._store(first)
            self._store(second)
            self._bump_generation()
            return (first, second)

    def append_targets(self, targets: Sequence[str]) -> tuple[int, ...]:
        targets = list(targets)
        with self._lock:
            if not targets:
                return ()
            self._check_duplicates(targets, self._known_targets())
            created = self._append_shards(targets)
            return tuple(shard.index for shard in created)

    def resume(self, targets: Sequence[str]) -> ResumeReport:
        targets = list(targets)
        with self._lock:
            completed = tuple(
                index for index in sorted(self._ledger.shards)
                if self._ledger.shards[index].state == STATE_COMPLETED
            )
            for shard in list(self._ledger.shards.values()):
                if shard.state in (STATE_RUNNING, STATE_FAILED):
                    self._store(replace(
                        shard, state=STATE_PENDING, owner=None, processed=0, reason=None,
                    ))
            known = self._known_targets()
            new_targets = [target for target in targets if target not in known]
            self._check_duplicates(new_targets, known)
            appended = self._append_shards(new_targets) if new_targets else ()
            self._bump_generation()
            pending = tuple(
                index for index in sorted(self._ledger.shards)
                if self._ledger.shards[index].state == STATE_PENDING
            )
            return ResumeReport(
                resumed_completed=completed,
                resumed_pending=pending,
                appended=tuple(shard.index for shard in appended),
                generation=self._ledger.generation,
            )

    def save(self, path: str) -> None:
        payload = self.snapshot()
        tmp_path = f"{path}{LEDGER_TMP_SUFFIX}"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    def load(self, path: str) -> ShardLedger:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise LedgerCorruptError(f"ledger at {path} is not valid JSON: {exc}") from exc
        ledger = self._ledger_from_state(raw)
        with self._lock:
            self._ledger = ledger
        return ledger

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "format": LEDGER_FORMAT,
                "generation": self._ledger.generation,
                "shards": [
                    {
                        "index": shard.index,
                        "targets": list(shard.targets),
                        "state": shard.state,
                        "owner": shard.owner,
                        "processed": shard.processed,
                        "reason": shard.reason,
                    }
                    for _, shard in sorted(self._ledger.shards.items())
                ],
            }

    def restore(self, state: dict) -> None:
        ledger = self._ledger_from_state(state)
        with self._lock:
            self._ledger = ledger

    @staticmethod
    def _ledger_from_state(state: dict) -> ShardLedger:
        if not isinstance(state, dict) or state.get("format") != LEDGER_FORMAT:
            raise LedgerCorruptError("ledger format version mismatch or missing")
        try:
            generation = int(state["generation"])
            shards: dict[int, Shard] = {}
            failed_reasons: dict[int, str] = {}
            for entry in state["shards"]:
                shard = Shard(
                    index=int(entry["index"]),
                    targets=tuple(entry["targets"]),
                    state=str(entry["state"]),
                    owner=entry.get("owner"),
                    processed=int(entry.get("processed", 0)),
                    reason=entry.get("reason"),
                )
                if shard.state not in (STATE_PENDING, STATE_RUNNING, STATE_COMPLETED, STATE_FAILED):
                    raise LedgerCorruptError(f"unknown shard state: {shard.state}")
                shards[shard.index] = shard
                if shard.state == STATE_FAILED:
                    failed_reasons[shard.index] = shard.reason or ""
        except LedgerCorruptError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerCorruptError(f"ledger content is corrupt: {exc}") from exc
        return ShardLedger(generation=generation, shards=shards, failed_reasons=failed_reasons)

    def cancel(self) -> None:
        with self._lock:
            self._ledger.shards = {
                index: shard
                for index, shard in self._ledger.shards.items()
                if shard.state in (STATE_COMPLETED, STATE_FAILED)
            }
            self._bump_generation()
