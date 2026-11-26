"""WebSocket topic subscription authorizer with revocation and outbox handling."""

from __future__ import annotations

import copy
import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


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
        self._connections: dict[str, None] = {}
        self._grants: dict[str, set[str]] = {}
        self._subscriptions: dict[str, dict[str, str]] = {}
        self._outboxes: dict[str, deque[OutboxItem]] = {}
        self._resubscriptions: dict[tuple[str, str], dict[str, Any]] = {}
        self._next_seq = 1
        self._counters = {
            "broadcast_delivered": 0,
            "broadcast_dropped": 0,
            "revoked_subscriptions": 0,
        }

    @staticmethod
    def _invalid(detail: object = "invalid request") -> WsAuthError:
        return WsAuthError("WS_INVALID_REQUEST", detail)

    @classmethod
    def _require_non_empty_string(cls, value: object, name: str) -> str:
        if not isinstance(value, str) or value == "":
            raise cls._invalid({name: "must be a non-empty string"})
        return value

    @staticmethod
    def _coerce_time(value: object) -> tuple[object, float]:
        if value is None:
            moment = datetime.now(timezone.utc)
            return moment.isoformat(), moment.timestamp()

        if isinstance(value, bool):
            raise WsSubscriptionAuthorizer._invalid({"now": "invalid timestamp"})

        if isinstance(value, (int, float)):
            return value, float(value)

        if isinstance(value, datetime):
            moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return value.isoformat(), moment.timestamp()

        if isinstance(value, str):
            try:
                text = value[:-1] + "+00:00" if value.endswith("Z") else value
                moment = datetime.fromisoformat(text)
            except (TypeError, ValueError) as exc:
                raise WsSubscriptionAuthorizer._invalid({"now": "invalid timestamp"}) from exc
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return value, moment.timestamp()

        raise WsSubscriptionAuthorizer._invalid({"now": "invalid timestamp"})

    def _append_outbox_locked(self, conn_id: str, item: OutboxItem) -> bool:
        if self.max_outbox_per_conn <= 0:
            if item.kind == "message":
                self._counters["broadcast_dropped"] += 1
            return False

        queue = self._outboxes.setdefault(conn_id, deque())
        while len(queue) >= self.max_outbox_per_conn:
            dropped = queue.popleft()
            if dropped.kind == "message":
                self._counters["broadcast_dropped"] += 1
        queue.append(item)
        return True

    def connect(self, conn_id: str) -> None:
        conn_id = self._require_non_empty_string(conn_id, "conn_id")
        with self._lock:
            if conn_id in self._connections:
                return None
            if len(self._connections) >= self.max_connections:
                raise WsAuthError(
                    "WS_CONNECTION_LIMIT",
                    {"max_connections": self.max_connections},
                )

            self._connections[conn_id] = None
            self._grants[conn_id] = set()
            self._subscriptions[conn_id] = {}
            self._outboxes[conn_id] = deque()
            return None

    def disconnect(self, conn_id: str) -> int:
        conn_id = self._require_non_empty_string(conn_id, "conn_id")
        with self._lock:
            if conn_id not in self._connections:
                return 0

            removed = len(self._subscriptions.get(conn_id, {}))
            self._connections.pop(conn_id, None)
            self._grants.pop(conn_id, None)
            self._subscriptions.pop(conn_id, None)
            self._outboxes.pop(conn_id, None)

            prefix = (conn_id, "")
            for key in [key for key in self._resubscriptions if key[0] == prefix[0]]:
                self._resubscriptions.pop(key, None)

            return removed

    def grant(self, conn_id: str, topic: str) -> None:
        conn_id = self._require_non_empty_string(conn_id, "conn_id")
        topic = self._require_non_empty_string(topic, "topic")
        with self._lock:
            if conn_id not in self._connections:
                raise WsAuthError("WS_UNKNOWN_CONNECTION", {"conn_id": conn_id})
            self._grants.setdefault(conn_id, set()).add(topic)

    def subscribe(self, conn_id: str, topic: str, now: str | None = None) -> SubscriptionView:
        conn_id = self._require_non_empty_string(conn_id, "conn_id")
        topic = self._require_non_empty_string(topic, "topic")
        subscribed_at, now_ts = self._coerce_time(now)

        with self._lock:
            if conn_id not in self._connections:
                raise WsAuthError("WS_UNKNOWN_CONNECTION", {"conn_id": conn_id})

            grants = self._grants.setdefault(conn_id, set())
            if topic not in grants:
                raise WsAuthError("WS_TOPIC_NOT_ALLOWED", {"conn_id": conn_id, "topic": topic})

            subscriptions = self._subscriptions.setdefault(conn_id, {})
            existing = subscriptions.get(topic)
            if existing is not None:
                return SubscriptionView(conn_id, topic, existing)

            retry_key = (conn_id, topic)
            retry_state = self._resubscriptions.get(retry_key)
            if retry_state is not None:
                attempts = int(retry_state.get("attempts", 0)) + 1
                retry_state["attempts"] = attempts

                if attempts > self.max_resubscribe_attempts:
                    raise WsAuthError(
                        "WS_RESUBSCRIBE_EXHAUSTED",
                        {
                            "conn_id": conn_id,
                            "topic": topic,
                            "attempts": attempts - 1,
                            "max_resubscribe_attempts": self.max_resubscribe_attempts,
                        },
                    )

                revoked_at = retry_state.get("revoked_ts")
                if revoked_at is None:
                    _, revoked_at = self._coerce_time(retry_state.get("revoked_at"))
                    retry_state["revoked_ts"] = revoked_at

                retry_after = self.resubscribe_cooldown_s - (now_ts - float(revoked_at))
                if retry_after > 0:
                    raise WsAuthError(
                        "WS_RESUBSCRIBE_COOLDOWN",
                        {"retry_after_s": max(1, math.ceil(retry_after))},
                    )

            if len(subscriptions) >= self.max_topics_per_conn:
                raise WsAuthError(
                    "WS_SUBSCRIPTION_LIMIT",
                    {
                        "conn_id": conn_id,
                        "max_topics_per_conn": self.max_topics_per_conn,
                    },
                )

            subscriptions[topic] = subscribed_at
            self._resubscriptions.pop(retry_key, None)
            return SubscriptionView(conn_id, topic, subscribed_at)

    def unsubscribe(self, conn_id: str, topic: str) -> bool:
        conn_id = self._require_non_empty_string(conn_id, "conn_id")
        topic = self._require_non_empty_string(topic, "topic")
        with self._lock:
            if conn_id not in self._connections:
                return False
            subscriptions = self._subscriptions.setdefault(conn_id, {})
            return subscriptions.pop(topic, None) is not None

    def revoke_grant(self, topic: str, now: str | None = None) -> dict:
        topic = self._require_non_empty_string(topic, "topic")
        revoked_at, revoked_ts = self._coerce_time(now)

        with self._lock:
            removed_subscriptions = 0
            affected_connections: list[str] = []

            for conn_id in self._connections:
                self._grants.get(conn_id, set()).discard(topic)
                subscriptions = self._subscriptions.get(conn_id, {})

                if topic not in subscriptions:
                    continue

                subscriptions.pop(topic, None)
                removed_subscriptions += 1
                self._counters["revoked_subscriptions"] += 1
                affected_connections.append(conn_id)

                queue = self._outboxes.get(conn_id)
                if queue:
                    self._outboxes[conn_id] = deque(
                        item for item in queue if item.topic != topic
                    )

                self._resubscriptions[(conn_id, topic)] = {
                    "revoked_at": revoked_at,
                    "revoked_ts": revoked_ts,
                    "attempts": 0,
                }

            for conn_id in affected_connections:
                item = OutboxItem(
                    seq=self._next_seq,
                    kind="notice",
                    topic=topic,
                    payload={"reason": "grant_revoked", "topic": topic, "revoked_at": revoked_at},
                )
                self._next_seq += 1
                self._append_outbox_locked(conn_id, item)

            return {
                "revoked_connections": len(affected_connections),
                "removed_subscriptions": removed_subscriptions,
                "notices": len(affected_connections),
            }

    def broadcast(self, topic: str, payload, now: str | None = None) -> int:
        topic = self._require_non_empty_string(topic, "topic")
        # ``now`` is accepted for call-site compatibility but broadcasts are
        # enqueued immediately and OutboxItem has no timestamp field.

        with self._lock:
            targets = [
                conn_id
                for conn_id, subscriptions in self._subscriptions.items()
                if topic in subscriptions
            ]

            delivered = 0
            for conn_id in targets:
                item = OutboxItem(
                    seq=self._next_seq,
                    kind="message",
                    topic=topic,
                    payload=payload,
                )
                self._next_seq += 1
                if self._append_outbox_locked(conn_id, item):
                    delivered += 1
                    self._counters["broadcast_delivered"] += 1

            return delivered

    def flush_outbox(self, conn_id: str) -> list:
        conn_id = self._require_non_empty_string(conn_id, "conn_id")
        with self._lock:
            queue = self._outboxes.get(conn_id)
            if not queue:
                self._outboxes.pop(conn_id, None)
                return []
            items = list(queue)
            queue.clear()
            return items

    def stats(self) -> dict:
        with self._lock:
            subscription_count = sum(len(items) for items in self._subscriptions.values())
            grant_count = sum(len(items) for items in self._grants.values())
            outbox_count = sum(len(queue) for queue in self._outboxes.values())
            return {
                "connections": len(self._connections),
                "subscriptions": subscription_count,
                "grants": grant_count,
                "outbox_items": outbox_count,
                "broadcast_delivered": self._counters["broadcast_delivered"],
                "broadcast_dropped": self._counters["broadcast_dropped"],
                "revoked_subscriptions": self._counters["revoked_subscriptions"],
            }

    def snapshot(self) -> dict:
        with self._lock:
            outbox_state = {
                conn_id: list(queue)
                for conn_id, queue in self._outboxes.items()
            }
            stats_state = self.stats()
            state = {
                "version": 1,
                "connections": list(self._connections),
                "grants": {
                    conn_id: sorted(topics)
                    for conn_id, topics in self._grants.items()
                },
                "subscriptions": {
                    conn_id: dict(topics)
                    for conn_id, topics in self._subscriptions.items()
                },
                "outbox": outbox_state,
                "outboxes": outbox_state,
                "next_seq": self._next_seq,
                "stats": stats_state,
                "counts": stats_state,
                "counters": stats_state,
                "revocations": [
                    {
                        "conn_id": conn_id,
                        "topic": topic,
                        "revoked_at": data["revoked_at"],
                        "attempts": data["attempts"],
                    }
                    for (conn_id, topic), data in self._resubscriptions.items()
                ],
            }
            detached = copy.deepcopy(state)
            detached["outboxes"] = detached["outbox"]
            detached["counts"] = detached["stats"]
            detached["counters"] = detached["stats"]
            return detached

    def restore(self, state: dict) -> None:
        try:
            detached = copy.deepcopy(state)
            restored = self._build_restored_state(detached)
        except WsAuthError:
            raise
        except Exception as exc:
            raise WsAuthError("WS_SNAPSHOT_INVALID", {"reason": str(exc)}) from exc

        with self._lock:
            self._connections = {conn_id: None for conn_id in restored["connections"]}
            self._grants = restored["grants"]
            self._subscriptions = restored["subscriptions"]
            self._outboxes = {
                conn_id: deque(items)
                for conn_id, items in restored["outboxes"].items()
            }
            self._resubscriptions = restored["resubscriptions"]
            self._next_seq = restored["next_seq"]
            self._counters = restored["counters"]

    @staticmethod
    def _snapshot_invalid(detail: object) -> WsAuthError:
        return WsAuthError("WS_SNAPSHOT_INVALID", detail)

    @classmethod
    def _build_restored_state(cls, state: object) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise cls._snapshot_invalid({"state": "must be a dict"})

        version = state.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise cls._snapshot_invalid({"version": "must be 1"})

        for key in ("connections", "grants", "subscriptions"):
            if key not in state:
                raise cls._snapshot_invalid({key: "is required"})
        if not any(key in state for key in ("outboxes", "outbox", "queues")):
            raise cls._snapshot_invalid({"outboxes": "is required"})
        if not any(key in state for key in ("stats", "counts", "counters")):
            raise cls._snapshot_invalid({"stats": "is required"})

        connections_raw = state.get("connections", [])
        if isinstance(connections_raw, (str, bytes)) or not isinstance(
            connections_raw, (list, tuple, set, frozenset, dict)
        ):
            raise cls._snapshot_invalid({"connections": "must be a sequence"})

        connections: list[str] = []
        seen: set[str] = set()
        for conn_id in connections_raw:
            if not isinstance(conn_id, str) or conn_id == "":
                raise cls._snapshot_invalid({"connections": "invalid conn_id"})
            if conn_id not in seen:
                seen.add(conn_id)
                connections.append(conn_id)

        known = set(connections)
        grants = cls._restore_grants(state.get("grants", {}), known)
        subscriptions = cls._restore_subscriptions(state.get("subscriptions", {}), known)

        outbox_key = next(key for key in ("outboxes", "outbox", "queues") if key in state)
        outboxes_raw = state[outbox_key]
        outboxes, used_seqs, max_seq = cls._restore_outboxes(outboxes_raw, known)

        next_seq = cls._restore_next_seq(state, max_seq)
        if used_seqs and next_seq <= max_seq:
            next_seq = max_seq + 1

        counters = cls._restore_counters(state)
        resubscriptions = cls._restore_resubscriptions(
            state.get("revocations", state.get("resubscriptions", [])),
        )

        return {
            "connections": connections,
            "grants": grants,
            "subscriptions": subscriptions,
            "outboxes": outboxes,
            "next_seq": next_seq,
            "counters": counters,
            "resubscriptions": resubscriptions,
        }

    @classmethod
    def _restore_grants(cls, raw: object, known: set[str]) -> dict[str, set[str]]:
        if not isinstance(raw, dict):
            raise cls._snapshot_invalid({"grants": "must be a dict"})

        grants: dict[str, set[str]] = {conn_id: set() for conn_id in known}
        for conn_id, topics in raw.items():
            if conn_id not in known:
                raise cls._snapshot_invalid({"grants": "unknown connection"})
            if isinstance(topics, dict):
                topic_values = topics.keys()
            elif isinstance(topics, (list, tuple, set, frozenset)) and not isinstance(topics, (str, bytes)):
                topic_values = topics
            else:
                raise cls._snapshot_invalid({"grants": "topics must be a collection"})

            for topic in topic_values:
                if not isinstance(topic, str) or topic == "":
                    raise cls._snapshot_invalid({"grants": "invalid topic"})
                grants[conn_id].add(topic)
        return grants

    @classmethod
    def _restore_subscriptions(cls, raw: object, known: set[str]) -> dict[str, dict[str, object]]:
        if not isinstance(raw, dict):
            raise cls._snapshot_invalid({"subscriptions": "must be a dict"})

        subscriptions: dict[str, dict[str, object]] = {
            conn_id: {} for conn_id in known
        }
        for conn_id, topics_raw in raw.items():
            if conn_id not in known:
                raise cls._snapshot_invalid({"subscriptions": "unknown connection"})

            topics = subscriptions[conn_id]
            if isinstance(topics_raw, dict):
                entries = topics_raw.items()
                for topic, subscribed_at in entries:
                    cls._add_restored_subscription(topics, topic, subscribed_at)
            elif isinstance(topics_raw, (list, tuple, set, frozenset)) and not isinstance(
                topics_raw, (str, bytes)
            ):
                for entry in topics_raw:
                    topic, subscribed_at = cls._subscription_entry(entry)
                    cls._add_restored_subscription(topics, topic, subscribed_at)
            else:
                raise cls._snapshot_invalid({"subscriptions": "invalid topics"})

        return subscriptions

    @staticmethod
    def _subscription_entry(entry: object) -> tuple[object, object]:
        if isinstance(entry, str):
            return entry, None
        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            return entry[0], entry[1]
        if isinstance(entry, dict):
            topic = entry.get("topic")
            subscribed_at = entry.get("subscribed_at", entry.get("at"))
            return topic, subscribed_at

        topic = getattr(entry, "topic", None)
        subscribed_at = getattr(entry, "subscribed_at", None)
        if topic is not None:
            return topic, subscribed_at
        raise WsSubscriptionAuthorizer._snapshot_invalid(
            {"subscriptions": "invalid subscription entry"}
        )

    @classmethod
    def _add_restored_subscription(
        cls, topics: dict[str, object], topic: object, subscribed_at: object
    ) -> None:
        if not isinstance(topic, str) or topic == "":
            raise cls._snapshot_invalid({"subscriptions": "invalid topic"})
        if subscribed_at is None:
            subscribed_at = datetime.now(timezone.utc).isoformat()
        if not isinstance(subscribed_at, (str, int, float, datetime)) or isinstance(
            subscribed_at, bool
        ):
            raise cls._snapshot_invalid({"subscriptions": "invalid subscribed_at"})
        topics[topic] = subscribed_at

    @classmethod
    def _restore_outboxes(
        cls, raw: object, known: set[str]
    ) -> tuple[dict[str, list[OutboxItem]], set[int], int]:
        if not isinstance(raw, dict):
            raise cls._snapshot_invalid({"outboxes": "must be a dict"})

        outboxes: dict[str, list[OutboxItem]] = {conn_id: [] for conn_id in known}
        used_seqs: set[int] = set()
        max_seq = 0

        for conn_id, items_raw in raw.items():
            if conn_id not in known:
                raise cls._snapshot_invalid({"outboxes": "unknown connection"})
            if isinstance(items_raw, (str, bytes)) or not isinstance(
                items_raw, (list, tuple, deque)
            ):
                raise cls._snapshot_invalid({"outboxes": "queue must be a list"})

            previous_seq = 0
            items: list[OutboxItem] = []
            for item_raw in items_raw:
                item = cls._outbox_item(item_raw)
                if item.seq in used_seqs:
                    raise cls._snapshot_invalid({"outboxes": "duplicate seq"})
                if item.seq <= previous_seq:
                    raise cls._snapshot_invalid({"outboxes": "seq must increase"})
                used_seqs.add(item.seq)
                max_seq = max(max_seq, item.seq)
                previous_seq = item.seq
                items.append(item)
            outboxes[conn_id] = items

        return outboxes, used_seqs, max_seq

    @classmethod
    def _outbox_item(cls, raw: object) -> OutboxItem:
        if isinstance(raw, OutboxItem):
            seq, kind, topic, payload = raw.seq, raw.kind, raw.topic, raw.payload
        elif isinstance(raw, dict):
            seq = raw.get("seq", raw.get("sequence"))
            kind = raw.get("kind", raw.get("type"))
            topic = raw.get("topic")
            payload = raw.get("payload", raw.get("data"))
        elif isinstance(raw, tuple) and len(raw) == 4:
            seq, kind, topic, payload = raw
        else:
            raise cls._snapshot_invalid({"outboxes": "invalid item"})

        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise cls._snapshot_invalid({"outboxes": "invalid seq"})
        if kind not in ("message", "notice"):
            raise cls._snapshot_invalid({"outboxes": "invalid kind"})
        if not isinstance(topic, str) or topic == "":
            raise cls._snapshot_invalid({"outboxes": "invalid topic"})
        return OutboxItem(seq=seq, kind=kind, topic=topic, payload=payload)

    @classmethod
    def _restore_next_seq(cls, state: dict, max_seq: int) -> int:
        raw = state.get("next_seq", state.get("seq", state.get("next_sequence", max_seq + 1)))
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise cls._snapshot_invalid({"next_seq": "must be a positive integer"})
        return max(raw, max_seq + 1)

    @classmethod
    def _restore_counters(cls, state: dict) -> dict[str, int]:
        raw = next(
            (state[key] for key in ("stats", "counts", "counters")
             if isinstance(state.get(key), dict)),
            {},
        )
        if not isinstance(raw, dict):
            raise cls._snapshot_invalid({"stats": "must be a dict"})

        counters = {
            "broadcast_delivered": 0,
            "broadcast_dropped": 0,
            "revoked_subscriptions": 0,
        }
        for key in counters:
            value = raw.get(key, state.get(key, 0))
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise cls._snapshot_invalid({"stats": f"{key} must be non-negative integer"})
            counters[key] = value
        return counters

    @classmethod
    def _restore_resubscriptions(cls, raw: object) -> dict[tuple[str, str], dict[str, Any]]:
        if isinstance(raw, dict):
            entries = []
            for key, value in raw.items():
                if isinstance(key, str) and "\u0000" in key:
                    conn_id, topic = key.split("\u0000", 1)
                    if isinstance(value, dict):
                        value = {**value, "conn_id": conn_id, "topic": topic}
                entries.append(value)
            raw = entries

        if not isinstance(raw, (list, tuple)):
            raise cls._snapshot_invalid({"revocations": "must be a list"})

        restored: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise cls._snapshot_invalid({"revocations": "invalid entry"})
            conn_id = entry.get("conn_id")
            topic = entry.get("topic")
            if not isinstance(conn_id, str) or conn_id == "" or not isinstance(topic, str) or topic == "":
                raise cls._snapshot_invalid({"revocations": "invalid identity"})

            revoked_at = entry.get("revoked_at", entry.get("at"))
            _, revoked_ts = WsSubscriptionAuthorizer._coerce_time(revoked_at)
            attempts = entry.get("attempts", 0)
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                raise cls._snapshot_invalid({"revocations": "invalid attempts"})

            restored[(conn_id, topic)] = {
                "revoked_at": revoked_at,
                "revoked_ts": revoked_ts,
                "attempts": attempts,
            }
        return restored
