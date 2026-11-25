"""Mount-table drift detection: rebuild trees by mount id and compare with a baseline."""

from __future__ import annotations

import threading


class DriftError(ValueError):
    """Refused or inconsistent drift-detector operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class OvermountDriftDetector:
    """Compare successive mount tables against an explicitly confirmed baseline."""

    def __init__(self, *, max_entries: int = 32, alert_window: float = 2.0,
                 clock=None) -> None:
        self.max_entries = max_entries
        self.alert_window = alert_window
        self._clock = clock
        self._lock = threading.RLock()

    def baseline(self, rows) -> dict:
        raise NotImplementedError

    def mount_tree(self, rows=None) -> dict:
        raise NotImplementedError

    def scan(self, rows) -> dict:
        raise NotImplementedError

    def diff(self, current=None) -> dict:
        raise NotImplementedError

    def confirm(self) -> int:
        raise NotImplementedError

    def recover(self, target: str) -> dict:
        raise NotImplementedError

    def alerts(self) -> list[dict]:
        raise NotImplementedError

    def clear_alerts(self) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
