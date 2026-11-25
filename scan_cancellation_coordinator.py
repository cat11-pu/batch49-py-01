"""扫描任务取消的级联传播与资源回收组件。"""

from __future__ import annotations

from dataclasses import dataclass, field

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"

TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_CANCELLED})

REASON_COMPLETED = "completed"


class UnknownTaskError(Exception):
    """任务编号未注册。"""


class DuplicateTaskError(Exception):
    """任务编号重复注册。"""


class CycleError(Exception):
    """父子关系会形成环。"""


class InvalidTransitionError(Exception):
    """当前状态不允许该操作。"""


@dataclass(frozen=True)
class CoordinatorConfig:
    max_children: int = 32
    defer_cancel: bool = True


@dataclass(frozen=True)
class TaskNode:
    task_id: str
    parent_id: str | None = None
    state: str = STATE_PENDING
    target: str | None = None
    completed_checks: tuple[str, ...] = ()
    terminal_reason: str | None = None
    terminal_tick: int | None = None


@dataclass(frozen=True)
class CancelTicket:
    ticket_id: int
    task_id: str
    reason: str
    tick: int
    confirmed: bool = False


@dataclass(frozen=True)
class CancelReport:
    tickets: tuple[CancelTicket, ...] = ()
    cancelled: tuple[str, ...] = ()
    already_terminal: tuple[str, ...] = ()


@dataclass(frozen=True)
class SealedSnapshot:
    task_id: str
    covered_checks: tuple[str, ...]
    reason: str
    tick: int


class ScanCancellationCoordinator:
    """沿任务树级联取消，并把部分结果封存进快照。"""

    def __init__(self, config: CoordinatorConfig | None = None) -> None:
        self.config = config or CoordinatorConfig()
        self._nodes: dict[str, TaskNode] = {}
        self._children: dict[str, list[str]] = {}
        self._locks: set[str] = set()
        self._slots: set[str] = set()
        self._sealed: dict[str, SealedSnapshot] = {}
        self._tick = 0

    def register(
        self, task_id: str, parent_id: str | None = None, target: str | None = None
    ) -> TaskNode:
        raise NotImplementedError

    def link(self, child_id: str, parent_id: str) -> None:
        raise NotImplementedError

    def start(self, task_id: str) -> TaskNode:
        raise NotImplementedError

    def complete_check(self, task_id: str, check_name: str) -> TaskNode:
        raise NotImplementedError

    def finish(self, task_id: str) -> TaskNode:
        raise NotImplementedError

    def cancel(self, task_id: str, reason: str) -> CancelReport:
        raise NotImplementedError

    def request_cancel(self, task_id: str, reason: str) -> CancelTicket:
        raise NotImplementedError

    def confirm_cancel(self, ticket: CancelTicket) -> CancelReport:
        raise NotImplementedError

    def seal_partial(self, task_id: str) -> SealedSnapshot:
        raise NotImplementedError

    def state_of(self, task_id: str) -> str:
        raise NotImplementedError

    def terminal_of(self, task_id: str) -> str | None:
        raise NotImplementedError

    def children_of(self, task_id: str) -> tuple[str, ...]:
        raise NotImplementedError

    def held_locks(self) -> frozenset[str]:
        raise NotImplementedError

    def held_slots(self) -> frozenset[str]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
