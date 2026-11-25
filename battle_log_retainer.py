"""Battle log retainer: bounded ring buffer with pinning, sampling and range export."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class BattleLogError(ValueError):
    """Raised when a battle log operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LogEntry:
    seq: int
    kind: str
    level: int
    payload: dict
    pinned: bool


class BattleLogRetainer:
    """Keep a bounded battle log with pinned critical events and sampling."""

    def __init__(self, *, capacity: int = 64, sample_rate: float = 1.0,
                 clock=None) -> None:
        self.capacity = capacity
        self.sample_rate = sample_rate
        self._clock = clock
        self._lock = threading.RLock()

    def append(self, kind: str, payload: dict | None = None, *, level: int = 0,
               now: int | None = None) -> LogEntry | None:
        raise NotImplementedError

    def pin(self, seq: int) -> LogEntry:
        raise NotImplementedError

    def unpin(self, seq: int) -> bool:
        raise NotImplementedError

    def set_sample_rate(self, rate: float) -> None:
        raise NotImplementedError

    def export_range(self, start_seq: int, end_seq: int) -> list[dict]:
        raise NotImplementedError

    def entries(self) -> list[LogEntry]:
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
