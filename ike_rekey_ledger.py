"""IKEv2 SA generation ledger with a message-id window and duplicate suppression."""

from __future__ import annotations

import copy
import threading


class IkeLedgerError(ValueError):
    """Raised when a rekey, message or deletion is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


_REKEY_REQUEST = "rekey_request"
_DELETE_REQUEST = "delete_request"
_INFORMATIONAL = "informational"
_KINDS = (_REKEY_REQUEST, _DELETE_REQUEST, _INFORMATIONAL)

_SA_ACTIVE = "active"
_SA_CLOSING = "closing"
_SA_DELETED = "deleted"
_SA_STATES = (_SA_ACTIVE, _SA_CLOSING, _SA_DELETED)

_STAT_KEYS = ("sas_created", "messages", "rekeys", "deletes",
              "duplicates", "rejected", "pruned")


class IkeRekeyLedger:
    """Track SA generations, control-message windows and rekey overlap windows."""

    def __init__(self, *, window_size: int = 64, max_sas: int = 32,
                 overlap_seconds: int = 120, max_initiators: int = 16,
                 sa_lifetime: int = 3600) -> None:
        self.window_size = window_size
        self.max_sas = max_sas
        self.overlap_seconds = overlap_seconds
        self.max_initiators = max_initiators
        self.sa_lifetime = sa_lifetime
        self._lock = threading.RLock()
        self._initiators: dict = {}
        self._stats = {key: 0 for key in _STAT_KEYS}

    def _fail(self, code: str, detail: object = None) -> None:
        self._stats["rejected"] += 1
        raise IkeLedgerError(code, detail)

    @staticmethod
    def _is_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def _known(self, initiator: object) -> str:
        name = initiator.strip() if isinstance(initiator, str) else ""
        if not name or name not in self._initiators:
            self._fail("IKE_UNKNOWN_INITIATOR", {"initiator": initiator})
        return name

    def _new_sa(self, generation: int, spi: str, now: int,
                nonce: object) -> dict:
        return {
            "generation": generation,
            "spi": spi,
            "state": _SA_ACTIVE,
            "created_at": now,
            "expires_at": now + self.sa_lifetime,
            "retire_at": None,
            "nonce": nonce,
        }

    @staticmethod
    def _sa_view(name: str, sa: dict) -> dict:
        return {
            "initiator": name,
            "generation": sa["generation"],
            "spi": sa["spi"],
            "state": sa["state"],
            "created_at": sa["created_at"],
            "expires_at": sa["expires_at"],
            "retire_at": sa["retire_at"],
            "nonce": sa.get("nonce"),
        }

    @staticmethod
    def _find_sa(entry: dict, generation: int) -> dict | None:
        for sa in entry["sas"]:
            if sa["generation"] == generation:
                return sa
        return None

    @staticmethod
    def _active_sa(entry: dict) -> dict:
        for sa in entry["sas"]:
            if sa["state"] == _SA_ACTIVE:
                return sa
        return entry["sas"][-1]

    def _advance_generation(self, name: str, now: int,
                            nonce: object) -> dict:
        entry = self._initiators[name]
        if len(entry["sas"]) >= self.max_sas:
            self._fail("IKE_SA_LIMIT", {"initiator": name,
                                        "max_sas": self.max_sas})
        generation = max(sa["generation"] for sa in entry["sas"]) + 1
        for sa in entry["sas"]:
            if sa["state"] == _SA_ACTIVE:
                sa["state"] = _SA_CLOSING
                sa["retire_at"] = now
        sa = self._new_sa(generation, "spi-%d" % generation, now, nonce)
        entry["sas"].append(sa)
        self._stats["rekeys"] += 1
        self._stats["sas_created"] += 1
        return sa

    def create_sa(self, initiator: str, *, now: int = 0,
                  spi: str | None = None) -> dict:
        with self._lock:
            name = initiator.strip() if isinstance(initiator, str) else ""
            if not name:
                self._fail("IKE_INITIATOR_INVALID", {"initiator": initiator})
            if name in self._initiators:
                self._fail("IKE_INITIATOR_EXISTS", {"initiator": name})
            if len(self._initiators) >= self.max_initiators:
                self._fail("IKE_INITIATOR_LIMIT", {"initiator": name})
            if spi is None:
                spi_value = "spi-1"
            else:
                spi_value = spi.strip() if isinstance(spi, str) else ""
                if not spi_value:
                    self._fail("IKE_SPI_INVALID", {"spi": spi})
            entry = {"sas": [], "seen": set(), "highest": -1, "cache": {}}
            sa = self._new_sa(1, spi_value, now, None)
            entry["sas"].append(sa)
            self._initiators[name] = entry
            self._stats["sas_created"] += 1
            return self._sa_view(name, sa)

    def _validate_payload(self, kind: str, payload: object) -> None:
        if kind == _REKEY_REQUEST:
            nonce = payload.get("nonce") if isinstance(payload, dict) else None
            if not isinstance(nonce, str) or not nonce.strip():
                self._fail("IKE_PAYLOAD_INVALID",
                           {"kind": kind, "payload": payload})
        elif kind == _DELETE_REQUEST:
            generation = (payload.get("generation")
                          if isinstance(payload, dict) else None)
            if not self._is_int(generation):
                self._fail("IKE_PAYLOAD_INVALID",
                           {"kind": kind, "payload": payload})

    def _apply(self, name: str, entry: dict, kind: str,
               payload: object, now: int) -> dict:
        if kind == _INFORMATIONAL:
            active = self._active_sa(entry)
            return {"action": "noted", "generation": active["generation"],
                    "duplicate": False}
        if kind == _REKEY_REQUEST:
            sa = self._advance_generation(name, now, payload.get("nonce"))
            return {"action": "rekeyed", "generation": sa["generation"],
                    "spi": sa["spi"], "duplicate": False}
        generation = payload["generation"]
        sa = self._find_sa(entry, generation)
        if sa is None:
            self._fail("IKE_SA_UNKNOWN", {"generation": generation})
        if sa["state"] == _SA_ACTIVE:
            self._fail("IKE_SA_ACTIVE", {"generation": generation})
        if sa["state"] == _SA_CLOSING:
            sa["state"] = _SA_DELETED
            self._stats["deletes"] += 1
        return {"action": "deleted", "generation": generation,
                "duplicate": False}

    def receive(self, initiator: str, message_id: int, kind: str,
                payload: dict | None = None, *, now: int = 0) -> dict:
        with self._lock:
            if not self._is_int(message_id) or message_id < 0:
                self._fail("IKE_MSG_INVALID", {"message_id": message_id})
            if kind not in _KINDS:
                self._fail("IKE_KIND_INVALID", {"kind": kind})
            name = self._known(initiator)
            self._validate_payload(kind, payload)
            entry = self._initiators[name]
            cached = entry["cache"].get(message_id)
            if cached is not None:
                if cached["kind"] != kind or cached["payload"] != payload:
                    self._fail("IKE_MSG_CONFLICT",
                               {"message_id": message_id})
                self._stats["duplicates"] += 1
                response = copy.deepcopy(cached["response"])
                response["duplicate"] = True
                return response
            highest = entry["highest"]
            if highest >= 0:
                if message_id > highest + self.window_size:
                    self._fail("IKE_MSG_OUT_OF_WINDOW",
                               {"message_id": message_id, "highest": highest})
                if message_id <= highest - self.window_size:
                    self._fail("IKE_MSG_TOO_OLD",
                               {"message_id": message_id, "highest": highest})
            response = self._apply(name, entry, kind, payload, now)
            entry["seen"].add(message_id)
            entry["highest"] = max(highest, message_id)
            entry["cache"][message_id] = {
                "kind": kind,
                "payload": copy.deepcopy(payload),
                "response": copy.deepcopy(response),
            }
            self._stats["messages"] += 1
            return response

    def rekey(self, initiator: str, *, now: int = 0,
              nonce: str | None = None) -> dict:
        with self._lock:
            name = self._known(initiator)
            sa = self._advance_generation(name, now, nonce)
            return self._sa_view(name, sa)

    def delete_sa(self, initiator: str, generation: int, *, now: int = 0) -> bool:
        with self._lock:
            name = self._known(initiator)
            sa = self._find_sa(self._initiators[name], generation)
            if sa is None:
                self._fail("IKE_SA_UNKNOWN", {"generation": generation})
            if sa["state"] == _SA_ACTIVE:
                self._fail("IKE_SA_ACTIVE", {"generation": generation})
            if sa["state"] == _SA_DELETED:
                return False
            sa["state"] = _SA_DELETED
            self._stats["deletes"] += 1
            return True

    def generations(self, initiator: str) -> list:
        with self._lock:
            name = self._known(initiator)
            entry = self._initiators[name]
            return [self._sa_view(name, sa) for sa in sorted(
                entry["sas"], key=lambda item: item["generation"])]

    def window(self, initiator: str) -> dict:
        with self._lock:
            name = self._known(initiator)
            entry = self._initiators[name]
            return {"highest": entry["highest"], "size": self.window_size,
                    "seen": sorted(entry["seen"])}

    def state(self, initiator: str) -> dict:
        with self._lock:
            name = self._known(initiator)
            entry = self._initiators[name]
            active = self._active_sa(entry)
            return {"active_generation": active["generation"],
                    "active_spi": active["spi"],
                    "highest": entry["highest"]}

    def prune(self, *, now: int = 0) -> int:
        with self._lock:
            removed = 0
            for entry in self._initiators.values():
                kept = []
                for sa in entry["sas"]:
                    if (sa["state"] == _SA_CLOSING
                            and sa["retire_at"] is not None
                            and now >= sa["retire_at"] + self.overlap_seconds):
                        removed += 1
                    else:
                        kept.append(sa)
                entry["sas"] = kept
            self._stats["pruned"] += removed
            return removed

    def snapshot(self) -> dict:
        with self._lock:
            initiators = {}
            for name, entry in self._initiators.items():
                initiators[name] = {
                    "sas": copy.deepcopy(entry["sas"]),
                    "window": {"highest": entry["highest"],
                               "seen": sorted(entry["seen"])},
                    "cache": copy.deepcopy(entry["cache"]),
                }
            return {
                "version": 1,
                "config": {
                    "window_size": self.window_size,
                    "max_sas": self.max_sas,
                    "overlap_seconds": self.overlap_seconds,
                    "max_initiators": self.max_initiators,
                    "sa_lifetime": self.sa_lifetime,
                },
                "initiators": initiators,
                "stats": dict(self._stats),
            }

    def _parse_snapshot(self, state: object) -> tuple:
        def invalid() -> None:
            self._fail("IKE_SNAPSHOT_INVALID",
                       {"reason": "malformed snapshot"})

        if not isinstance(state, dict):
            invalid()
        raw_initiators = state.get("initiators")
        raw_stats = state.get("stats")
        if not isinstance(raw_initiators, dict) or not isinstance(raw_stats, dict):
            invalid()
        for key in _STAT_KEYS:
            if not self._is_int(raw_stats.get(key)):
                invalid()
        initiators = {}
        for name, entry in raw_initiators.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                invalid()
            sas = entry.get("sas")
            window = entry.get("window")
            cache = entry.get("cache")
            if (not isinstance(sas, list) or not isinstance(window, dict)
                    or not isinstance(cache, dict)):
                invalid()
            parsed_sas = []
            for sa in sas:
                if not isinstance(sa, dict):
                    invalid()
                if (not self._is_int(sa.get("generation"))
                        or not isinstance(sa.get("spi"), str)
                        or sa.get("state") not in _SA_STATES
                        or "created_at" not in sa
                        or "expires_at" not in sa
                        or "retire_at" not in sa):
                    invalid()
                parsed_sas.append(copy.deepcopy(sa))
            highest = window.get("highest")
            seen = window.get("seen")
            if not self._is_int(highest) or not isinstance(seen, list):
                invalid()
            seen_set = set()
            for message_id in seen:
                if not self._is_int(message_id):
                    invalid()
                seen_set.add(message_id)
            parsed_cache = {}
            for message_id, record in cache.items():
                if not self._is_int(message_id) or not isinstance(record, dict):
                    invalid()
                if "kind" not in record or "response" not in record:
                    invalid()
                parsed_cache[message_id] = copy.deepcopy(record)
            initiators[name] = {"sas": parsed_sas, "seen": seen_set,
                                "highest": highest, "cache": parsed_cache}
        stats = {key: raw_stats[key] for key in _STAT_KEYS}
        return initiators, stats

    def restore(self, state: dict) -> None:
        with self._lock:
            initiators, stats = self._parse_snapshot(state)
            self._initiators = initiators
            self._stats = stats

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)
