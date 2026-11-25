"""Namespace fd ledger: per-process refs, auditing, leak detection, reclaim."""

from __future__ import annotations

import threading


class NsLedgerError(ValueError):
    """Raised when a ledger operation is refused."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class NamespaceRefLedger:
    """Track namespace holders by inode, audit against observations, reclaim."""

    def __init__(self, *, max_namespaces=32, max_holders=8, clock=None):
        self.max_namespaces = max_namespaces
        self.max_holders = max_holders
        self._clock = clock
        self._lock = threading.RLock()

    def track(self, inode, pid, *, starttime, mode="fd", path=None):
        raise NotImplementedError

    def release(self, inode, pid, *, starttime=None):
        raise NotImplementedError

    def audit(self, observed):
        raise NotImplementedError

    def leaks(self):
        raise NotImplementedError

    def reclaim(self, *, now=None):
        raise NotImplementedError

    def holders(self, inode):
        raise NotImplementedError

    def refcount(self, inode):
        raise NotImplementedError

    def state(self, inode):
        raise NotImplementedError

    def bind_mount(self, inode, mountpoint):
        raise NotImplementedError

    def unmount(self, mountpoint):
        raise NotImplementedError

    def paths(self, inode):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, snapshot):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
