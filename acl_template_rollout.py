"""ACL template rollout: plan, apply, rollback and crash recovery."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AclRolloutError(ValueError):
    """Raised when a template rollout is refused or fails."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AclEntry:
    """One normalized access or default ACL entry."""

    tag: str
    qualifier: str
    perms: str


@dataclass(frozen=True)
class TemplateEntry:
    """One line of an ACL template, with its source line number."""

    tag: str
    qualifier: str
    perms: str
    default: bool = False
    line: int = 0


@dataclass(frozen=True)
class PathAcl:
    """In-memory stand-in for the ACL of one path."""

    dev: int
    inode: int
    is_dir: bool
    access: tuple = ()
    default: tuple = ()
    mtime_ns: int = 0


@dataclass(frozen=True)
class PathDiff:
    """Planned change for a single path."""

    path: str
    dev: int
    inode: int
    is_dir: bool
    original_access: tuple
    original_default: tuple
    desired_access: tuple
    desired_default: tuple
    current_fingerprint: str
    target_fingerprint: str


@dataclass(frozen=True)
class RolloutPlan:
    """A plan produced from a set of paths and the current ledger."""

    plan_id: str
    diffs: tuple


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying a plan."""

    plan_id: str
    applied: tuple
    rolled_back: tuple
    failed_path: object
    error_code: object
    written: int


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of rolling a plan back after the fact."""

    plan_id: str
    reverted: tuple
    skipped: tuple


class AclTemplateRollout:
    """Apply longest-prefix ACL templates as a journaled transaction."""

    def __init__(self, acl_store=None, *, uid_map=None, gid_map=None,
                 max_paths: int = 256, max_entries: int = 64,
                 max_depth: int = 8, on_write=None) -> None:
        self.acl_store = {} if acl_store is None else acl_store
        self.uid_map = dict(uid_map or {})
        self.gid_map = dict(gid_map or {})
        self.max_paths = max_paths
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.on_write = on_write
        self._lock = threading.RLock()
        self._templates = {}
        self._journal = []
        self._plans = {}

    def add_path(self, path, *, dev, inode, is_dir, access=(),
                 default=(), mtime_ns=0):
        raise NotImplementedError

    def add_template(self, prefix, entries):
        raise NotImplementedError

    def effective_template(self, path):
        raise NotImplementedError

    def resolve_principal(self, kind, name):
        raise NotImplementedError

    def recompute_masks(self, entries):
        raise NotImplementedError

    def fingerprint(self, entries):
        raise NotImplementedError

    def plan(self, paths):
        raise NotImplementedError

    def diff(self, plan):
        raise NotImplementedError

    def apply(self, plan, *, now_ns=0):
        raise NotImplementedError

    def rollback(self, plan, *, request_id=None):
        raise NotImplementedError

    def journal(self):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError

    def recover(self):
        raise NotImplementedError
