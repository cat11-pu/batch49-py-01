"""PTP slave servo: offset filtering, stepping, frequency tuning, and holdover."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PtpError(ValueError):
    """Raised when a PTP sample, delay, step, or restore is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ServoResult:
    offset_ns: int
    mode: str
    freq_ppb: int
    step_count: int


@dataclass(frozen=True)
class HoldoverResult:
    offset_ns: int
    available: bool
    elapsed_ns: int


class PtpSlaveServo:
    """Own the sample window, link delay, and local clock corrections."""

    def __init__(self, *, window: int = 8, step_threshold_ns: int = 1_000_000,
                 max_freq_ppb: int = 1000, slew_divisor: int = 100,
                 max_steps: int = 4, holdover_limit_ns: int = 10_000_000,
                 delay_age: int = 5) -> None:
        self.window = window
        self.step_threshold_ns = step_threshold_ns
        self.max_freq_ppb = max_freq_ppb
        self.slew_divisor = slew_divisor
        self.max_steps = max_steps
        self.holdover_limit_ns = holdover_limit_ns
        self.delay_age = delay_age
        self._lock = threading.RLock()

    def feed_sync(self, seq: int, master_send_ns: int, slave_recv_ns: int) -> bool:
        raise NotImplementedError

    def feed_delay_req(self, seq: int, request_send_ns: int,
                       response_recv_ns: int) -> bool:
        raise NotImplementedError

    def offset_estimate(self) -> int:
        raise NotImplementedError

    def servo_update(self) -> ServoResult:
        raise NotImplementedError

    def holdover(self, elapsed_ns: int) -> HoldoverResult:
        raise NotImplementedError

    def step_threshold(self) -> int:
        raise NotImplementedError

    def clock_ns(self) -> int:
        raise NotImplementedError

    def frequency_ppb(self) -> int:
        raise NotImplementedError

    def link_delay(self) -> int:
        raise NotImplementedError

    def window_size(self) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
