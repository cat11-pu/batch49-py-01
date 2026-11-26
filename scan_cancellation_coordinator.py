"""扫描任务取消的级联传播与资源回收组件。"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Iterable

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"

TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_CANCELLED})

REASON_COMPLETED = "completed"
REASON_PARTIAL = "partial"


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
    created_order: int = field(default=0, compare=False)
    created_tick: int | None = field(default=None, compare=False)

    @property
    def order(self) -> int:
        return self.created_order

    @property
    def created_seq(self) -> int:
        return self.created_order


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
        if config is None:
            config = CoordinatorConfig()
        elif isinstance(config, dict):
            valid = {item.name for item in fields(CoordinatorConfig)}
            config = CoordinatorConfig(
                **{key: value for key, value in config.items() if key in valid}
            )
        self.config = config
        self._lock = threading.RLock()
        self.reset()

    def register(
        self, task_id: str, parent_id: str | None = None, target: str | None = None
    ) -> TaskNode:
        with self._lock:
            if task_id in self._nodes:
                raise DuplicateTaskError(f"task already registered: {task_id}")
            if parent_id is not None and parent_id not in self._nodes:
                raise UnknownTaskError(f"unknown parent task: {parent_id}")

            created_order = self._created_seq
            self._created_seq += 1
            node = TaskNode(
                task_id=task_id,
                parent_id=parent_id,
                state=STATE_PENDING,
                target=target,
                created_order=created_order,
            )
            self._nodes[task_id] = node
            self._children.setdefault(task_id, [])
            if parent_id is not None:
                self._children.setdefault(parent_id, []).append(task_id)
            return node

    def link(self, child_id: str, parent_id: str) -> None:
        with self._lock:
            child = self._require_task_locked(child_id)
            self._require_task_locked(parent_id)

            if child_id == parent_id or self._is_ancestor_locked(
                child_id, parent_id
            ):
                raise CycleError(
                    f"linking {child_id} under {parent_id} would create a cycle"
                )

            old_parent_id = child.parent_id
            if old_parent_id == parent_id:
                return

            if old_parent_id is not None:
                siblings = self._children.get(old_parent_id, [])
                if child_id in siblings:
                    siblings.remove(child_id)

            self._nodes[child_id] = replace(child, parent_id=parent_id)
            self._children.setdefault(parent_id, []).append(child_id)
            self._children.setdefault(child_id, [])

    def start(self, task_id: str) -> TaskNode:
        with self._lock:
            node = self._require_task_locked(task_id)
            if node.state in TERMINAL_STATES:
                raise InvalidTransitionError(
                    f"cannot start terminal task {task_id} ({node.state})"
                )

            self._acquire_resources_locked(node)
            if node.state != STATE_RUNNING:
                node = replace(node, state=STATE_RUNNING)
                self._nodes[task_id] = node
            return node

    def complete_check(self, task_id: str, check_name: str) -> TaskNode:
        with self._lock:
            node = self._require_task_locked(task_id)
            if check_name not in node.completed_checks:
                node = replace(
                    node,
                    completed_checks=(*node.completed_checks, check_name),
                )
                self._nodes[task_id] = node
            return node

    def finish(self, task_id: str) -> TaskNode:
        with self._lock:
            node = self._require_task_locked(task_id)
            if node.state in TERMINAL_STATES:
                return node

            node = replace(
                node,
                state=STATE_COMPLETED,
                terminal_reason=REASON_COMPLETED,
                terminal_tick=self._next_tick_locked(),
            )
            self._nodes[task_id] = node
            self._release_resources_locked(node)
            return node

    def cancel(self, task_id: str, reason: str) -> CancelReport:
        with self._lock:
            self._require_task_locked(task_id)
            return self._cancel_tree_locked(task_id, reason)

    def request_cancel(self, task_id: str, reason: str) -> CancelTicket:
        with self._lock:
            self._require_task_locked(task_id)
            self._ticket_seq += 1
            ticket = CancelTicket(
                ticket_id=self._ticket_seq,
                task_id=task_id,
                reason=reason,
                tick=self._next_tick_locked(),
                confirmed=False,
            )
            self._tickets[ticket.ticket_id] = ticket
            return ticket

    def confirm_cancel(self, ticket: CancelTicket) -> CancelReport:
        with self._lock:
            stored = self._resolve_ticket_locked(ticket)
            self._require_task_locked(stored.task_id)

            confirmed = replace(stored, confirmed=True)
            self._tickets[confirmed.ticket_id] = confirmed
            report = self._cancel_tree_locked(
                confirmed.task_id, confirmed.reason
            )
            return replace(report, tickets=(confirmed,))

    def seal_partial(self, task_id: str) -> SealedSnapshot:
        with self._lock:
            node = self._require_task_locked(task_id)
            sealed = self._sealed.get(task_id)
            if sealed is not None:
                return sealed

            reason = node.terminal_reason or REASON_PARTIAL
            tick = node.terminal_tick
            if tick is None:
                tick = self._next_tick_locked()

            sealed = SealedSnapshot(
                task_id=task_id,
                covered_checks=node.completed_checks,
                reason=reason,
                tick=tick,
            )
            self._sealed[task_id] = sealed
            return sealed

    def state_of(self, task_id: str) -> str:
        with self._lock:
            return self._require_task_locked(task_id).state

    def terminal_of(self, task_id: str) -> str | None:
        with self._lock:
            return self._require_task_locked(task_id).terminal_reason

    def children_of(self, task_id: str) -> tuple[str, ...]:
        with self._lock:
            self._require_task_locked(task_id)
            return tuple(self._children.get(task_id, ()))

    def held_locks(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._locks)

    def held_slots(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._slots)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "config": asdict(self.config)
                if is_dataclass_instance(self.config)
                else None,
                "tick": self._tick,
                "created_seq": self._created_seq,
                "ticket_seq": self._ticket_seq,
                "nodes": {
                    task_id: asdict(node)
                    for task_id, node in self._nodes.items()
                },
                "children": {
                    parent_id: list(child_ids)
                    for parent_id, child_ids in self._children.items()
                },
                "locks": sorted(self._locks),
                "slots": sorted(self._slots),
                "lock_owners": {
                    lock: sorted(owners)
                    for lock, owners in self._lock_owners.items()
                },
                "sealed": {
                    task_id: asdict(sealed)
                    for task_id, sealed in self._sealed.items()
                },
                "tickets": {
                    str(ticket_id): asdict(ticket)
                    for ticket_id, ticket in self._tickets.items()
                },
            }

    def restore(self, state: dict) -> None:
        if not isinstance(state, dict):
            raise TypeError("snapshot state must be a dictionary")

        with self._lock:
            nodes = self._nodes_from_state_locked(state)
            children = self._children_from_state_locked(state, nodes)
            locks_provided = "locks" in state or "held_locks" in state
            slots_provided = "slots" in state or "held_slots" in state
            locks = self._string_set_from_state(state, "locks", "held_locks")
            slots = self._string_set_from_state(state, "slots", "held_slots")
            if not locks_provided:
                locks = {
                    f"target:{node.target}"
                    for node in nodes.values()
                    if node.state == STATE_RUNNING and node.target is not None
                }
            if not slots_provided:
                slots = {
                    f"slot:{node.task_id}"
                    for node in nodes.values()
                    if node.state == STATE_RUNNING
                }
            lock_owners = self._owners_from_state_locked(state, nodes, locks)
            sealed = self._sealed_from_state_locked(state)
            tickets, ticket_seq = self._tickets_from_state_locked(state)

            tick = state.get("tick", 0)
            if not isinstance(tick, int) or isinstance(tick, bool):
                tick = 0
            for node in nodes.values():
                if isinstance(node.terminal_tick, int):
                    tick = max(tick, node.terminal_tick)
            for ticket in tickets.values():
                tick = max(tick, ticket.tick)

            created_seq = state.get("created_seq", len(nodes))
            if not isinstance(created_seq, int) or isinstance(created_seq, bool):
                created_seq = len(nodes)
            created_seq = max(created_seq, len(nodes))

            ticket_seq_from_state = state.get("ticket_seq", ticket_seq)
            if (
                not isinstance(ticket_seq_from_state, int)
                or isinstance(ticket_seq_from_state, bool)
            ):
                ticket_seq_from_state = ticket_seq
            ticket_seq = max(ticket_seq, ticket_seq_from_state)

            config = self._config_from_state_locked(state.get("config"))

            self.config = config
            self._nodes = nodes
            self._children = children
            self._locks = locks
            self._slots = slots
            self._lock_owners = lock_owners
            self._sealed = sealed
            self._tickets = tickets
            self._tick = tick
            self._created_seq = created_seq
            self._ticket_seq = ticket_seq

    def stats(self) -> dict[str, int]:
        with self._lock:
            counts = {
                STATE_PENDING: 0,
                STATE_RUNNING: 0,
                STATE_COMPLETED: 0,
                STATE_CANCELLED: 0,
            }
            for node in self._nodes.values():
                counts[node.state] = counts.get(node.state, 0) + 1
            return counts

    def reset(self) -> None:
        with self._lock:
            self._nodes = {}
            self._children = {}
            self._locks = set()
            self._slots = set()
            self._lock_owners = {}
            self._sealed = {}
            self._tickets = {}
            self._tick = 0
            self._created_seq = 0
            self._ticket_seq = 0

    def _next_tick_locked(self) -> int:
        self._tick += 1
        return self._tick

    def _require_task_locked(self, task_id: str) -> TaskNode:
        try:
            return self._nodes[task_id]
        except KeyError:
            raise UnknownTaskError(f"unknown task: {task_id}") from None

    def _is_ancestor_locked(self, candidate_id: str, task_id: str) -> bool:
        cursor: str | None = task_id
        seen: set[str] = set()
        while cursor is not None:
            if cursor == candidate_id:
                return True
            if cursor in seen:
                return False
            seen.add(cursor)
            node = self._nodes.get(cursor)
            if node is None:
                return False
            cursor = node.parent_id
        return False

    def _acquire_resources_locked(self, node: TaskNode) -> None:
        self._slots.add(f"slot:{node.task_id}")
        if node.target is not None:
            lock = f"target:{node.target}"
            self._locks.add(lock)
            self._lock_owners.setdefault(lock, set()).add(node.task_id)

    def _release_resources_locked(self, node: TaskNode) -> None:
        self._slots.discard(f"slot:{node.task_id}")
        if node.target is None:
            return

        lock = f"target:{node.target}"
        owners = self._lock_owners.get(lock)
        if owners is not None:
            owners.discard(node.task_id)
            if owners:
                return
            self._lock_owners.pop(lock, None)
        self._locks.discard(lock)

    def _cancel_tree_locked(
        self, root_id: str, reason: str
    ) -> CancelReport:
        visit_order: list[str] = []
        visited: set[str] = set()
        stack = [root_id]

        while stack:
            task_id = stack.pop()
            if task_id in visited:
                continue
            node = self._nodes.get(task_id)
            if node is None:
                continue

            visited.add(task_id)
            visit_order.append(task_id)
            children = self._children.get(task_id, ())
            for child_id in reversed(tuple(children)):
                if child_id not in visited:
                    stack.append(child_id)

        cancelled: list[str] = []
        already_terminal: list[str] = []

        for task_id in visit_order:
            node = self._nodes[task_id]
            if node.state in TERMINAL_STATES:
                already_terminal.append(task_id)
                self._release_resources_locked(node)
                continue

            cancelled.append(task_id)
            terminal_node = replace(
                node,
                state=STATE_CANCELLED,
                terminal_reason=reason,
                terminal_tick=self._next_tick_locked(),
            )
            self._nodes[task_id] = terminal_node
            self._release_resources_locked(terminal_node)

        return CancelReport(
            cancelled=tuple(cancelled),
            already_terminal=tuple(already_terminal),
        )

    def _resolve_ticket_locked(
        self, ticket: CancelTicket | int | str
    ) -> CancelTicket:
        if isinstance(ticket, CancelTicket):
            stored = self._tickets.get(ticket.ticket_id)
            if stored is not None:
                return stored
            self._tickets[ticket.ticket_id] = ticket
            return ticket

        ticket_id = int(ticket)
        try:
            return self._tickets[ticket_id]
        except KeyError:
            raise UnknownTaskError(f"unknown cancellation ticket: {ticket_id}") from None

    def _nodes_from_state_locked(self, state: dict[str, Any]) -> dict[str, TaskNode]:
        raw_nodes = state.get(
            "nodes", state.get("tasks", state.get("task_nodes", {}))
        )
        nodes: dict[str, TaskNode] = {}
        if not isinstance(raw_nodes, dict):
            raise TypeError("snapshot nodes must be a mapping")

        valid_fields = {item.name for item in fields(TaskNode)}
        for key, raw_node in raw_nodes.items():
            if isinstance(raw_node, TaskNode):
                node = raw_node
            elif isinstance(raw_node, dict):
                data = dict(raw_node)
                data.setdefault("task_id", key)
                if "created_order" not in data and "order" in data:
                    data["created_order"] = data["order"]
                data = {
                    name: value
                    for name, value in data.items()
                    if name in valid_fields
                }
                checks = data.get("completed_checks", ())
                if checks is None:
                    checks = ()
                data["completed_checks"] = tuple(checks)
                node = TaskNode(**data)
            else:
                raise TypeError("snapshot node entries must be mappings or TaskNode")
            nodes[node.task_id] = node
        return nodes

    def _children_from_state_locked(
        self, state: dict[str, Any], nodes: dict[str, TaskNode]
    ) -> dict[str, list[str]]:
        children = {task_id: [] for task_id in nodes}
        raw_children = state.get(
            "children",
            state.get("child_order", state.get("parent_child", None)),
        )

        if isinstance(raw_children, dict):
            for parent_id, child_ids in raw_children.items():
                if parent_id not in nodes or not isinstance(child_ids, Iterable):
                    continue
                children.setdefault(parent_id, [])
                for child_id in child_ids:
                    if child_id in nodes and child_id not in children[parent_id]:
                        children[parent_id].append(child_id)
        else:
            for node in nodes.values():
                if node.parent_id in nodes:
                    children.setdefault(node.parent_id, []).append(node.task_id)

        for parent_id, child_ids in children.items():
            for child_id in child_ids:
                node = nodes.get(child_id)
                if node is not None and node.parent_id != parent_id:
                    nodes[child_id] = replace(node, parent_id=parent_id)
        return children

    def _string_set_from_state(
        self, state: dict[str, Any], primary: str, alias: str
    ) -> set[str]:
        raw = state.get(primary)
        if raw is None:
            raw = state.get(alias, ())
        if raw is None:
            raw = ()
        return {str(value) for value in raw}

    def _owners_from_state_locked(
        self,
        state: dict[str, Any],
        nodes: dict[str, TaskNode],
        locks: set[str],
    ) -> dict[str, set[str]]:
        raw_owners = state.get("lock_owners", state.get("owners"))
        owners: dict[str, set[str]] = {}
        if isinstance(raw_owners, dict):
            for lock, task_ids in raw_owners.items():
                owners[str(lock)] = {str(task_id) for task_id in task_ids or ()}

        for node in nodes.values():
            if node.state == STATE_RUNNING and node.target is not None:
                lock = f"target:{node.target}"
                if lock in locks:
                    owners.setdefault(lock, set()).add(node.task_id)
        return {lock: task_ids for lock, task_ids in owners.items() if task_ids}

    def _sealed_from_state_locked(
        self, state: dict[str, Any]
    ) -> dict[str, SealedSnapshot]:
        raw_sealed = state.get(
            "sealed",
            state.get(
                "sealed_snapshots", state.get("sealed_partials", {})
            ),
        )
        sealed: dict[str, SealedSnapshot] = {}
        if not isinstance(raw_sealed, dict):
            return sealed

        valid_fields = {item.name for item in fields(SealedSnapshot)}
        for task_id, raw in raw_sealed.items():
            if isinstance(raw, SealedSnapshot):
                snapshot = raw
            elif isinstance(raw, dict):
                data = {
                    name: value
                    for name, value in raw.items()
                    if name in valid_fields
                }
                data.setdefault("task_id", task_id)
                data["covered_checks"] = tuple(data.get("covered_checks", ()) or ())
                snapshot = SealedSnapshot(**data)
            else:
                continue
            sealed[snapshot.task_id] = snapshot
        return sealed

    def _tickets_from_state_locked(
        self, state: dict[str, Any]
    ) -> tuple[dict[int, CancelTicket], int]:
        raw_tickets = state.get("tickets", {})
        tickets: dict[int, CancelTicket] = {}
        if not isinstance(raw_tickets, dict):
            return tickets, 0

        valid_fields = {item.name for item in fields(CancelTicket)}
        for key, raw in raw_tickets.items():
            if isinstance(raw, CancelTicket):
                ticket = raw
            elif isinstance(raw, dict):
                data = {
                    name: value
                    for name, value in raw.items()
                    if name in valid_fields
                }
                data.setdefault("ticket_id", int(key))
                ticket = CancelTicket(**data)
            else:
                continue
            tickets[ticket.ticket_id] = ticket
        next_id = state.get("ticket_seq")
        if not isinstance(next_id, int) or isinstance(next_id, bool):
            next_id = max(tickets, default=0)
        return tickets, max(next_id, max(tickets, default=0))

    def _config_from_state_locked(self, raw_config: Any) -> CoordinatorConfig:
        if isinstance(raw_config, CoordinatorConfig):
            return raw_config
        if isinstance(raw_config, dict):
            valid = {item.name for item in fields(CoordinatorConfig)}
            return CoordinatorConfig(
                **{key: value for key, value in raw_config.items() if key in valid}
            )
        return self.config if isinstance(self.config, CoordinatorConfig) else CoordinatorConfig()


def is_dataclass_instance(value: Any) -> bool:
    return hasattr(type(value), "__dataclass_fields__")
