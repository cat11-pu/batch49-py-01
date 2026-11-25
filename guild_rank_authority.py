"""Guild rank authority: inherited permissions, temporary grants and audit trail."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class GuildError(ValueError):
    """Raised when a guild authority operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GrantRecord:
    grant_id: str
    grantor: str
    target: str
    permissions: tuple
    issued_at: int
    expires_at: int
    frozen: bool


class GuildRankAuthority:
    """Rank matrix, temporary grants, expiry reclamation and append-only audit."""

    def __init__(self, *, max_temp_grants: int = 32,
                 max_audit_entries: int = 1000, max_rank: int = 4) -> None:
        self.max_temp_grants = max_temp_grants
        self.max_audit_entries = max_audit_entries
        self.max_rank = max_rank
        self._lock = threading.RLock()

    def found_guild(self, master_id: str) -> dict:
        raise NotImplementedError

    def add_member(self, member_id: str, rank: int) -> dict:
        raise NotImplementedError

    def set_rank_permissions(self, rank: int, permissions) -> None:
        raise NotImplementedError

    def set_member_rank(self, actor_id: str, member_id: str, rank: int) -> dict:
        raise NotImplementedError

    def grant_temp(self, grantor_id: str, target_id: str, permissions,
                   expires_at: int, *, now: int = 0) -> dict:
        raise NotImplementedError

    def revoke(self, actor_id: str, grant_id: str) -> bool:
        raise NotImplementedError

    def check(self, member_id: str, permission: str, *, at: int = 0) -> bool:
        raise NotImplementedError

    def expire_grants(self, now: int) -> int:
        raise NotImplementedError

    def active_grants(self, member_id: str, *, at: int = 0) -> list:
        raise NotImplementedError

    def audit_log(self) -> list:
        raise NotImplementedError

    def transfer_master(self, actor_id: str, new_master_id: str,
                        *, now: int = 0) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
