"""Generation-scoped temp directory sweeper: freeze, lease, delete, quarantine."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class SweepError(ValueError):
    """Refusal raised by the sweeper."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Generation:
    gen: int
    label: object
    state: str
    created_at: int


@dataclass(frozen=True)
class DirEntry:
    path: str
    gen: int
    inode: int
    bytes: int


class TempGenerationSweeper:
    """Create temp dirs per job generation and delete frozen generations."""

    def __init__(self, *, root, max_dirs_per_gen: int = 64,
                 max_bytes: int = 1 << 20, quarantine_limit: int = 8,
                 clock=None) -> None:
        self.root = root
        self.max_dirs_per_gen = max_dirs_per_gen
        self.max_bytes = max_bytes
        self.quarantine_limit = quarantine_limit
        self._clock = clock
        self._lock = threading.RLock()
        self._generations: dict = {}
        self._entries: dict = {}
        self._leases: dict = {}
        self._quarantine: dict = {}
        self._faults: dict = {}
        self._swept: dict = {}
        self._next_gen = 0
        self._next_inode = 0
        self._counters = {"deleted": 0, "skipped": 0, "quarantined": 0,
                          "failed": 0, "delayed": 0}

    def begin_generation(self, *, label=None, now=0):
        raise NotImplementedError

    def current_generation(self):
        raise NotImplementedError

    def freeze(self, gen):
        raise NotImplementedError

    def create(self, path, *, bytes=0, inode=None):
        raise NotImplementedError

    def acquire(self, path, *, lease):
        raise NotImplementedError

    def release(self, path, *, lease):
        raise NotImplementedError

    def rename(self, path, new_path):
        raise NotImplementedError

    def sweep(self, *, gen=None, now=0):
        raise NotImplementedError

    def dirs(self, *, gen=None):
        raise NotImplementedError

    def quarantine_report(self):
        raise NotImplementedError

    def retry_quarantine(self, *, now=0):
        raise NotImplementedError

    def inject_fault(self, path, *, code="EACCES", times=1):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
