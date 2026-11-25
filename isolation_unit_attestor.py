"""Pre-start verification and runtime recheck for isolated run units."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class IsolationAttestError(ValueError):
    """Raised when an isolation unit cannot be attested."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class UnitSpec:
    """Declared isolation properties of one run unit."""

    unit_id: str
    uid: int
    gid: int
    supplementary_groups: tuple = ()
    tmpdir: str = ""
    unit_root: str = ""
    writable_mounts: tuple = ()
    read_only_mounts: tuple = ()
    cgroup_path: str = ""
    pid_ns: str = "host"


@dataclass(frozen=True)
class ProcessInfo:
    """One process row as observed on the host."""

    pid: int
    uid: int
    starttime: int
    unit_id: str = ""
    pid_ns: str = "host"
    cgroup_path: str = ""
    open_fds: tuple = ()


@dataclass(frozen=True)
class DirStat:
    """Owner, mode and link state of the unit private directory."""

    uid: int
    gid: int
    mode: int
    symlink_target: object = None


@dataclass(frozen=True)
class CheckItem:
    """One attested precondition."""

    name: str
    ok: bool
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AttestReport:
    """Result of a start or a runtime recheck."""

    unit_id: str
    status: str
    items: tuple
    taint_reason: object = None
    last_checked_at: int = 0
    conflicts: tuple = ()


class IsolationUnitAttestor:
    """Assert dedicated uid, groups, tmpdir, cgroup and mounts per unit."""

    def __init__(self, *, max_units: int = 64) -> None:
        self.max_units = max_units
        self._lock = threading.RLock()
        self._units = {}
        self._tasks = {}
        self._rechecking = set()
        self._resets = []

    def verify(self, spec, *, processes=(), tmpdir_stat=None, unreadable=()):
        raise NotImplementedError

    def start(self, spec, *, processes=(), tmpdir_stat=None, unreadable=(),
              now=0):
        raise NotImplementedError

    def recheck(self, unit_id, *, processes=(), tmpdir_stat=None,
                unreadable=(), now=0):
        raise NotImplementedError

    def taint(self, unit_id, reason, *, conflicts=(), now=0):
        raise NotImplementedError

    def reset(self, unit_id, reason, *, now=0):
        raise NotImplementedError

    def status(self, unit_id):
        raise NotImplementedError

    def accepting(self, unit_id):
        raise NotImplementedError

    def draining(self, unit_id):
        raise NotImplementedError

    def begin_recheck(self, unit_id):
        raise NotImplementedError

    def end_recheck(self, unit_id):
        raise NotImplementedError

    def match_cgroup(self, declared, actual):
        raise NotImplementedError

    def submit(self, unit_id, task_id):
        raise NotImplementedError

    def tasks(self, unit_id):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError

    def unit_count(self):
        raise NotImplementedError
