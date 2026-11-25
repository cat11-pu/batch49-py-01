"""Firewall policy evaluation, first-match lookup and conflict analysis."""

from __future__ import annotations


class FwPolicyError(ValueError):
    """Raised when a rule or query is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FirewallPolicyAnalyzer:
    """Keep ordered rules, answer flow queries and report policy conflicts."""

    def __init__(self, *, default_action: str = "deny", max_rules: int = 256) -> None:
        self.default_action = default_action
        self.max_rules = max_rules

    def add_rule(self, name: str, *, action: str, src: str, dst: str,
                 proto: str = "tcp", ports: str | None = None,
                 priority: int = 100, enabled: bool = True,
                 zone: str | None = None) -> dict:
        raise NotImplementedError

    def remove_rule(self, name: str) -> bool:
        raise NotImplementedError

    def enable_rule(self, name: str, *, enabled: bool = True) -> bool:
        raise NotImplementedError

    def query(self, src_ip: str, dst_ip: str, *, proto: str = "tcp",
              port: int | None = None, zone: str | None = None) -> dict:
        raise NotImplementedError

    def explain(self, src_ip: str, dst_ip: str, *, proto: str = "tcp",
                port: int | None = None, zone: str | None = None) -> dict:
        raise NotImplementedError

    def conflicts(self) -> list:
        raise NotImplementedError

    def summary(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
