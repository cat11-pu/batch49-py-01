"""ACL snapshot export and reconciled restore for shared storage subtrees."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass


class AclSnapshotError(ValueError):
    """Raised when a snapshot or restore operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Record:
    """One path as recorded in a snapshot or in the live subtree."""

    path: str
    dev: int
    inode: int
    owner_uid: int
    owner_gid: int
    access: tuple = ()
    default: tuple = ()
    is_dir: bool = True
    symlink: object = None


@dataclass(frozen=True)
class Snapshot:
    """A canonicalized view of one subtree."""

    root: str
    entries: tuple
    digest: str


@dataclass(frozen=True)
class Conflict:
    """One path that cannot be restored without a decision."""

    path: str
    reason: str
    current: object
    snapshot: object


@dataclass(frozen=True)
class RestoreReport:
    """Per-path outcome of a restore run."""

    applied: tuple
    created: tuple
    skipped: tuple
    conflicted: tuple
    interrupted: bool = False


class AclSnapshotReconciler:
    """Export, compare and restore ACL state with a resumable journal."""

    def __init__(self, *, max_conflicts: int = 8, known_uids=None,
                 known_gids=None, symlink_policy: str = "link",
                 on_write=None) -> None:
        self.max_conflicts = max_conflicts
        self.known_uids = None if known_uids is None else set(known_uids)
        self.known_gids = None if known_gids is None else set(known_gids)
        self.symlink_policy = symlink_policy
        self.on_write = on_write
        self.targets = {}
        self._lock = threading.RLock()
        self._busy_segments = set()
        self._journal = []
        self._pending = None
        self._snapshot = None

    def put(self, path, *, dev, inode, owner_uid, owner_gid, access=(),
            default=(), is_dir=True, symlink=None):
        raise NotImplementedError

    def drop(self, path):
        raise NotImplementedError

    def snapshot(self, root):
        raise NotImplementedError

    def export_bytes(self, snap):
        raise NotImplementedError

    def import_bytes(self, data):
        raise NotImplementedError

    def compare(self, snap):
        raise NotImplementedError

    def restore(self, snap, *, allow_create=False, policy="keep"):
        raise NotImplementedError

    def resolve_conflict(self, path, *, strategy):
        raise NotImplementedError

    def merge_entries(self, desired, current, policy):
        raise NotImplementedError

    def fingerprint(self, record):
        raise NotImplementedError

    def journal(self):
        raise NotImplementedError

    def resume(self):
        raise NotImplementedError

    def acquire_segment(self, segment):
        raise NotImplementedError

    def release_segment(self, segment):
        raise NotImplementedError

    def save_state(self):
        raise NotImplementedError

    def load_state(self, state):
        raise NotImplementedError
