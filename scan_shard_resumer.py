"""扫描目标的切片规划与断点续扫台账。"""

from __future__ import annotations

from dataclasses import dataclass, field
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

    def plan(self, targets: Sequence[str]) -> tuple[Shard, ...]:
        raise NotImplementedError

    def shard_of_target(self, target: str) -> int:
        raise NotImplementedError

    def mark_started(self, index: int) -> bool:
        raise NotImplementedError

    def mark_completed(self, index: int, processed: int) -> None:
        raise NotImplementedError

    def mark_failed(self, index: int, reason: str) -> None:
        raise NotImplementedError

    def claim(self, index: int, owner: str) -> ClaimResult:
        raise NotImplementedError

    def release(self, index: int, owner: str) -> None:
        raise NotImplementedError

    def pending_shards(self) -> tuple[int, ...]:
        raise NotImplementedError

    def completed_shards(self) -> tuple[int, ...]:
        raise NotImplementedError

    def failed_shards(self) -> dict[int, str]:
        raise NotImplementedError

    def refine(self, index: int) -> tuple[Shard, ...]:
        raise NotImplementedError

    def append_targets(self, targets: Sequence[str]) -> tuple[int, ...]:
        raise NotImplementedError

    def resume(self, targets: Sequence[str]) -> ResumeReport:
        raise NotImplementedError

    def save(self, path: str) -> None:
        raise NotImplementedError

    def load(self, path: str) -> ShardLedger:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        raise NotImplementedError
