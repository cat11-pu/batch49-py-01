"""Batch set/clear of the immutable attribute over protected paths."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ImmutableError(ValueError):
    """Refusal raised by the batch attribute tool."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PathNode:
    path: str
    kind: str
    inode: int
    parent: object
    immutable: bool
    fs_immutable: bool
    mount: bool
    digest: str


@dataclass(frozen=True)
class CredentialReport:
    ok: bool
    missing: tuple
    action_ok: bool
    digest_ok: bool


@dataclass(frozen=True)
class BatchPlan:
    action: str
    ordered: tuple
    skipped: tuple


@dataclass(frozen=True)
class BatchResult:
    action: str
    applied: tuple
    skipped: tuple
    rolled_back: int


class ImmutableAttrBatch:
    """Plan, apply and recover immutable-attribute batches."""

    def __init__(self, *, allowed_roots, max_paths: int = 64,
                 max_depth: int = 8, clock=None) -> None:
        self.allowed_roots = tuple(allowed_roots)
        self.max_paths = max_paths
        self.max_depth = max_depth
        self._clock = clock
        self._lock = threading.RLock()
        self._nodes: dict = {}
        self._journal: list = []
        self._faults: dict = {}
        self._crash_after = None
        self._seq = 0
        self._counters = {"applied": 0, "skipped": 0, "unset": 0,
                          "rolled_back": 0, "conflicts": 0, "recovered": 0}

    def add_path(self, path, *, kind="file", inode=None, mount=False,
                 fs_immutable=True, digest="", immutable=False):
        raise NotImplementedError

    def attributes(self, path):
        raise NotImplementedError

    def credential_check(self, paths, credential, *, action, now=0):
        raise NotImplementedError

    def plan(self, paths, *, action, credential=None, strict_order=False, now=0):
        raise NotImplementedError

    def set(self, paths, *, credential, strict_order=False, now=0):
        raise NotImplementedError

    def unset(self, paths, *, credential, strict_order=False, now=0):
        raise NotImplementedError

    def verify(self, path):
        raise NotImplementedError

    def journal_entries(self):
        raise NotImplementedError

    def recover(self, *, now=0):
        raise NotImplementedError

    def inject_fault(self, path, *, code="READBACK_MISMATCH", when="apply"):
        raise NotImplementedError

    def crash_after(self, count):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
