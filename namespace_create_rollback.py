"""Transactional creation of user/pid/net/mnt/ipc/uts namespaces."""

from __future__ import annotations

import threading


class NamespaceError(ValueError):
    """Raised when namespace creation or release is refused."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


NS_KINDS = ("user", "pid", "net", "mnt", "ipc", "uts")


class NamespaceCreateRollback:
    """Create a namespace set per unit with reverse-order rollback."""

    def __init__(self, *, creator=None, closer=None, registrar=None,
                 max_ns_per_unit=6, max_fds=32):
        self.max_ns_per_unit = max_ns_per_unit
        self.max_fds = max_fds
        self._creator = creator
        self._closer = closer
        self._registrar = registrar
        self._lock = threading.RLock()

    def create(self, unit, *, kinds=None, exclusive=True, share_key=None):
        raise NotImplementedError

    def release(self, unit, *, kinds=None):
        raise NotImplementedError

    def list(self):
        raise NotImplementedError

    def rollback_log(self):
        raise NotImplementedError

    def refcount(self, unit, kind):
        raise NotImplementedError

    def fd_table(self):
        raise NotImplementedError

    def dup(self, unit, kind):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, snapshot):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
