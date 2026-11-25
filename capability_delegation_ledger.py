"""Capability delegation ledger: role-scoped grants, reclaim and reconciliation."""

from __future__ import annotations

import threading
from dataclasses import dataclass

SNAPSHOT_VERSION = 1


class CapError(ValueError):
    """Refusal raised by the delegation ledger."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Delegation:
    role: str
    capability: str
    holder: str
    count: int
    granted_by: str
    granted_at: int
    expires_at: object
    state: str


@dataclass(frozen=True)
class HolderInfo:
    holder: str
    pid: int
    start: int
    group: object
    alive: bool


class CapabilityDelegationLedger:
    """Track which role holds which capability units and where they went."""

    def __init__(self, *, max_caps_per_holder: int = 16,
                 max_delegations: int = 128, max_roles: int = 8,
                 clock=None) -> None:
        self.max_caps_per_holder = max_caps_per_holder
        self.max_delegations = max_delegations
        self.max_roles = max_roles
        self._clock = clock
        self._lock = threading.RLock()
        self._daemon: dict = {}
        self._holders: dict = {}
        self._by_pid: dict = {}
        self._records: list = []
        self._counters = {"delegated": 0, "suppressed": 0, "pending_revoke": 0,
                          "revoked": 0, "reclaimed": 0, "expired": 0}

    def load_daemon(self, caps):
        raise NotImplementedError

    def register_holder(self, holder, *, pid, start, group=None):
        raise NotImplementedError

    def delegate(self, *, role, capability, holder, count=1, ttl=None,
                 now=0, granted_by="daemon"):
        raise NotImplementedError

    def revoke(self, *, role, capability, holder, now=0):
        raise NotImplementedError

    def remove_role(self, role, *, now=0):
        raise NotImplementedError

    def held_by(self, holder):
        raise NotImplementedError

    def active(self, capability):
        raise NotImplementedError

    def summary(self):
        raise NotImplementedError

    def exit_holder(self, holder, *, now=0):
        raise NotImplementedError

    def tick(self, now):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
