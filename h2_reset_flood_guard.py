"""HTTP/2 connection and stream ledger for Rapid Reset style floods."""

from __future__ import annotations


class H2GuardError(ValueError):
    """Raised when a connection or stream operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class H2ResetFloodGuard:
    """Track stream lifecycles and quarantine connections that reset too fast."""

    def __init__(self, *, max_concurrent_streams: int = 100,
                 window_seconds: int = 60, reset_limit: int = 20,
                 ratio_limit: float = 0.5, min_samples: int = 10,
                 quarantine_seconds: int = 30, max_connections: int = 64,
                 max_reset_history: int = 2048) -> None:
        self.max_concurrent_streams = max_concurrent_streams
        self.window_seconds = window_seconds
        self.reset_limit = reset_limit
        self.ratio_limit = ratio_limit
        self.min_samples = min_samples
        self.quarantine_seconds = quarantine_seconds
        self.max_connections = max_connections
        self.max_reset_history = max_reset_history

    def open_connection(self, conn_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def close_connection(self, conn_id: str, *, now: int = 0) -> bool:
        raise NotImplementedError

    def open_stream(self, conn_id: str, stream_id: int, *, now: int = 0) -> dict:
        raise NotImplementedError

    def close_stream(self, conn_id: str, stream_id: int, *, now: int = 0) -> bool:
        raise NotImplementedError

    def record_reset(self, conn_id: str, stream_id: int, *, now: int = 0) -> dict:
        raise NotImplementedError

    def evaluate(self, conn_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def quarantine(self, conn_id: str, *, now: int = 0, reason: str = "manual") -> dict:
        raise NotImplementedError

    def release(self, conn_id: str, *, now: int = 0) -> bool:
        raise NotImplementedError

    def state(self, conn_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def prune(self, *, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
