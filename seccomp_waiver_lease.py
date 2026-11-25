"""seccomp 例外时限放行：工单绑定、到期收回、续期封顶与重启补收。"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class WaiverError(ValueError):
    """例外操作被拒绝时抛出。"""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Waiver:
    """一条系统调用例外。"""

    waiver_id: str
    profile: str
    syscall: str
    ticket: str
    state: str
    expires_at: int
    renewals: int


class SeccompWaiverLease:
    """管理例外的申请、审批、续期、收回与到期扫描。"""

    def __init__(self, *, max_active: int = 64, max_renewals: int = 3,
                 sweep_batch: int = 16, default_ttl: int = 3600000) -> None:
        self.max_active = max_active
        self.max_renewals = max_renewals
        self.sweep_batch = sweep_batch
        self.default_ttl = default_ttl
        self._lock = threading.RLock()

    def set_profile(self, profile, rules):
        raise NotImplementedError

    def remove_profile(self, profile):
        raise NotImplementedError

    def effective(self, profile):
        raise NotImplementedError

    def request(self, profile, syscall, ticket, *, ttl=None, now=0):
        raise NotImplementedError

    def approve(self, waiver_id, *, now=0):
        raise NotImplementedError

    def revoke(self, waiver_id, *, now=0):
        raise NotImplementedError

    def renew(self, waiver_id, *, extend, now=0):
        raise NotImplementedError

    def active(self, *, profile=None):
        raise NotImplementedError

    def sweep(self, now):
        raise NotImplementedError

    def find(self, *, ticket=None, profile=None, syscall=None):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state, *, now=0):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
