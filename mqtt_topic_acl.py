"""MQTT topic ACL: inherited rules, wildcard coverage and retained replay
isolation."""

from __future__ import annotations

import threading


class MqttAclError(ValueError):
    """Raised when a topic, filter, rule or publish is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MqttTopicAcl:
    """Authorize subscriptions and publishes against a per-principal rule set."""

    def __init__(self, *, max_rules: int = 64, max_depth: int = 8,
                 max_subscriptions: int = 8, max_queued: int = 8,
                 max_retained: int = 32, default_effect: str = "deny") -> None:
        self.max_rules = max_rules
        self.max_depth = max_depth
        self.max_subscriptions = max_subscriptions
        self.max_queued = max_queued
        self.max_retained = max_retained
        self.default_effect = default_effect
        self._lock = threading.RLock()

    def normalize_topic(self, topic: str) -> str:
        raise NotImplementedError

    def normalize_filter(self, flt: str) -> str:
        raise NotImplementedError

    def add_rule(self, principal: str, flt: str, *, effect: str = "allow",
                 priority: int = 0) -> dict:
        raise NotImplementedError

    def rules(self) -> list:
        raise NotImplementedError

    def authorize(self, principal: str, flt: str, *,
                  action: str = "subscribe") -> dict:
        raise NotImplementedError

    def subscribe(self, principal: str, flt: str, *, qos: int = 0,
                  session: str = "default", now: int = 0) -> dict:
        raise NotImplementedError

    def unsubscribe(self, principal: str, flt: str) -> bool:
        raise NotImplementedError

    def publish(self, principal: str, topic: str, payload, *, qos: int = 0,
                retain: bool = False, now: int = 0) -> dict:
        raise NotImplementedError

    def replay(self, principal: str, *, session: str = "default",
               now: int = 0) -> list:
        raise NotImplementedError

    def drain(self, principal: str, *, session: str = "default",
              limit: int = None) -> list:
        raise NotImplementedError

    def subscriptions(self) -> list:
        raise NotImplementedError

    def revoke(self, principal: str) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
