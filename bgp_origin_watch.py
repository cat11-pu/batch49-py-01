"""BGP announcement watch for origin changes, ROA coverage and hijack events."""

from __future__ import annotations


class BgpWatchError(ValueError):
    """Raised when an announcement or ROA operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BgpOriginWatch:
    """Score announcements against ROAs and the learned origin baseline."""

    def __init__(self, *, confirm_peers: int = 2, max_events: int = 256,
                 max_prefixes: int = 256, roa_grace: int = 0) -> None:
        self.confirm_peers = confirm_peers
        self.max_events = max_events
        self.max_prefixes = max_prefixes
        self.roa_grace = roa_grace

    def add_roa(self, prefix: str, origin_as: int, *, max_length: int | None = None,
                expires_at: int | None = None) -> dict:
        raise NotImplementedError

    def withdraw_roa(self, prefix: str, origin_as: int) -> bool:
        raise NotImplementedError

    def observe(self, prefix: str, origin_as: int, path: list, *, peer: str,
                now: int = 0) -> dict:
        raise NotImplementedError

    def assessment(self, prefix: str) -> dict:
        raise NotImplementedError

    def events(self, *, include_resolved: bool = False) -> list:
        raise NotImplementedError

    def ack(self, event_id: str) -> bool:
        raise NotImplementedError

    def prefixes(self) -> list:
        raise NotImplementedError

    def prune(self, *, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
