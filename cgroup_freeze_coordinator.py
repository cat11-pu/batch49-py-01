"""cgroup 冻结/解冻协调：四态状态机、用量快照、意图日志与整批原子回滚。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Optional

STATES = ("thawed", "freezing", "frozen", "thawing")

ACTIONS = ("freeze", "thaw", "rollback", "recover")


class FreezeError(ValueError):
    """冻结/解冻被拒绝时抛出。"""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GroupView:
    """单个 cgroup 的对外视图。"""

    path: str
    state: str
    frozen_ms: int
    procs: int
    quota_cpu: int
    quota_mem: int


@dataclass(frozen=True)
class UsageView:
    """用量记账视图。"""

    used_ms: int
    frozen_ms: int


@dataclass
class _Group:
    path: str
    parent: Optional[str]
    state: str
    frozen_ms: int
    running: int
    exiting: int
    zombies: int
    forking: int
    quota_cpu: int
    quota_mem: int
    used_ms: int = 0
    freeze_start: Optional[int] = None
    last_now: int = 0

    # rate 使用“上一次采样 -> 最近一次采样”的窗口。冻结期间不允许
    # 采样，因此窗口内被冻结的时长单独累计，在计算分母时扣除。
    rate_prev_used: int = 0
    rate_prev_ts: int = 0
    rate_cur_used: int = 0
    rate_cur_ts: int = 0
    rate_frozen_interval: int = 0
    rate_frozen_pending: int = 0


class CgroupFreezeCoordinator:
    """批量冻结/解冻协调器，维护状态、用量与意图日志。"""

    def __init__(self, *, max_batch: int = 32, max_procs: int = 2048,
                 retry_limit: int = 3) -> None:
        self.max_batch = max_batch
        self.max_procs = max_procs
        self.retry_limit = retry_limit
        self._lock = threading.RLock()
        self._batch_lock = threading.RLock()
        self._groups: dict[str, _Group] = {}
        self._journal: list[dict[str, object]] = []
        self._stats = {
            "freeze_events": 0,
            "thaw_events": 0,
            "zombie_warnings": 0,
            "fork_retries": 0,
            "rollbacks": 0,
            "recoveries": 0,
        }

    # ------------------------------------------------------------------
    # 内部辅助
    def _get(self, path) -> _Group:
        group = self._groups.get(path)
        if group is None:
            raise FreezeError("FREEZE_UNKNOWN_GROUP", path)
        return group

    @staticmethod
    def _total_procs(group: _Group) -> int:
        return (group.running + group.exiting + group.zombies +
                group.forking)

    @staticmethod
    def _view(group: _Group) -> GroupView:
        return GroupView(
            path=group.path,
            state=group.state,
            frozen_ms=group.frozen_ms,
            procs=CgroupFreezeCoordinator._total_procs(group),
            quota_cpu=group.quota_cpu,
            quota_mem=group.quota_mem,
        )

    def _log(self, path: str, phase: str, ts) -> None:
        # 意图必须先于状态变化落盘；journal 只暴露 path/phase/ts。
        self._journal.append({"path": path, "phase": phase, "ts": ts})

    @staticmethod
    def _restore(group: _Group, snapshot: _Group) -> None:
        for name, value in vars(snapshot).items():
            setattr(group, name, value)

    def _check_proc_cap(self, group: _Group) -> None:
        if self._total_procs(group) > self.max_procs:
            raise FreezeError("FREEZE_PROC_LIMIT_EXCEEDED", group.path)

    def _freeze_locked(self, group: _Group, now) -> GroupView:
        if group.state == "frozen":
            # 幂等：不重设起点、不重复计数、不写新日志。
            return self._view(group)
        if group.state in ("freezing", "thawing"):
            raise FreezeError("FREEZE_BUSY", group.path)
        if group.state != "thawed":
            raise FreezeError("FREEZE_BUSY", group.path)

        self._check_proc_cap(group)
        if group.exiting:
            raise FreezeError("FREEZE_PROCS_EXITING", group.path)
        if group.forking:
            for _ in range(max(0, int(self.retry_limit))):
                self._stats["fork_retries"] += 1
            raise FreezeError("FREEZE_FORK_RETRY_EXHAUSTED", group.path)

        if group.zombies:
            self._stats["zombie_warnings"] += group.zombies

        self._log(group.path, "freezing", now)
        group.state = "freezing"

        group.freeze_start = now
        group.last_now = now
        self._log(group.path, "frozen", now)
        group.state = "frozen"
        self._stats["freeze_events"] += 1
        return self._view(group)

    def _finish_thaw_locked(self, group: _Group, now) -> None:
        if group.freeze_start is not None:
            duration = max(0, now - group.freeze_start)
            group.frozen_ms += duration
            group.rate_frozen_pending += duration
        group.freeze_start = None
        group.last_now = now
        self._log(group.path, "thawed", now)
        group.state = "thawed"
        self._stats["thaw_events"] += 1

    def _thaw_locked(self, group: _Group, now, *, finalize: bool) -> GroupView:
        if group.state == "thawed":
            return self._view(group)
        if group.state == "thawing":
            if finalize:
                self._finish_thaw_locked(group, now)
            return self._view(group)
        if group.state != "frozen":
            raise FreezeError("FREEZE_BUSY", group.path)

        self._log(group.path, "thawing", now)
        group.state = "thawing"
        group.last_now = now
        if finalize:
            self._finish_thaw_locked(group, now)
        return self._view(group)

    def _rollback_freeze_locked(self, group: _Group, snapshot: _Group,
                                now) -> None:
        """撤销本批次刚刚完成、之前并非 frozen 的冻结。"""
        self._log(group.path, "thawing", now)
        group.state = "thawing"
        self._log(group.path, "thawed", now)
        self._restore(group, snapshot)
        group.state = "thawed"
        group.freeze_start = None
        # 题面要求被批量回滚的组冻结时长归零。
        group.frozen_ms = 0
        self._stats["freeze_events"] = max(
            0, self._stats["freeze_events"] - 1)
        self._stats["zombie_warnings"] = max(
            0, self._stats["zombie_warnings"] - snapshot.zombies)
        self._stats["rollbacks"] += 1

    def _rollback_thaw_locked(self, group: _Group, snapshot: _Group,
                              now) -> None:
        """把批次中已经解冻的组恢复成冻结前的原状。"""
        self._log(group.path, "freezing", now)
        group.state = "freezing"
        self._log(group.path, "frozen", now)
        self._restore(group, snapshot)
        self._stats["thaw_events"] = max(0, self._stats["thaw_events"] - 1)
        self._stats["rollbacks"] += 1

    # ------------------------------------------------------------------
    # 公开接口
    def add_group(self, path, *, parent=None, procs=0, quota_cpu=100,
                  quota_mem=1024, now=0):
        with self._lock:
            if path in self._groups:
                raise FreezeError("FREEZE_DUPLICATE_GROUP", path)
            if procs < 0:
                raise FreezeError("FREEZE_INVALID_PROCESS_COUNT", path)
            if procs > self.max_procs:
                raise FreezeError("FREEZE_PROC_LIMIT_EXCEEDED", path)
            group = _Group(
                path=path,
                parent=parent,
                state="thawed",
                frozen_ms=0,
                running=procs,
                exiting=0,
                zombies=0,
                forking=0,
                quota_cpu=quota_cpu,
                quota_mem=quota_mem,
                used_ms=0,
                freeze_start=None,
                last_now=now,
                rate_prev_used=0,
                rate_prev_ts=now,
                rate_cur_used=0,
                rate_cur_ts=now,
            )
            self._groups[path] = group
            return self._view(group)

    def set_processes(self, path, *, running, exiting=0, zombies=0,
                      forking=0):
        with self._lock:
            group = self._get(path)
            counts = (running, exiting, zombies, forking)
            if any(value < 0 for value in counts):
                raise FreezeError("FREEZE_INVALID_PROCESS_COUNT", path)
            if sum(counts) > self.max_procs:
                raise FreezeError("FREEZE_PROC_LIMIT_EXCEEDED", path)
            group.running = running
            group.exiting = exiting
            group.zombies = zombies
            group.forking = forking
            return self._view(group)

    def freeze(self, path, *, now):
        with self._lock:
            return self._freeze_locked(self._get(path), now)

    def thaw(self, path, *, now, finalize=True):
        with self._lock:
            return self._thaw_locked(self._get(path), now, finalize=finalize)

    def status(self, path):
        with self._lock:
            return self._view(self._get(path))

    def batch_freeze(self, paths, *, now):
        paths = list(paths)
        if len(paths) > self.max_batch:
            raise FreezeError(
                "FREEZE_BATCH_TOO_LARGE",
                {"paths": paths, "max_batch": self.max_batch},
            )

        committed: list[tuple[str, _Group]] = []
        frozen: list[str] = []
        with self._batch_lock:
            for path in paths:
                try:
                    with self._lock:
                        group = self._get(path)
                        before = replace(group)
                        self._freeze_locked(group, now)
                        frozen.append(path)
                        if before.state != "frozen":
                            committed.append((path, before))
                except FreezeError:
                    rolled_back = self._rollback_freezes(committed, now)
                    raise FreezeError(
                        "FREEZE_BATCH_FAILED",
                        {"failed": path, "rolled_back": rolled_back},
                    ) from None
        return {"frozen": frozen, "rolled_back": []}

    def _rollback_freezes(self, committed, now) -> list[str]:
        rolled_back: list[str] = []
        for path, snapshot in committed:
            with self._lock:
                group = self._groups.get(path)
                if group is None:
                    continue
                self._rollback_freeze_locked(group, snapshot, now)
            rolled_back.append(path)
        return rolled_back

    def batch_thaw(self, paths, *, now):
        paths = list(paths)
        if len(paths) > self.max_batch:
            raise FreezeError(
                "FREEZE_BATCH_TOO_LARGE",
                {"paths": paths, "max_batch": self.max_batch},
            )

        committed: list[tuple[str, _Group]] = []
        thawed: list[str] = []
        with self._batch_lock:
            for path in paths:
                try:
                    with self._lock:
                        group = self._get(path)
                        if group.state != "frozen":
                            raise FreezeError("FREEZE_BUSY", path)
                        before = replace(group)
                        self._thaw_locked(group, now, finalize=True)
                        committed.append((path, before))
                        thawed.append(path)
                except FreezeError:
                    rolled_back = self._rollback_thaws(committed, now)
                    raise FreezeError(
                        "FREEZE_BATCH_FAILED",
                        {"failed": path, "rolled_back": rolled_back},
                    ) from None
        return {"thawed": thawed, "rolled_back": []}

    def _rollback_thaws(self, committed, now) -> list[str]:
        rolled_back: list[str] = []
        for path, snapshot in committed:
            with self._lock:
                group = self._groups.get(path)
                if group is None:
                    continue
                self._rollback_thaw_locked(group, snapshot, now)
            rolled_back.append(path)
        return rolled_back

    def resize(self, path, *, quota_cpu=None, quota_mem=None, now=0):
        with self._lock:
            group = self._get(path)
            if group.state != "thawed":
                raise FreezeError("FREEZE_RESIZE_FORBIDDEN", path)
            if quota_cpu is not None:
                group.quota_cpu = quota_cpu
            if quota_mem is not None:
                group.quota_mem = quota_mem
            group.last_now = now
            return self._view(group)

    def usage(self, path):
        with self._lock:
            group = self._get(path)
            return {"used_ms": group.used_ms, "frozen_ms": group.frozen_ms}

    def rate(self, path, *, now):
        with self._lock:
            group = self._get(path)
            if group.state != "thawed":
                return 0.0
            used_delta = group.rate_cur_used - group.rate_prev_used
            end_ts = max(group.rate_cur_ts, now)
            frozen = (group.rate_frozen_interval +
                      group.rate_frozen_pending)
            elapsed = end_ts - group.rate_prev_ts - frozen
            if used_delta <= 0 or elapsed <= 0:
                return 0.0
            return used_delta / elapsed

    def sample_usage(self, path, *, used_ms, now):
        with self._lock:
            group = self._get(path)
            if group.state != "thawed":
                raise FreezeError("FREEZE_SAMPLE_REJECTED", path)
            if used_ms < group.used_ms:
                # 用量计数器只允许单调递增，回落视为非法输入。
                raise FreezeError("FREEZE_SAMPLE_REJECTED", path)

            group.used_ms = used_ms
            group.last_now = now
            group.rate_prev_used = group.rate_cur_used
            group.rate_prev_ts = group.rate_cur_ts
            group.rate_cur_used = used_ms
            group.rate_cur_ts = now
            group.rate_frozen_interval = group.rate_frozen_pending
            group.rate_frozen_pending = 0
            return {"used_ms": group.used_ms, "frozen_ms": group.frozen_ms}

    def forget(self, path):
        with self._lock:
            if path not in self._groups:
                return False
            del self._groups[path]
            return True

    def journal(self):
        with self._lock:
            return [dict(entry) for entry in self._journal]

    def recover(self, entries):
        with self._lock:
            last: dict[str, tuple[str, object]] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                path = entry.get("path")
                phase = entry.get("phase")
                ts = entry.get("ts", 0)
                if path not in self._groups or phase not in STATES:
                    continue
                last[path] = (phase, ts)

            states: dict[str, str] = {}
            converged = 0
            for path, (phase, ts) in last.items():
                group = self._groups[path]
                if phase == "freezing":
                    # 冻结尚未完成，恢复时直接回到 thawed。
                    group.state = "thawed"
                    group.freeze_start = None
                    converged += 1
                elif phase == "thawing":
                    # 解冻已经开始，按最终解冻处理并补齐冻结时长。
                    if group.freeze_start is not None:
                        duration = max(0, ts - group.freeze_start)
                        group.frozen_ms += duration
                        group.rate_frozen_pending += duration
                    group.state = "thawed"
                    group.freeze_start = None
                    converged += 1
                elif phase == "frozen":
                    group.state = "frozen"
                    if group.freeze_start is None:
                        group.freeze_start = ts
                else:  # thawed
                    if (group.state in ("frozen", "thawing") and
                            group.freeze_start is not None):
                        duration = max(0, ts - group.freeze_start)
                        group.frozen_ms += duration
                        group.rate_frozen_pending += duration
                    group.state = "thawed"
                    group.freeze_start = None
                states[path] = group.state

            self._stats["recoveries"] += converged
            return {"converged": converged, "states": states}

    def stats(self):
        with self._lock:
            return dict(self._stats)
