"""Player trade negotiation: versioned offers, turn rotation, atomic swap."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class TradeError(ValueError):
    """Raised when a trade operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OfferVersion:
    version: int
    proposer: str
    give: tuple
    want: tuple
    created_at: str
    expires_at: str


@dataclass(frozen=True)
class TradeResult:
    status: str
    version: int


_STAT_KEYS = (
    "sessions",
    "offers",
    "counters",
    "pending_accepts",
    "accepted",
    "rejected",
    "cancelled",
    "expired",
)
_STATUSES = {"open", "accepted", "rejected", "cancelled", "expired"}
_CLOSED_STATUSES = {"accepted", "rejected", "cancelled", "expired"}


class TradeOfferNegotiation:
    """Run two-party offer/counter sessions with atomic inventory exchange."""

    def __init__(self, *, offer_ttl: int = 300, max_versions: int = 20,
                 clock=None) -> None:
        self.offer_ttl = offer_ttl
        self.max_versions = max_versions
        self._clock = clock
        self._lock = threading.RLock()
        self._sessions = {}
        self._inventories = {}
        self._reserved = {}
        self._stats = {key: 0 for key in _STAT_KEYS}

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_name(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _is_qty(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @classmethod
    def _parse_time(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise TradeError("TRADE_INVALID_TIME", value) from exc
        else:
            raise TradeError("TRADE_INVALID_TIME", value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.isoformat()

    def _resolve_now(self, now: object) -> datetime | None:
        if now is not None:
            return self._parse_time(now)
        if self._clock is not None:
            return self._parse_time(self._clock())
        return None

    def _event_now(self, now: object) -> datetime:
        resolved = self._resolve_now(now)
        if resolved is None:
            resolved = datetime.now(timezone.utc)
        return resolved

    @staticmethod
    def _normalize_items(items: object, code: str, *, allow_empty: bool) -> tuple:
        if not isinstance(items, dict):
            raise TradeError(code, "items must be a mapping")
        if not items and not allow_empty:
            raise TradeError(code, "items must not be empty")
        normalized = []
        for name, qty in items.items():
            if not isinstance(name, str) or not name.strip():
                raise TradeError(code, "item name must be a non-empty string")
            if not TradeOfferNegotiation._is_qty(qty):
                raise TradeError(code, "item quantity must be a positive integer")
            normalized.append((name, qty))
        normalized.sort(key=lambda item: item[0])
        return tuple(normalized)

    @staticmethod
    def _normalize_pairs(pairs: object, code: str, *,
                         allow_empty: bool) -> tuple:
        if isinstance(pairs, dict):
            return TradeOfferNegotiation._normalize_items(
                pairs, code, allow_empty=allow_empty)
        if not isinstance(pairs, (list, tuple)):
            raise TradeError(code, "items must be a sequence of pairs")
        if not pairs and not allow_empty:
            raise TradeError(code, "items must not be empty")
        normalized = []
        seen = set()
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise TradeError(code, "item must be a name/quantity pair")
            name, qty = pair
            if not isinstance(name, str) or not name.strip():
                raise TradeError(code, "item name must be a non-empty string")
            if not TradeOfferNegotiation._is_qty(qty):
                raise TradeError(code, "item quantity must be a positive integer")
            if name in seen:
                raise TradeError(code, "duplicate item name")
            seen.add(name)
            normalized.append((name, qty))
        normalized.sort(key=lambda item: item[0])
        return tuple(normalized)

    def _session(self, session_id: object) -> dict:
        if not isinstance(session_id, str):
            raise TradeError("TRADE_UNKNOWN_SESSION", session_id)
        session = self._sessions.get(session_id)
        if session is None:
            raise TradeError("TRADE_UNKNOWN_SESSION", session_id)
        return session

    @staticmethod
    def _is_party(session: dict, player: object) -> bool:
        return player == session["player_a"] or player == session["player_b"]

    @classmethod
    def _check_party(cls, session: dict, player: object) -> None:
        if not cls._is_party(session, player):
            raise TradeError("TRADE_NOT_PARTY", player)

    # ------------------------------------------------------------------
    # Reservations and inventory
    # ------------------------------------------------------------------

    def _session_reserved(self, session: dict, player: str, item: str) -> int:
        return session["reservations"].get(player, {}).get(item, 0)

    def _reserved_for(self, player: str, item: str) -> int:
        return self._reserved.get(player, {}).get(item, 0)

    def _can_reserve(self, session: dict, player: str, items: tuple) -> bool:
        inventory = self._inventories.get(player, {})
        for item, qty in items:
            available = inventory.get(item, 0) - self._reserved_for(player, item)
            available += self._session_reserved(session, player, item)
            if available < qty:
                return False
        return True

    def _can_complete(self, session: dict, offer: OfferVersion) -> bool:
        proposer = offer.proposer
        responder = (session["player_b"] if proposer == session["player_a"]
                     else session["player_a"])
        legs = (
            (proposer, offer.give),
            (responder, offer.want),
        )
        for player, items in legs:
            inventory = self._inventories.get(player, {})
            for item, qty in items:
                if inventory.get(item, 0) < self._reserved_for(player, item):
                    return False
                if inventory.get(item, 0) < qty:
                    return False
        return True

    def _add_reservation(self, session: dict, player: str, items: tuple) -> None:
        own = session["reservations"].setdefault(player, {})
        aggregate = self._reserved.setdefault(player, {})
        for item, qty in items:
            own[item] = own.get(item, 0) + qty
            aggregate[item] = aggregate.get(item, 0) + qty

    def _release_reservations(self, session: dict) -> None:
        for player, items in session["reservations"].items():
            aggregate = self._reserved.get(player, {})
            for item, qty in items.items():
                left = aggregate.get(item, 0) - qty
                if left > 0:
                    aggregate[item] = left
                else:
                    aggregate.pop(item, None)
            if not aggregate:
                self._reserved.pop(player, None)
        session["reservations"] = {}

    @staticmethod
    def _take(inventory: dict, item: str, qty: int) -> None:
        left = inventory.get(item, 0) - qty
        if left < 0:
            raise TradeError("TRADE_ITEM_OCCUPIED", item)
        if left:
            inventory[item] = left
        else:
            inventory.pop(item, None)

    @staticmethod
    def _add(inventory: dict, item: str, qty: int) -> None:
        inventory[item] = inventory.get(item, 0) + qty

    def _swap(self, session: dict, offer: OfferVersion) -> None:
        proposer = offer.proposer
        responder = (session["player_b"] if proposer == session["player_a"]
                     else session["player_a"])
        proposer_inventory = dict(self._inventories.get(proposer, {}))
        responder_inventory = dict(self._inventories.get(responder, {}))

        for item, qty in offer.give:
            self._take(proposer_inventory, item, qty)
            self._add(responder_inventory, item, qty)
        for item, qty in offer.want:
            self._take(responder_inventory, item, qty)
            self._add(proposer_inventory, item, qty)

        self._inventories[proposer] = proposer_inventory
        self._inventories[responder] = responder_inventory

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def open_session(self, *, session_id: str, player_a: str, player_b: str,
                     now: str) -> None:
        with self._lock:
            if not self._is_name(session_id):
                raise TradeError("TRADE_INVALID_SESSION", "session_id")
            if not self._is_name(player_a) or not self._is_name(player_b):
                raise TradeError("TRADE_INVALID_SESSION", "players")
            if player_a == player_b:
                raise TradeError("TRADE_INVALID_SESSION", "players must differ")
            if session_id in self._sessions:
                raise TradeError("TRADE_SESSION_EXISTS", session_id)
            created_at = self._format_time(self._parse_time(now))
            self._sessions[session_id] = {
                "session_id": session_id,
                "player_a": player_a,
                "player_b": player_b,
                "status": "open",
                "created_at": created_at,
                "versions": [],
                "accepts": {},
                "reservations": {},
            }
            self._stats["sessions"] += 1

    def set_inventory(self, player: str, items: dict) -> None:
        with self._lock:
            if not self._is_name(player):
                raise TradeError("TRADE_INVALID_INVENTORY", "player")
            normalized = self._normalize_items(
                items, "TRADE_INVALID_INVENTORY", allow_empty=True)
            self._inventories[player] = dict(normalized)

    def inventory_of(self, player: str) -> dict:
        with self._lock:
            return dict(self._inventories.get(player, {}))

    # ------------------------------------------------------------------
    # Offers
    # ------------------------------------------------------------------

    def _expire_if_needed(self, session: dict,
                          now: datetime | None) -> bool:
        if (now is not None and session["status"] == "open"
                and session["versions"]):
            latest = session["versions"][-1]
            if now >= self._parse_time(latest.expires_at):
                session["status"] = "expired"
                self._release_reservations(session)
                session["accepts"].clear()
                self._stats["expired"] += 1
                return True
        return False

    @staticmethod
    def _new_offer(session: dict, version: int, proposer: str,
                   give: tuple, want: tuple, now: datetime,
                   ttl: int) -> OfferVersion:
        return OfferVersion(
            version=version,
            proposer=proposer,
            give=give,
            want=want,
            created_at=TradeOfferNegotiation._format_time(now),
            expires_at=TradeOfferNegotiation._format_time(
                now + timedelta(seconds=ttl)),
        )

    def create_offer(self, *, session_id: str, proposer: str, give: dict,
                     want: dict, now: str) -> OfferVersion:
        with self._lock:
            session = self._session(session_id)
            self._check_party(session, proposer)
            if session["status"] == "expired":
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)
            if session["status"] in _CLOSED_STATUSES:
                raise TradeError("TRADE_SESSION_CLOSED", session["status"])
            if session["versions"]:
                raise TradeError("TRADE_OFFER_EXISTS", session_id)
            normalized_give = self._normalize_items(
                give, "TRADE_INVALID_OFFER", allow_empty=False)
            normalized_want = self._normalize_items(
                want, "TRADE_INVALID_OFFER", allow_empty=False)
            at = self._event_now(now)
            offer = self._new_offer(
                session, 1, proposer, normalized_give, normalized_want,
                at, self.offer_ttl)
            session["versions"].append(offer)
            self._stats["offers"] += 1
            return offer

    def counter_offer(self, *, session_id: str, proposer: str, give: dict,
                      want: dict, now: str) -> OfferVersion:
        with self._lock:
            session = self._session(session_id)
            self._check_party(session, proposer)
            if session["status"] == "expired":
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)
            if session["status"] in _CLOSED_STATUSES:
                raise TradeError("TRADE_SESSION_CLOSED", session["status"])
            at = self._event_now(now)
            if self._expire_if_needed(session, at):
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)
            if not session["versions"]:
                raise TradeError("TRADE_NO_OFFER", session_id)
            if len(session["versions"]) >= self.max_versions:
                raise TradeError("TRADE_VERSION_LIMIT", self.max_versions)
            latest = session["versions"][-1]
            if proposer == latest.proposer:
                raise TradeError("TRADE_OUT_OF_TURN", proposer)
            normalized_give = self._normalize_items(
                give, "TRADE_INVALID_OFFER", allow_empty=False)
            normalized_want = self._normalize_items(
                want, "TRADE_INVALID_OFFER", allow_empty=False)

            self._release_reservations(session)
            session["accepts"].clear()
            offer = self._new_offer(
                session, latest.version + 1, proposer, normalized_give,
                normalized_want, at, self.offer_ttl)
            session["versions"].append(offer)
            self._stats["counters"] += 1
            return offer

    def _fail_completion(self, session: dict, version: int,
                         detail: object) -> None:
        self._release_reservations(session)
        session["accepts"].pop(version, None)
        session["accepts"].clear()
        raise TradeError("TRADE_ITEM_OCCUPIED", detail)

    def accept(self, *, session_id: str, player: str, version: int,
               now: str) -> TradeResult:
        with self._lock:
            session = self._session(session_id)
            self._check_party(session, player)
            if session["status"] == "expired":
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)
            if session["status"] == "accepted":
                latest = session["versions"][-1]
                if (isinstance(version, bool)
                        or not isinstance(version, int)
                        or version != latest.version):
                    raise TradeError("TRADE_VERSION_CONFLICT", version)
                if player in session["accepts"].get(version, set()):
                    return TradeResult(status="completed", version=version)
                raise TradeError("TRADE_SESSION_CLOSED", session["status"])
            if session["status"] in _CLOSED_STATUSES:
                raise TradeError("TRADE_SESSION_CLOSED", session["status"])
            if not session["versions"]:
                raise TradeError("TRADE_NO_OFFER", session_id)
            latest = session["versions"][-1]
            if (isinstance(version, bool) or not isinstance(version, int)
                    or version != latest.version):
                raise TradeError("TRADE_VERSION_CONFLICT", version)
            at = self._event_now(now)
            if self._expire_if_needed(session, at):
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)

            accepted = session["accepts"].setdefault(version, set())
            if player in accepted:
                return TradeResult(status="pending", version=version)

            items = latest.give if player == latest.proposer else latest.want
            if not self._can_reserve(session, player, items):
                self._fail_completion(session, version, player)

            if not accepted:
                accepted.add(player)
                self._add_reservation(session, player, items)
                self._stats["pending_accepts"] += 1
                return TradeResult(status="pending", version=version)

            accepted.add(player)
            self._add_reservation(session, player, items)
            if not self._can_complete(session, latest):
                self._fail_completion(session, version, player)
            try:
                self._swap(session, latest)
            except TradeError:
                self._fail_completion(session, version, player)

            self._release_reservations(session)
            session["status"] = "accepted"
            self._stats["accepted"] += 1
            return TradeResult(status="completed", version=version)

    def reject(self, *, session_id: str, player: str,
               version: int | None = None,
               now: str | None = None) -> None:
        with self._lock:
            session = self._session(session_id)
            self._check_party(session, player)
            if session["status"] == "rejected":
                return
            if session["status"] in _CLOSED_STATUSES:
                raise TradeError("TRADE_SESSION_CLOSED", session["status"])
            at = self._resolve_now(now)
            if self._expire_if_needed(session, at):
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)
            if version is not None:
                if not session["versions"]:
                    raise TradeError("TRADE_NO_OFFER", session_id)
                if (isinstance(version, bool) or not isinstance(version, int)
                        or version != session["versions"][-1].version):
                    raise TradeError("TRADE_VERSION_CONFLICT", version)
            self._release_reservations(session)
            session["accepts"].clear()
            session["status"] = "rejected"
            self._stats["rejected"] += 1

    def cancel(self, *, session_id: str, player: str,
               now: str | None = None) -> None:
        with self._lock:
            session = self._session(session_id)
            self._check_party(session, player)
            if session["status"] == "cancelled":
                return
            if session["status"] in _CLOSED_STATUSES:
                raise TradeError("TRADE_SESSION_CLOSED", session["status"])
            at = self._resolve_now(now)
            if self._expire_if_needed(session, at):
                raise TradeError("TRADE_OFFER_EXPIRED", session_id)
            self._release_reservations(session)
            session["accepts"].clear()
            session["status"] = "cancelled"
            self._stats["cancelled"] += 1

    def state(self, *, session_id: str, now: str | None = None) -> str:
        with self._lock:
            session = self._session(session_id)
            self._expire_if_needed(session, self._resolve_now(now))
            return session["status"]

    def versions_of(self, session_id: str) -> list:
        with self._lock:
            session = self._session(session_id)
            return list(session["versions"])

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    @staticmethod
    def _offer_to_state(offer: OfferVersion) -> dict:
        return {
            "version": offer.version,
            "proposer": offer.proposer,
            "give": [[name, qty] for name, qty in offer.give],
            "want": [[name, qty] for name, qty in offer.want],
            "created_at": offer.created_at,
            "expires_at": offer.expires_at,
        }

    def snapshot(self) -> dict:
        with self._lock:
            sessions = {}
            for session_id, session in self._sessions.items():
                sessions[session_id] = {
                    "session_id": session["session_id"],
                    "player_a": session["player_a"],
                    "player_b": session["player_b"],
                    "status": session["status"],
                    "created_at": session["created_at"],
                    "versions": [
                        self._offer_to_state(offer)
                        for offer in session["versions"]
                    ],
                    "accepts": {
                        str(version): sorted(players)
                        for version, players in session["accepts"].items()
                        if players
                    },
                    "reservations": {
                        player: [[name, qty] for name, qty in sorted(items.items())]
                        for player, items in session["reservations"].items()
                    },
                }
            inventories = {
                player: dict(sorted(items.items()))
                for player, items in self._inventories.items()
            }
            return {
                "version": 1,
                "sessions": sessions,
                "inventories": inventories,
                "stats": dict(self._stats),
            }

    @staticmethod
    def _snapshot_error(detail: object = None) -> TradeError:
        return TradeError("TRADE_SNAPSHOT_INVALID", detail)

    def _decode_snapshot(self, state: object) -> tuple:
        if not isinstance(state, dict):
            raise self._snapshot_error("state must be a mapping")
        if set(state) != {"version", "sessions", "inventories", "stats"}:
            raise self._snapshot_error("snapshot keys")
        if (isinstance(state["version"], bool)
                or not isinstance(state["version"], int)
                or state["version"] != 1):
            raise self._snapshot_error("snapshot version")

        raw_inventories = state["inventories"]
        if not isinstance(raw_inventories, dict):
            raise self._snapshot_error("inventories")
        inventories = {}
        for player, items in raw_inventories.items():
            if not self._is_name(player):
                raise self._snapshot_error("inventory player")
            if not isinstance(items, dict):
                raise self._snapshot_error("inventory items")
            try:
                normalized = self._normalize_items(
                    items, "TRADE_INVALID_INVENTORY", allow_empty=True)
            except TradeError as exc:
                raise self._snapshot_error(exc.detail) from exc
            inventories[player] = dict(normalized)

        raw_stats = state["stats"]
        if not isinstance(raw_stats, dict) or set(raw_stats) != set(_STAT_KEYS):
            raise self._snapshot_error("stats")
        stats = {}
        for key in _STAT_KEYS:
            value = raw_stats[key]
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise self._snapshot_error("stats value")
            stats[key] = value

        raw_sessions = state["sessions"]
        if not isinstance(raw_sessions, dict):
            raise self._snapshot_error("sessions")
        sessions = {}
        reserved = {}
        for session_id, raw in raw_sessions.items():
            if not self._is_name(session_id):
                raise self._snapshot_error("session id")
            if not isinstance(raw, dict):
                raise self._snapshot_error("session record")
            required = {
                "player_a", "player_b", "status", "versions",
                "accepts", "reservations",
            }
            if not required.issubset(raw):
                raise self._snapshot_error("session fields")
            if raw.get("session_id", session_id) != session_id:
                raise self._snapshot_error("session id mismatch")
            if ("created_at" in raw and raw["created_at"] is not None
                    and not isinstance(raw["created_at"], str)):
                raise self._snapshot_error("session created_at")
            player_a = raw["player_a"]
            player_b = raw["player_b"]
            if (not self._is_name(player_a) or not self._is_name(player_b)
                    or player_a == player_b):
                raise self._snapshot_error("session players")
            status = raw["status"]
            if status not in _STATUSES:
                raise self._snapshot_error("session status")

            raw_versions = raw["versions"]
            if not isinstance(raw_versions, list):
                raise self._snapshot_error("versions")
            versions = []
            previous_proposer = None
            for index, item in enumerate(raw_versions, start=1):
                if not isinstance(item, dict):
                    raise self._snapshot_error("version record")
                version_fields = {
                    "version", "proposer", "give", "want",
                    "created_at", "expires_at",
                }
                if not version_fields.issubset(item):
                    raise self._snapshot_error("version fields")
                number = item["version"]
                if (isinstance(number, bool) or not isinstance(number, int)
                        or number != index):
                    raise self._snapshot_error("version number")
                proposer = item["proposer"]
                if proposer not in (player_a, player_b):
                    raise self._snapshot_error("version proposer")
                if previous_proposer is not None and proposer == previous_proposer:
                    raise self._snapshot_error("version rotation")
                previous_proposer = proposer
                try:
                    give = self._normalize_pairs(
                        item["give"], "TRADE_INVALID_OFFER", allow_empty=False)
                    want = self._normalize_pairs(
                        item["want"], "TRADE_INVALID_OFFER", allow_empty=False)
                except TradeError as exc:
                    raise self._snapshot_error(exc.detail) from exc
                try:
                    created_at = self._parse_time(item["created_at"])
                    expires_at = self._parse_time(item["expires_at"])
                except TradeError as exc:
                    raise self._snapshot_error(exc.detail) from exc
                if expires_at < created_at:
                    raise self._snapshot_error("offer expiry")
                versions.append(OfferVersion(
                    version=number,
                    proposer=proposer,
                    give=give,
                    want=want,
                    created_at=self._format_time(created_at),
                    expires_at=self._format_time(expires_at),
                ))

            raw_accepts = raw["accepts"]
            if not isinstance(raw_accepts, dict):
                raise self._snapshot_error("accepts")
            accepts = {}
            for version_key, players in raw_accepts.items():
                try:
                    version_number = int(version_key)
                except (TypeError, ValueError) as exc:
                    raise self._snapshot_error("accept version") from exc
                if (not versions or version_number < 1
                        or version_number > len(versions)
                        or version_number != versions[-1].version):
                    raise self._snapshot_error("accept version")
                if not isinstance(players, list) or not players:
                    raise self._snapshot_error("accept players")
                accepted_players = set()
                for player in players:
                    if player not in (player_a, player_b) or player in accepted_players:
                        raise self._snapshot_error("accept player")
                    accepted_players.add(player)
                accepts[version_number] = accepted_players
            if accepts and status not in {"open", "accepted"}:
                raise self._snapshot_error("accepts on closed session")

            raw_reservations = raw["reservations"]
            if not isinstance(raw_reservations, dict):
                raise self._snapshot_error("reservations")
            reservations = {}
            for player, items in raw_reservations.items():
                if player not in (player_a, player_b):
                    raise self._snapshot_error("reservation player")
                try:
                    normalized = self._normalize_pairs(
                        items, "TRADE_INVALID_OFFER", allow_empty=False)
                except TradeError as exc:
                    raise self._snapshot_error(exc.detail) from exc
                reservations[player] = dict(normalized)
            if reservations and status != "open":
                raise self._snapshot_error("reservations on closed session")

            latest = versions[-1] if versions else None
            if status == "open":
                if len(accepts) > 1:
                    raise self._snapshot_error("too many accepted versions")
                if accepts:
                    accepted_players = next(iter(accepts.values()))
                    if len(accepted_players) != 1:
                        raise self._snapshot_error("open session accepts")
                    player = next(iter(accepted_players))
                    expected = (latest.give if player == latest.proposer
                                else latest.want)
                    if (set(reservations) != {player}
                            or reservations[player] != dict(expected)):
                        raise self._snapshot_error("accept reservation mismatch")
                elif reservations:
                    raise self._snapshot_error("reservation without accept")
            elif status == "accepted":
                if (latest is None
                        or accepts.get(latest.version) != {player_a, player_b}
                        or reservations):
                    raise self._snapshot_error("accepted session state")
            elif accepts or reservations:
                raise self._snapshot_error("closed session state")

            sessions[session_id] = {
                "session_id": session_id,
                "player_a": player_a,
                "player_b": player_b,
                "status": status,
                "created_at": raw.get("created_at"),
                "versions": versions,
                "accepts": accepts,
                "reservations": reservations,
            }
            for player, items in reservations.items():
                aggregate = reserved.setdefault(player, {})
                for item, qty in items.items():
                    aggregate[item] = aggregate.get(item, 0) + qty

        return sessions, inventories, stats, reserved

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                sessions, inventories, stats, reserved = \
                    self._decode_snapshot(state)
            except TradeError as exc:
                if exc.code == "TRADE_SNAPSHOT_INVALID":
                    raise
                raise TradeError("TRADE_SNAPSHOT_INVALID", exc.detail) from exc
            except Exception as exc:
                raise TradeError("TRADE_SNAPSHOT_INVALID", str(exc)) from exc
            self._sessions = sessions
            self._inventories = inventories
            self._stats = stats
            self._reserved = reserved
