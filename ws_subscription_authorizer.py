"""WebSocket topic subscription authorizer with revocation and outbox handling."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class WsAuthError(RuntimeError):
    """Raised when a subscription or authorization operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SubscriptionView:
    conn_id: str
    topic: str
    subscribed_at: str


@dataclass(frozen=True)
class OutboxItem:
    seq: int
    kind: str
    topic: str
    payload: object


class WsSubscriptionAuthorizer:
    """Track per-connection subscriptions, topic grants and pending outbox items."""

    def __init__(self, *, max_connections: int = 256,
                 max_topics_per_conn: int = 32,
                 max_outbox_per_conn: int = 64,
                 resubscribe_cooldown_s: int = 30,
                 max_resubscribe_attempts: int = 3) -> None:
        self.max_connections = max_connections
        self.max_topics_per_conn = max_topics_per_conn
        self.max_outbox_per_conn = max_outbox_per_conn
        self.resubscribe_cooldown_s = resubscribe_cooldown_s
        self.max_resubscribe_attempts = max_resubscribe_attempts
        self._lock = threading.RLock()

    def connect(self, conn_id: str) -> None:
        raise NotImplementedError

    def disconnect(self, conn_id: str) -> int:
        raise NotImplementedError

    def grant(self, conn_id: str, topic: str) -> None:
        raise NotImplementedError

    def subscribe(self, conn_id: str, topic: str, now: str | None = None) -> SubscriptionView:
        raise NotImplementedError

    def unsubscribe(self, conn_id: str, topic: str) -> bool:
        raise NotImplementedError

    def revoke_grant(self, topic: str, now: str | None = None) -> dict:
        raise NotImplementedError

    def broadcast(self, topic: str, payload, now: str | None = None) -> int:
        raise NotImplementedError

    def flush_outbox(self, conn_id: str) -> list:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
