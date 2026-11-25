"""软硬双阈值资源治理。

对外暴露 SoftlimitGraceGovernor。所有时间参数 now 均为调用方传入的
单调秒数，实现不得读取系统墙钟。
"""


class LimitError(Exception):
    """治理器统一异常，带机器可读的 code 与 detail。"""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail if detail is not None else {}
        super().__init__(code)


class SoftlimitGraceGovernor:
    """按资源类别维护软限、硬限、限速状态与告警窗口。"""

    def __init__(self, grace_seconds=30, hysteresis_seconds=60):
        self.grace_seconds = grace_seconds
        self.hysteresis_seconds = hysteresis_seconds

    def define(self, category, soft, hard):
        raise NotImplementedError

    def admit(self, category, amount, now):
        raise NotImplementedError

    def release(self, category, amount, now):
        raise NotImplementedError

    def observe(self, category):
        raise NotImplementedError

    def state(self, category, now):
        raise NotImplementedError

    def alert_count(self, category):
        raise NotImplementedError

    def on_alert(self, callback):
        raise NotImplementedError

    def hard_rejections(self, category):
        raise NotImplementedError

    def soft_throttles(self, category):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, data):
        raise NotImplementedError
