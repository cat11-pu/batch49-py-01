"""seccomp 例外时限放行：工单绑定、到期收回、续期封顶与重启补收。

收回口径：profile 的基础规则（set_profile 传入）永远不被本模块改写，
active 例外只以覆盖层的形式把对应 syscall 的生效决定改成 allow；
revoke / sweep 到期收回仅删除本例外自己的覆盖，基础规则原样保留。
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, fields, replace


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
        self._profiles: dict[str, dict[str, str]] = {}
        self._waivers: dict[str, Waiver] = {}
        self._seq = 0
        self._stats = {
            "requested": 0,
            "approved": 0,
            "renewed": 0,
            "revoked": 0,
            "expired": 0,
            "restored": 0,
        }

    def set_profile(self, profile, rules):
        """整体下发/替换一个 profile 的基础规则；不动任何未到期例外。"""
        with self._lock:
            self._profiles[profile] = {
                str(syscall): str(decision) for syscall, decision in rules.items()
            }

    def remove_profile(self, profile):
        """删除 profile；其名下例外成为孤儿，交由 sweep 清理，不报错。"""
        with self._lock:
            self._profiles.pop(profile, None)

    def effective(self, profile):
        """返回当前生效规则的副本；active 例外以 allow 覆盖基础决定。"""
        with self._lock:
            base = self._profiles.get(profile)
            if base is None:
                return {}
            merged = dict(base)
            for waiver in self._waivers.values():
                if waiver.profile == profile and waiver.state == "active":
                    merged[waiver.syscall] = "allow"
            return merged

    def request(self, profile, syscall, ticket, *, ttl=None, now=0):
        """申请一条 pending 例外，到期时间固定为 now + ttl。"""
        with self._lock:
            if profile not in self._profiles:
                raise WaiverError(
                    "WAIVER_PROFILE_UNKNOWN",
                    {"profile": profile},
                )
            if not syscall or not str(syscall).strip():
                raise WaiverError(
                    "WAIVER_SYSCALL_INVALID",
                    {"syscall": syscall},
                )
            if not ticket or not str(ticket).strip():
                raise WaiverError(
                    "WAIVER_TICKET_REQUIRED",
                    {"ticket": ticket},
                )
            if ttl is None:
                ttl = self.default_ttl
            if ttl <= 0:
                raise WaiverError(
                    "WAIVER_EXPIRY_INVALID",
                    {"ttl": ttl},
                )
            expires_at = now + ttl
            waiver = Waiver(
                waiver_id=self._next_id(),
                profile=profile,
                syscall=str(syscall),
                ticket=str(ticket),
                state="pending",
                expires_at=expires_at,
                renewals=0,
            )
            self._waivers[waiver.waiver_id] = waiver
            self._stats["requested"] += 1
            return waiver

    def approve(self, waiver_id, *, now=0):
        """审批通过：校验 deny 前提、唯一性与容量后让例外生效。"""
        with self._lock:
            waiver = self._require_waiver(waiver_id)
            if waiver.state != "pending":
                raise WaiverError(
                    "WAIVER_STATE_INVALID",
                    {"waiver_id": waiver_id, "state": waiver.state},
                )
            if waiver.expires_at <= now:
                raise WaiverError(
                    "WAIVER_STATE_INVALID",
                    {"waiver_id": waiver_id, "expires_at": waiver.expires_at},
                )
            rules = self._profiles.get(waiver.profile)
            if rules is None or rules.get(waiver.syscall) != "deny":
                raise WaiverError(
                    "WAIVER_SYSCALL_ALLOWED",
                    {"profile": waiver.profile, "syscall": waiver.syscall},
                )
            for other in self._waivers.values():
                if (
                    other.profile == waiver.profile
                    and other.syscall == waiver.syscall
                    and other.state == "active"
                ):
                    raise WaiverError(
                        "WAIVER_DUPLICATE",
                        {"profile": waiver.profile, "syscall": waiver.syscall},
                    )
            active_count = sum(
                1
                for other in self._waivers.values()
                if other.profile == waiver.profile and other.state == "active"
            )
            if active_count >= self.max_active:
                raise WaiverError(
                    "WAIVER_CAPACITY",
                    {"profile": waiver.profile, "max_active": self.max_active},
                )
            activated = replace(waiver, state="active")
            self._waivers[waiver_id] = activated
            self._stats["approved"] += 1
            return activated

    def revoke(self, waiver_id, *, now=0):
        """人工收回；与 sweep 共用同一套 active 状态判定，只能收回一次。"""
        with self._lock:
            waiver = self._require_waiver(waiver_id)
            if waiver.state != "active":
                raise WaiverError(
                    "WAIVER_STATE_INVALID",
                    {"waiver_id": waiver_id, "state": waiver.state},
                )
            return self._terminate(waiver, "revoked", "revoked")

    def renew(self, waiver_id, *, extend, now=0):
        """续期只能向后延长，不刷新工单号，次数受 max_renewals 限制。"""
        with self._lock:
            waiver = self._require_waiver(waiver_id)
            if waiver.state != "active":
                raise WaiverError(
                    "WAIVER_STATE_INVALID",
                    {"waiver_id": waiver_id, "state": waiver.state},
                )
            if extend <= 0:
                raise WaiverError(
                    "WAIVER_RENEW_INVALID",
                    {"waiver_id": waiver_id, "extend": extend},
                )
            if waiver.renewals >= self.max_renewals:
                raise WaiverError(
                    "WAIVER_RENEW_LIMIT",
                    {"waiver_id": waiver_id, "renewals": waiver.renewals},
                )
            renewed = replace(
                waiver,
                expires_at=waiver.expires_at + extend,
                renewals=waiver.renewals + 1,
            )
            self._waivers[waiver_id] = renewed
            self._stats["renewed"] += 1
            return renewed

    def active(self, profile=None):
        """返回 active 例外的只读快照，按 profile、syscall、ID 排序。"""
        with self._lock:
            rows = [
                waiver
                for waiver in self._waivers.values()
                if waiver.state == "active"
                and (profile is None or waiver.profile == profile)
            ]
            return self._sorted(rows)

    def sweep(self, now):
        """到期扫描：本批最多 sweep_batch 条，返回实际收回条数。

        孤儿例外（profile 已删除）一并清理；候选选取与状态翻转在同一把
        锁内完成，扫描期间到达的新申请不可能被本次扫描误收。
        """
        with self._lock:
            due = []
            for waiver in self._waivers.values():
                if waiver.state not in ("pending", "active"):
                    continue
                orphan = waiver.profile not in self._profiles
                if orphan or waiver.expires_at <= now:
                    due.append(waiver)
            due.sort(key=lambda waiver: (waiver.expires_at, waiver.waiver_id))
            for waiver in due[: self.sweep_batch]:
                self._terminate(waiver, "expired", "expired")
            return min(len(due), self.sweep_batch)

    def find(self, *, ticket=None, profile=None, syscall=None):
        """按工单 / profile / syscall 检索，返回只读快照，排序口径固定。"""
        with self._lock:
            rows = []
            for waiver in self._waivers.values():
                if ticket is not None and waiver.ticket != ticket:
                    continue
                if profile is not None and waiver.profile != profile:
                    continue
                if syscall is not None and waiver.syscall != syscall:
                    continue
                rows.append(waiver)
            return self._sorted(rows)

    def snapshot(self):
        """落盘快照：profile 基础规则、例外明细、统计与 ID 序列各一份。"""
        with self._lock:
            return {
                "version": 1,
                "seq": self._seq,
                "profiles": {
                    profile: dict(rules)
                    for profile, rules in self._profiles.items()
                },
                "waivers": [asdict(waiver) for waiver in self._waivers.values()],
                "stats": dict(self._stats),
            }

    def restore(self, state, *, now=0):
        """从快照恢复，并在启动时补收已到期（含孤儿）的例外。"""
        with self._lock:
            self._profiles = {
                profile: dict(rules)
                for profile, rules in state.get("profiles", {}).items()
            }
            waiver_fields = {item.name for item in fields(Waiver)}
            self._waivers = {}
            for row in state.get("waivers", []):
                payload = {key: row[key] for key in waiver_fields if key in row}
                waiver = Waiver(**payload)
                self._waivers[waiver.waiver_id] = waiver
            saved_stats = state.get("stats", {})
            self._stats = {key: int(saved_stats.get(key, 0)) for key in self._stats}
            self._seq = self._restore_seq(state)

            due = [
                waiver
                for waiver in self._waivers.values()
                if waiver.state in ("pending", "active")
                and (
                    waiver.profile not in self._profiles
                    or waiver.expires_at <= now
                )
            ]
            due.sort(key=lambda waiver: (waiver.expires_at, waiver.waiver_id))
            for waiver in due:
                self._terminate(waiver, "expired", "expired")

            restored = len(self._waivers)
            self._stats["restored"] += restored
            return {"restored": restored, "swept": len(due)}

    def stats(self):
        """返回六项计数器的副本。"""
        with self._lock:
            return dict(self._stats)

    def _next_id(self) -> str:
        self._seq += 1
        return "W-%d" % self._seq

    def _restore_seq(self, state: dict) -> int:
        seq = state.get("seq")
        if isinstance(seq, int) and seq > 0:
            return seq
        highest = 0
        for waiver in self._waivers.values():
            if waiver.waiver_id.startswith("W-"):
                try:
                    highest = max(highest, int(waiver.waiver_id[2:]))
                except ValueError:
                    continue
        return highest

    def _require_waiver(self, waiver_id: str) -> Waiver:
        waiver = self._waivers.get(waiver_id)
        if waiver is None:
            raise WaiverError(
                "WAIVER_NOT_FOUND",
                {"waiver_id": waiver_id},
            )
        return waiver

    def _terminate(self, waiver: Waiver, state: str, stat_key: str) -> Waiver:
        terminated = replace(waiver, state=state)
        self._waivers[waiver.waiver_id] = terminated
        self._stats[stat_key] += 1
        return terminated

    @staticmethod
    def _sorted(rows: list[Waiver]) -> list[Waiver]:
        return sorted(
            rows,
            key=lambda waiver: (
                waiver.profile,
                waiver.syscall,
                waiver.waiver_id,
            ),
        )
