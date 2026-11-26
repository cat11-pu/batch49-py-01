"""文件锁租约与 fencing。

对外暴露 FileLockLeaseFencer。锁状态以 lock_path 指向的 JSON 文件为准，
受保护的数据追加到 lock_path + ".data"。所有时间 now 由调用方传入。
"""

import contextlib
import fcntl
import json
import os
import tempfile
import threading


LOCK_FILE_SUFFIX = ".flock"
DATA_FILE_SUFFIX = ".data"


class LeaseError(Exception):
    """租约器统一异常，带机器可读的 code 与 detail。"""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail if detail is not None else {}
        super().__init__(code)


class FileLockLeaseFencer:
    """用文件锁加租约管理独占写入，并用单调递增的 fencing token 拦截旧持有者。"""

    def __init__(self, lock_path, lease_seconds=30, min_lease=5):
        if lease_seconds < min_lease:
            raise LeaseError(
                "lease_too_short",
                {
                    "lease_seconds": lease_seconds,
                    "min_lease": min_lease,
                },
            )
        self.lock_path = lock_path
        self.lease_seconds = lease_seconds
        self.min_lease = min_lease
        self.data_path = lock_path + DATA_FILE_SUFFIX
        self._gate_path = lock_path + LOCK_FILE_SUFFIX
        self._thread_lock = threading.RLock()
        self._token = 0
        self._lease = None
        self._history = []
        self._load_persisted_state()

    # ------------------------------------------------------------------
    # 持久化与临界区
    # ------------------------------------------------------------------

    def _load_persisted_state(self):
        """进程重启后从锁文件恢复，token 只增不减，绝不归零。"""
        if not os.path.exists(self.lock_path):
            return
        state = self._read_state_file()
        persisted_token = int(state.get("token", 0) or 0)
        if persisted_token > self._token:
            self._token = persisted_token
        lease = state.get("lease")
        if isinstance(lease, dict):
            self._lease = dict(lease)
        history = state.get("history")
        if isinstance(history, list):
            self._history = [dict(event) for event in history]

    def _read_state_file(self):
        try:
            with open(self.lock_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as exc:
            raise LeaseError(
                "corrupt_state",
                {"lock_path": self.lock_path, "reason": str(exc)},
            )

    @contextlib.contextmanager
    def _critical_section(self):
        """同一临界区内串行化：进程内用 RLock，多进程用 flock。

        文件系统不支持文件锁时直接拒绝，绝不降级为无锁运行。
        """
        with self._thread_lock:
            parent = os.path.dirname(os.path.abspath(self.lock_path))
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                raise LeaseError(
                    "lock_unavailable",
                    {"lock_path": self._gate_path, "reason": str(exc)},
                )
            try:
                gate = open(self._gate_path, "a+b")
            except OSError as exc:
                raise LeaseError(
                    "lock_unavailable",
                    {"lock_path": self._gate_path, "reason": str(exc)},
                )
            try:
                try:
                    fcntl.flock(gate.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    raise LeaseError(
                        "lock_unavailable",
                        {"lock_path": self._gate_path, "reason": str(exc)},
                    )
                self._reconcile_with_disk()
                yield
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(gate.fileno(), fcntl.LOCK_UN)
                gate.close()

    def _reconcile_with_disk(self):
        """进入临界区后与磁盘状态对齐，兼容其它进程的授予动作。"""
        if not os.path.exists(self.lock_path):
            return
        state = self._read_state_file()
        disk_token = int(state.get("token", 0) or 0)
        if disk_token > self._token:
            self._token = disk_token
        disk_lease = state.get("lease")
        if isinstance(disk_lease, dict):
            self._lease = dict(disk_lease)
        else:
            self._lease = None
        disk_history = state.get("history")
        if isinstance(disk_history, list) and len(disk_history) > len(self._history):
            self._history = [dict(event) for event in disk_history]

    def _persist(self):
        """原子写回 token、租约与历史，避免半截 JSON。"""
        state = {
            "token": self._token,
            "lease": dict(self._lease) if self._lease is not None else None,
            "history": [dict(event) for event in self._history],
        }
        parent = os.path.dirname(os.path.abspath(self.lock_path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=os.path.basename(self.lock_path) + ".",
            suffix=".tmp",
            dir=parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.lock_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise

    def _has_live_lease(self, now):
        return self._lease is not None and self._lease["expires_at"] > now

    def _lease_snapshot(self):
        return dict(self._lease) if self._lease is not None else None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def acquire(self, owner, now):
        with self._critical_section():
            if self._has_live_lease(now):
                raise LeaseError(
                    "held",
                    {
                        "owner": self._lease["owner"],
                        "expires_at": self._lease["expires_at"],
                    },
                )
            preempted = self._lease is not None
            self._token += 1
            expires_at = now + self.lease_seconds
            self._lease = {
                "owner": owner,
                "token": self._token,
                "expires_at": expires_at,
            }
            reason = "preempted" if preempted else "acquired"
            self._history.append(
                {
                    "token": self._token,
                    "owner": owner,
                    "time": now,
                    "reason": reason,
                }
            )
            self._persist()
            return {
                "owner": owner,
                "token": self._token,
                "expires_at": expires_at,
                "reason": reason,
            }

    def renew(self, owner, token, now):
        with self._critical_section():
            if self._lease is None:
                raise LeaseError("no_lease", {})
            if self._lease["owner"] != owner:
                raise LeaseError(
                    "not_owner",
                    {"owner": owner, "holder": self._lease["owner"]},
                )
            if self._lease["token"] != token:
                raise LeaseError(
                    "bad_token",
                    {"token": token, "current_token": self._lease["token"]},
                )
            self._lease["expires_at"] = now + self.lease_seconds
            self._persist()
            return self._lease_snapshot()

    def release(self, owner, token):
        with self._critical_section():
            if self._lease is None:
                raise LeaseError("no_lease", {})
            if self._lease["owner"] != owner:
                raise LeaseError(
                    "not_owner",
                    {"owner": owner, "holder": self._lease["owner"]},
                )
            if self._lease["token"] != token:
                raise LeaseError(
                    "bad_token",
                    {"token": token, "current_token": self._lease["token"]},
                )
            self._lease = None
            self._persist()

    def guarded_write(self, owner, token, data):
        with self._critical_section():
            current = self._lease
            if (
                current is None
                or current["owner"] != owner
                or current["token"] != token
            ):
                raise LeaseError(
                    "stale_token",
                    {"owner": owner, "token": token},
                )
            raw = (data + "\n").encode("utf-8")
            parent = os.path.dirname(os.path.abspath(self.data_path))
            os.makedirs(parent, exist_ok=True)
            with open(self.data_path, "ab") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            return len(raw)

    def lease(self):
        with self._thread_lock:
            return self._lease_snapshot()

    def token(self):
        with self._thread_lock:
            return self._token

    def remaining(self, now):
        with self._thread_lock:
            if self._lease is None:
                return None
            return self._lease["expires_at"] - now

    def is_expired(self, now):
        with self._thread_lock:
            return not self._has_live_lease(now)

    def history(self):
        with self._thread_lock:
            return [dict(event) for event in self._history]

    def snapshot(self):
        with self._thread_lock:
            return {
                "token": self._token,
                "lease": self._lease_snapshot(),
                "history": [dict(event) for event in self._history],
            }

    def restore(self, data):
        with self._critical_section():
            restored_token = int(data.get("token", 0) or 0)
            if restored_token > self._token:
                self._token = restored_token
            lease = data.get("lease")
            self._lease = dict(lease) if isinstance(lease, dict) else None
            history = data.get("history")
            if isinstance(history, list):
                self._history = [dict(event) for event in history]
            self._persist()
