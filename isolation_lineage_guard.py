"""Process lineage guard for isolated units: chain checks and child quotas."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


class LineageError(ValueError):
    """Raised when a lineage operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProcRow:
    """One row of a host process table snapshot."""

    pid: int
    ppid: int
    starttime: int
    state: str = "R"
    adopted_by: object = None
    pid_ns: str = "host"


@dataclass(frozen=True)
class LineageEntry:
    """One process recorded as belonging to a unit."""

    pid: int
    starttime: int
    unit_id: str
    depth: int
    parent_pid: object
    state: str = "live"


@dataclass(frozen=True)
class CrossDomain:
    """A refused attempt to attach a process across unit boundaries."""

    pid: int
    starttime: int
    owner_unit: str
    target_unit: str
    reason: str


@dataclass(frozen=True)
class ScanResult:
    """Outcome of one scan of the host process table."""

    unit_id: str
    generation: int
    checked: int
    missing: tuple
    violations: tuple
    escapes: tuple
    exited: tuple
    truncated: int


class IsolationLineageGuard:
    """Track per-unit process lineage and refuse cross-unit attaches."""

    def __init__(self, *, max_children: int = 32, max_depth: int = 8) -> None:
        self.max_children = max_children
        self.max_depth = max_depth
        self._lock = threading.RLock()
        self._units = {}
        self._entries = {}
        self._cross = []
        self._escapes = {}

    def register_unit(self, unit_id, *, generation=1):
        raise NotImplementedError

    def register(self, unit_id, pid, *, ppid=None, starttime=0, now=0):
        raise NotImplementedError

    def spawn(self, unit_id, parent_pid, child_pid, *, starttime=0, now=0):
        raise NotImplementedError

    def scan(self, unit_id, table):
        raise NotImplementedError

    def lineage(self, unit_id, *, include_retired=False):
        raise NotImplementedError

    def retire(self, unit_id):
        raise NotImplementedError

    def rebuild(self, unit_id, *, generation):
        raise NotImplementedError

    def active_children(self, unit_id):
        raise NotImplementedError

    def cross_domain(self, *, unit_id=None):
        raise NotImplementedError

    def escape_candidates(self, unit_id):
        raise NotImplementedError

    def owner_of(self, pid, starttime):
        raise NotImplementedError

    def generation(self, unit_id):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError
