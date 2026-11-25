"""Per-hop proxy authentication forwarder for multi-hop forward proxy chains."""

from __future__ import annotations

import threading


class HopAuthError(RuntimeError):
    """Raised when a hop or chain operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class HopAuthForwarder:
    """Maintain an ordered proxy chain, per-hop auth schemes and credential refs."""

    def __init__(self, *, max_auth_failures: int = 2) -> None:
        self.max_auth_failures = max_auth_failures
        self._lock = threading.RLock()

    def set_chain(self, hops) -> list:
        raise NotImplementedError

    def register_credential(self, ref: str, secret: str) -> None:
        raise NotImplementedError

    def forward(self, request: dict) -> dict:
        raise NotImplementedError

    def authenticate_hop(self, hop_name: str, now: str | None = None) -> dict:
        raise NotImplementedError

    def mark_response(self, hop_name: str, status: int,
                      now: str | None = None) -> dict:
        raise NotImplementedError

    def failover(self, hop_name: str) -> str:
        raise NotImplementedError

    def hop_status(self) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
