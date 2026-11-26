"""Guild rank authority: inherited permissions, temporary grants and audit trail."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, replace


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
        self._reset_state()

    def _reset_state(self) -> None:
        self._founded = False
        self._master_id = None
        self._members = {}
        self._rank_permissions = {
            rank: set() for rank in range(1, self.max_rank + 1)
        }
        self._grants = {}
        self._next_grant_seq = 1
        self._audit = []
        self._audit_seq = 0
        self._grant_barred = set()

    def _error(self, code: str, detail: object = None) -> GuildError:
        return GuildError(code, detail)

    def _valid_rank(self, rank: object) -> bool:
        return (
            isinstance(rank, int)
            and not isinstance(rank, bool)
            and 1 <= rank <= self.max_rank
        )

    @staticmethod
    def _normalize_permissions(permissions: object) -> tuple:
        if permissions is None:
            return ()
        if isinstance(permissions, str):
            values = (permissions,)
        else:
            try:
                values = tuple(permissions)
            except TypeError as exc:
                raise GuildError(
                    "GUILD_BAD_PERMISSIONS", permissions
                ) from exc
        normalized = set()
        for permission in values:
            if not isinstance(permission, str) or not permission:
                raise GuildError("GUILD_BAD_PERMISSIONS", permission)
            normalized.add(permission)
        return tuple(sorted(normalized))

    def _append_audit(self, action: str, actor: object, detail: dict) -> None:
        self._audit_seq += 1
        self._audit.append({
            "seq": self._audit_seq,
            "action": action,
            "actor": actor,
            "detail": copy.deepcopy(detail),
        })
        if self.max_audit_entries is not None and self.max_audit_entries >= 0:
            overflow = len(self._audit) - self.max_audit_entries
            if overflow > 0:
                del self._audit[:overflow]

    def _rank_permission_set(self, rank: int) -> set:
        permissions = set()
        for current_rank in range(1, rank + 1):
            permissions.update(self._rank_permissions.get(current_rank, ()))
        return permissions

    @staticmethod
    def _grant_is_live(record: GrantRecord, at: int) -> bool:
        return not record.frozen and record.expires_at > at

    def _effective_permissions(self, member_id: str, at: int) -> set:
        permissions = self._rank_permission_set(self._members[member_id])
        for record in self._grants.values():
            if record.target == member_id and self._grant_is_live(record, at):
                permissions.update(record.permissions)
        return permissions

    def _freeze_issued_grants(self, grantor_id: str) -> list:
        frozen_ids = []
        for grant_id, record in list(self._grants.items()):
            if record.grantor == grantor_id and not record.frozen:
                self._grants[grant_id] = replace(record, frozen=True)
                frozen_ids.append(grant_id)
        return frozen_ids

    @staticmethod
    def _grant_sort_key(grant_id: str):
        if isinstance(grant_id, str) and grant_id.startswith("g"):
            suffix = grant_id[1:]
            if suffix.isdigit():
                return (0, int(suffix), grant_id)
        return (1, 0, str(grant_id))

    def found_guild(self, master_id: str) -> dict:
        with self._lock:
            if self._founded:
                raise self._error("GUILD_ALREADY_FOUNDED")
            self._founded = True
            self._master_id = master_id
            self._members = {master_id: self.max_rank}
            self._append_audit("guild_founded", master_id, {
                "master": master_id,
                "rank": self.max_rank,
            })
            return {"master": master_id, "rank": self.max_rank}

    def add_member(self, member_id: str, rank: int) -> dict:
        with self._lock:
            if member_id in self._members:
                raise self._error("GUILD_DUPLICATE", member_id)
            if not self._valid_rank(rank):
                raise self._error("GUILD_BAD_RANK", rank)
            self._members[member_id] = rank
            self._append_audit("member_added", member_id, {"rank": rank})
            return {"member": member_id, "rank": rank}

    def set_rank_permissions(self, rank: int, permissions) -> None:
        with self._lock:
            if not self._valid_rank(rank):
                raise self._error("GUILD_BAD_RANK", rank)
            normalized = self._normalize_permissions(permissions)
            self._rank_permissions[rank] = set(normalized)

    def set_member_rank(self, actor_id: str, member_id: str, rank: int) -> dict:
        with self._lock:
            if not self._valid_rank(rank):
                raise self._error("GUILD_BAD_RANK", rank)
            if actor_id not in self._members or member_id not in self._members:
                raise self._error("GUILD_NO_MEMBER", member_id)
            actor_rank = self._members[actor_id]
            current_rank = self._members[member_id]
            if not actor_rank > current_rank or not rank < actor_rank:
                raise self._error("GUILD_RANK_TOO_LOW", {
                    "actor": actor_id,
                    "member": member_id,
                    "actor_rank": actor_rank,
                    "current_rank": current_rank,
                    "new_rank": rank,
                })

            old_rank = current_rank
            self._members[member_id] = rank
            frozen_grants = []
            if rank < old_rank:
                frozen_grants = self._freeze_issued_grants(member_id)
            self._append_audit("member_rank_changed", actor_id, {
                "member": member_id,
                "old_rank": old_rank,
                "new_rank": rank,
                "frozen_grants": frozen_grants,
            })
            return {"member": member_id, "rank": rank}

    def grant_temp(self, grantor_id: str, target_id: str, permissions,
                   expires_at: int, *, now: int = 0) -> dict:
        with self._lock:
            if target_id not in self._members:
                raise self._error("GUILD_NO_MEMBER", target_id)
            if not isinstance(expires_at, int) or isinstance(expires_at, bool):
                raise self._error("GUILD_BAD_EXPIRY", expires_at)
            if not isinstance(now, int) or isinstance(now, bool):
                raise self._error("GUILD_BAD_EXPIRY", now)
            if expires_at <= now:
                raise self._error("GUILD_BAD_EXPIRY", expires_at)
            if grantor_id not in self._members:
                raise self._error("GUILD_DENIED", grantor_id)
            grantor_rank = self._members[grantor_id]
            if (grantor_id in self._grant_barred
                    and grantor_rank != self.max_rank):
                raise self._error("GUILD_DENIED", grantor_id)

            requested = self._normalize_permissions(permissions)
            held = self._effective_permissions(grantor_id, now)
            granted = tuple(sorted(
                permission for permission in requested if permission in held
            ))
            if not granted:
                raise self._error("GUILD_DENIED", {
                    "grantor": grantor_id,
                    "permissions": requested,
                })

            active_count = sum(
                1 for record in self._grants.values()
                if self._grant_is_live(record, now)
            )
            if active_count >= self.max_temp_grants:
                raise self._error("GUILD_GRANT_CAP", self.max_temp_grants)

            grant_id = f"g{self._next_grant_seq}"
            self._next_grant_seq += 1
            record = GrantRecord(
                grant_id=grant_id,
                grantor=grantor_id,
                target=target_id,
                permissions=granted,
                issued_at=now,
                expires_at=expires_at,
                frozen=False,
            )
            self._grants[grant_id] = record
            self._append_audit("temp_granted", grantor_id, {
                "grant_id": grant_id,
                "target": target_id,
                "permissions": list(granted),
                "issued_at": now,
                "expires_at": expires_at,
            })
            return {
                "grant_id": grant_id,
                "granted": list(granted),
                "expires_at": expires_at,
            }

    def revoke(self, actor_id: str, grant_id: str) -> bool:
        with self._lock:
            record = self._grants.get(grant_id)
            if record is None:
                raise self._error("GUILD_NO_GRANT", grant_id)
            actor_rank = self._members.get(actor_id)
            is_master = actor_rank == self.max_rank
            if actor_id != record.grantor and not is_master:
                raise self._error("GUILD_DENIED", {
                    "actor": actor_id,
                    "grant_id": grant_id,
                })
            del self._grants[grant_id]
            self._append_audit("grant_revoked", actor_id, {
                "grant_id": grant_id,
                "target": record.target,
            })
            return True

    def check(self, member_id: str, permission: str, *, at: int = 0) -> bool:
        with self._lock:
            if member_id not in self._members:
                raise self._error("GUILD_NO_MEMBER", member_id)
            return permission in self._effective_permissions(member_id, at)

    def expire_grants(self, now: int) -> int:
        with self._lock:
            expired_ids = [
                grant_id
                for grant_id, record in self._grants.items()
                if record.expires_at <= now
            ]
            for grant_id in expired_ids:
                del self._grants[grant_id]
            if expired_ids:
                self._append_audit("grants_expired", "system", {
                    "count": len(expired_ids),
                    "grant_ids": expired_ids,
                    "at": now,
                })
            return len(expired_ids)

    def active_grants(self, member_id: str, *, at: int = 0) -> list:
        with self._lock:
            if member_id not in self._members:
                return []
            records = [
                record for record in self._grants.values()
                if record.target == member_id
                and self._grant_is_live(record, at)
            ]
            return sorted(
                records,
                key=lambda record: self._grant_sort_key(record.grant_id),
            )

    def audit_log(self) -> list:
        with self._lock:
            return copy.deepcopy(self._audit)

    def transfer_master(self, actor_id: str, new_master_id: str,
                        *, now: int = 0) -> dict:
        with self._lock:
            if self._members.get(actor_id) != self.max_rank:
                raise self._error("GUILD_DENIED", actor_id)
            if new_master_id not in self._members:
                raise self._error("GUILD_NO_MEMBER", new_master_id)

            previous_master = actor_id
            self._members[new_master_id] = self.max_rank
            if new_master_id != previous_master:
                self._members[previous_master] = self.max_rank - 1
                self._grant_barred.add(previous_master)
                self._grant_barred.discard(new_master_id)
            self._master_id = new_master_id
            self._append_audit("master_transferred", actor_id, {
                "master": new_master_id,
                "previous_master": previous_master,
                "at": now,
            })
            return {
                "master": new_master_id,
                "previous_master": previous_master,
            }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "founded": self._founded,
                "master_id": self._master_id,
                "max_rank": self.max_rank,
                "max_temp_grants": self.max_temp_grants,
                "max_audit_entries": self.max_audit_entries,
                "members": copy.deepcopy(self._members),
                "rank_permissions": {
                    str(rank): sorted(self._rank_permissions.get(rank, set()))
                    for rank in range(1, self.max_rank + 1)
                },
                "grants": {
                    grant_id: {
                        "grant_id": record.grant_id,
                        "grantor": record.grantor,
                        "target": record.target,
                        "permissions": list(record.permissions),
                        "issued_at": record.issued_at,
                        "expires_at": record.expires_at,
                        "frozen": record.frozen,
                    }
                    for grant_id, record in self._grants.items()
                },
                "next_grant_seq": self._next_grant_seq,
                "audit_seq": self._audit_seq,
                "audit": copy.deepcopy(self._audit),
                "grant_barred": sorted(self._grant_barred),
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                candidate = self._parse_snapshot(state)
            except GuildError:
                raise
            except Exception as exc:
                raise self._error("GUILD_SNAPSHOT_INVALID", str(exc)) from exc
            self._founded = candidate["founded"]
            self._master_id = candidate["master_id"]
            self.max_rank = candidate["max_rank"]
            self.max_temp_grants = candidate["max_temp_grants"]
            self.max_audit_entries = candidate["max_audit_entries"]
            self._members = candidate["members"]
            self._rank_permissions = candidate["rank_permissions"]
            self._grants = candidate["grants"]
            self._next_grant_seq = candidate["next_grant_seq"]
            self._audit = candidate["audit"]
            self._audit_seq = candidate["audit_seq"]
            self._grant_barred = candidate["grant_barred"]

    def _parse_snapshot(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise self._error("GUILD_SNAPSHOT_INVALID", "state must be a dict")
        if "members" not in state or "grants" not in state:
            raise self._error("GUILD_SNAPSHOT_INVALID", "missing members/grants")

        max_rank = self._parse_positive_int(
            state.get("max_rank", self.max_rank), "max_rank"
        )
        max_temp_grants = self._parse_nonnegative_int(
            state.get("max_temp_grants", self.max_temp_grants),
            "max_temp_grants",
        )
        max_audit_entries = self._parse_nonnegative_int(
            state.get("max_audit_entries", self.max_audit_entries),
            "max_audit_entries",
        )

        raw_members = state["members"]
        if not isinstance(raw_members, dict):
            raise self._error("GUILD_SNAPSHOT_INVALID", "members must be a dict")
        members = {}
        for member_id, member_state in raw_members.items():
            if not isinstance(member_id, str) or not member_id:
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad member id")
            if isinstance(member_state, dict):
                rank = member_state.get("rank")
            else:
                rank = member_state
            if not isinstance(rank, int) or isinstance(rank, bool):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad member rank")
            if not 1 <= rank <= max_rank:
                raise self._error("GUILD_SNAPSHOT_INVALID", "rank out of range")
            members[member_id] = rank

        raw_permissions = state.get(
            "rank_permissions", state.get("permissions", {})
        )
        if not isinstance(raw_permissions, dict):
            raise self._error("GUILD_SNAPSHOT_INVALID", "permissions must be a dict")
        rank_permissions = {rank: set() for rank in range(1, max_rank + 1)}
        for raw_rank, permission_values in raw_permissions.items():
            rank = self._parse_rank_key(raw_rank, max_rank)
            permissions = self._parse_snapshot_permissions(permission_values)
            rank_permissions[rank] = set(permissions)

        raw_grants = state["grants"]
        if not isinstance(raw_grants, dict):
            raise self._error("GUILD_SNAPSHOT_INVALID", "grants must be a dict")
        grants = {}
        for grant_key, raw_record in raw_grants.items():
            record = self._parse_grant_record(
                grant_key, raw_record, members
            )
            grants[record.grant_id] = record

        audit = self._parse_audit(state.get("audit", state.get("audit_log", [])))
        raw_audit_seq = state.get("audit_seq")
        if raw_audit_seq is None:
            audit_seq = max((entry["seq"] for entry in audit), default=0)
        else:
            audit_seq = self._parse_nonnegative_int(
                raw_audit_seq, "audit_seq"
            )
        if audit and audit_seq < max(entry["seq"] for entry in audit):
            raise self._error("GUILD_SNAPSHOT_INVALID", "audit_seq is behind log")

        next_grant_seq = state.get(
            "next_grant_seq", state.get("grant_seq")
        )
        if next_grant_seq is None:
            numeric_ids = [
                int(grant_id[1:])
                for grant_id in grants
                if grant_id.startswith("g") and grant_id[1:].isdigit()
            ]
            next_grant_seq = max(numeric_ids, default=0) + 1
        else:
            next_grant_seq = self._parse_positive_int(
                next_grant_seq, "next_grant_seq"
            )

        raw_barred = state.get("grant_barred", state.get("barred_grantors", []))
        if not isinstance(raw_barred, (list, tuple, set, frozenset)):
            raise self._error("GUILD_SNAPSHOT_INVALID", "grant_barred bad type")
        grant_barred = set()
        for member_id in raw_barred:
            if not isinstance(member_id, str):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad barred member")
            grant_barred.add(member_id)

        master_id = state.get("master_id", state.get("master"))
        if master_id is not None and not isinstance(master_id, str):
            raise self._error("GUILD_SNAPSHOT_INVALID", "bad master id")
        founded = state.get("founded", bool(members))
        if not isinstance(founded, bool):
            raise self._error("GUILD_SNAPSHOT_INVALID", "founded must be bool")

        return {
            "founded": founded,
            "master_id": master_id,
            "max_rank": max_rank,
            "max_temp_grants": max_temp_grants,
            "max_audit_entries": max_audit_entries,
            "members": members,
            "rank_permissions": rank_permissions,
            "grants": grants,
            "next_grant_seq": next_grant_seq,
            "audit": audit,
            "audit_seq": audit_seq,
            "grant_barred": grant_barred,
        }

    @staticmethod
    def _parse_positive_int(value: object, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise GuildError("GUILD_SNAPSHOT_INVALID", f"{field} must be positive int")
        return value

    @staticmethod
    def _parse_nonnegative_int(value: object, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise GuildError("GUILD_SNAPSHOT_INVALID", f"{field} must be nonnegative int")
        return value

    @classmethod
    def _parse_rank_key(cls, raw_rank: object, max_rank: int) -> int:
        if isinstance(raw_rank, bool):
            raise GuildError("GUILD_SNAPSHOT_INVALID", "bad rank key")
        if isinstance(raw_rank, int):
            rank = raw_rank
        elif isinstance(raw_rank, str) and raw_rank.isdigit():
            rank = int(raw_rank)
        else:
            raise GuildError("GUILD_SNAPSHOT_INVALID", "bad rank key")
        if not 1 <= rank <= max_rank:
            raise GuildError("GUILD_SNAPSHOT_INVALID", "rank key out of range")
        return rank

    @classmethod
    def _parse_snapshot_permissions(cls, value: object) -> tuple:
        if isinstance(value, str) or not isinstance(
            value, (list, tuple, set, frozenset)
        ):
            raise GuildError("GUILD_SNAPSHOT_INVALID", "bad permissions")
        permissions = []
        for permission in value:
            if not isinstance(permission, str) or not permission:
                raise GuildError("GUILD_SNAPSHOT_INVALID", "bad permission")
            permissions.append(permission)
        return tuple(sorted(set(permissions)))

    def _parse_grant_record(self, key: object, raw_record: object,
                            members: dict) -> GrantRecord:
        if isinstance(raw_record, GrantRecord):
            record = raw_record
        elif isinstance(raw_record, dict):
            try:
                grant_id = raw_record["grant_id"]
                grantor = raw_record["grantor"]
                target = raw_record["target"]
                raw_permissions = raw_record["permissions"]
                issued_at = raw_record["issued_at"]
                expires_at = raw_record["expires_at"]
                frozen = raw_record["frozen"]
            except KeyError as exc:
                raise self._error(
                    "GUILD_SNAPSHOT_INVALID", "grant record incomplete"
                ) from exc
            permissions = self._parse_snapshot_permissions(raw_permissions)
            if not isinstance(grant_id, str) or not isinstance(grantor, str):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad grant identities")
            if not isinstance(target, str) or not isinstance(frozen, bool):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad grant field types")
            if not isinstance(issued_at, int) or isinstance(issued_at, bool):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad issued_at")
            if not isinstance(expires_at, int) or isinstance(expires_at, bool):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad expires_at")
            if expires_at <= issued_at:
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad grant interval")
            record = GrantRecord(
                grant_id=grant_id,
                grantor=grantor,
                target=target,
                permissions=permissions,
                issued_at=issued_at,
                expires_at=expires_at,
                frozen=frozen,
            )
        else:
            raise self._error("GUILD_SNAPSHOT_INVALID", "bad grant record")

        if key != record.grant_id:
            raise self._error("GUILD_SNAPSHOT_INVALID", "grant id mismatch")
        if record.grantor not in members or record.target not in members:
            raise self._error("GUILD_SNAPSHOT_INVALID", "grant member missing")
        return record

    def _parse_audit(self, raw_audit: object) -> list:
        if not isinstance(raw_audit, list):
            raise self._error("GUILD_SNAPSHOT_INVALID", "audit must be a list")
        audit = []
        previous_seq = 0
        for entry in raw_audit:
            if not isinstance(entry, dict):
                raise self._error("GUILD_SNAPSHOT_INVALID", "audit entry must be dict")
            try:
                seq = entry["seq"]
                action = entry["action"]
                actor = entry["actor"]
                detail = entry["detail"]
            except KeyError as exc:
                raise self._error(
                    "GUILD_SNAPSHOT_INVALID", "audit entry incomplete"
                ) from exc
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= 0:
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad audit seq")
            if seq <= previous_seq:
                raise self._error("GUILD_SNAPSHOT_INVALID", "audit seq not ordered")
            if not isinstance(action, str) or not action:
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad audit action")
            if not isinstance(actor, str) or not isinstance(detail, dict):
                raise self._error("GUILD_SNAPSHOT_INVALID", "bad audit actor/detail")
            previous_seq = seq
            audit.append(copy.deepcopy(entry))
        return audit
