"""文件锁租约与 fencing。

对外暴露 FileLockLeaseFencer。锁状态以 lock_path 指向的 JSON 文件为准，
受保护的数据追加到 lock_path + ".data"。所有时间 now 由调用方传入。
"""


class LeaseError(Exception):
    """租约器统一异常，带机器可读的 code 与 detail。"""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail if detail is not None else {}
        super().__init__(code)


class FileLockLeaseFencer:
    """用文件锁加租约管理独占写入，并用单调递增的 fencing token 拦截旧持有者。"""

    def __init__(self, lock_path, lease_seconds=30, min_lease=5):
        self.lock_path = lock_path
        self.lease_seconds = lease_seconds
        self.min_lease = min_lease

    def acquire(self, owner, now):
        raise NotImplementedError

    def renew(self, owner, token, now):
        raise NotImplementedError

    def release(self, owner, token):
        raise NotImplementedError

    def guarded_write(self, owner, token, data):
        raise NotImplementedError

    def lease(self):
        raise NotImplementedError

    def token(self):
        raise NotImplementedError

    def remaining(self, now):
        raise NotImplementedError

    def is_expired(self, now):
        raise NotImplementedError

    def history(self):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, data):
        raise NotImplementedError
