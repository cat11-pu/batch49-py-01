"""Race recursive resolver upstreams and arbitrate UDP/TCP answers."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ResolveError(ValueError):
    """Raised when a resolution request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class UpstreamHealth:
    upstream: str
    state: str
    inflight: int
    failures: int
    cooldown_until: int | None


class ResolverRaceArbiter:
    """Send one query to several upstreams at once and keep the first answer."""

    def __init__(self, responder, *, max_inflight_per_upstream: int = 2,
                 penalty_threshold: int = 2, cooldown_seconds: int = 10,
                 max_upstreams: int = 8) -> None:
        self._responder = responder
        self.max_inflight_per_upstream = max_inflight_per_upstream
        self.penalty_threshold = penalty_threshold
        self.cooldown_seconds = cooldown_seconds
        self.max_upstreams = max_upstreams
        self._lock = threading.RLock()

    def set_upstreams(self, upstreams) -> int:
        raise NotImplementedError

    def upstreams(self) -> tuple[str, ...]:
        raise NotImplementedError

    def resolve(self, query: str, *, now: int) -> dict:
        raise NotImplementedError

    def tcp_retry(self, query: str, upstream: str, *, now: int) -> dict:
        raise NotImplementedError

    def health(self, upstream: str | None = None,
               *, now: int | None = None) -> dict:
        raise NotImplementedError

    def result_of(self, query: str) -> dict | None:
        raise NotImplementedError

    def deliver_late(self, query: str, upstream: str, response: dict,
                     *, now: int) -> bool:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
