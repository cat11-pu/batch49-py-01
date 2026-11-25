"""Signature matching engine with alert deduplication, thresholds and suppression."""

from __future__ import annotations


class IdsSignatureError(ValueError):
    """Raised when a rule, packet or suppression is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class IdsSignatureEngine:
    """Match packets against loaded signatures and account for alert volume."""

    def __init__(self, *, max_rules: int = 128, max_alerts: int = 256,
                 suppress_window: int = 30, max_payload_bytes: int = 8192) -> None:
        self.max_rules = max_rules
        self.max_alerts = max_alerts
        self.suppress_window = suppress_window
        self.max_payload_bytes = max_payload_bytes

    def load_rules(self, rules: list) -> int:
        raise NotImplementedError

    def add_rule(self, rule: dict) -> dict:
        raise NotImplementedError

    def remove_rule(self, sid: int) -> bool:
        raise NotImplementedError

    def rule(self, sid: int):
        raise NotImplementedError

    def inspect(self, packet: dict, *, now: int = 0) -> list:
        raise NotImplementedError

    def suppress(self, sid: int, *, scope: str = "global", ttl: int = 60,
                 now: int = 0) -> dict:
        raise NotImplementedError

    def unsuppress(self, sid: int, *, scope: str | None = None) -> int:
        raise NotImplementedError

    def alerts(self) -> list:
        raise NotImplementedError

    def prune(self, *, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
