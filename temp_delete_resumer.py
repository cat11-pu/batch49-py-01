"""Resumable temporary-directory deletion with identity-checked plans."""

from __future__ import annotations

import errno
import threading


class DeleteResumeError(ValueError):
    """Raised when a delete plan cannot be built or executed."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


ENTRY_KINDS = ("dir", "file", "link", "mount")


class TempDeleteResumer:
    """Build a post-order delete plan and execute it incrementally."""

    def __init__(self, *, root, probe, listdir, remove, log_writer=None,
                 max_depth=8, max_items=10000, subplan_size=64, clock=None):
        self.root = root
        self.max_depth = max_depth
        self.max_items = max_items
        self.subplan_size = subplan_size
        self._probe = probe
        self._listdir = listdir
        self._remove = remove
        self._log_writer = log_writer
        self._clock = clock
        self._lock = threading.RLock()

    def plan(self):
        raise NotImplementedError

    def entries(self):
        raise NotImplementedError

    def subplans(self):
        raise NotImplementedError

    def resume(self):
        raise NotImplementedError

    def status(self):
        raise NotImplementedError

    def cancel(self, reason=""):
        raise NotImplementedError

    def pending(self):
        raise NotImplementedError

    def log_records(self):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, snapshot):
        raise NotImplementedError

    def classify_error(self, errno_value):
        raise NotImplementedError
