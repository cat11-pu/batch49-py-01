"""多源审计流序号续接：缺口区间、补录窗口、消费游标与快照恢复。"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AuditSeqError(ValueError):
    """审计流序号操作被拒绝时抛出。"""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Gap:
    """一个尚未闭合的序号缺口区间。"""

    source: str
    start: int
    end: int
    missing: int
    discovered_at: int
    status: str


class AuditSequenceStitcher:
    """按来源维护单调序号、缺口区间、补录与消费游标。"""

    def __init__(self, *, backfill_window: int = 1000,
                 buffer_cap: int = 64) -> None:
        self.backfill_window = backfill_window
        self.buffer_cap = buffer_cap
        self._lock = threading.RLock()

    def ingest(self, source, seq, body, *, ts=0):
        raise NotImplementedError

    def backfill(self, source, seq, body, *, ts=0):
        raise NotImplementedError

    def gaps(self, source, *, now=None):
        raise NotImplementedError

    def close_gap(self, source, start):
        raise NotImplementedError

    def cursor(self, source):
        raise NotImplementedError

    def ack(self, source, seq):
        raise NotImplementedError

    def resume(self, source):
        raise NotImplementedError

    def events(self, source):
        raise NotImplementedError

    def generation(self, source):
        raise NotImplementedError

    def retire(self, source):
        raise NotImplementedError

    def sources(self):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
