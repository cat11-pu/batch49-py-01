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


_SNAPSHOT_VERSION = 1

_COUNTER_NAMES = ("resolved", "nxdomain", "servfail", "cancelled",
                  "cache_hits", "late_dropped", "fallback", "penalized",
                  "tcp_retries")

_RESULT_KEYS = ("query", "status", "answers", "upstream", "rcode",
                "transport")


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
        self._upstreams: list[str] = []
        self._inflight: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, object] = {}
        self._results: dict[str, dict] = {}
        self._counters = {name: 0 for name in _COUNTER_NAMES}

    def set_upstreams(self, upstreams) -> int:
        if not isinstance(upstreams, (list, tuple)):
            raise ResolveError("RESOLVE_UPSTREAM_INVALID",
                               "upstreams must be a list of names")
        if not upstreams:
            raise ResolveError("RESOLVE_UPSTREAM_INVALID",
                               "upstreams must not be empty")
        names = []
        for item in upstreams:
            if not isinstance(item, str) or not item.strip():
                raise ResolveError("RESOLVE_UPSTREAM_INVALID",
                                   f"invalid upstream: {item!r}")
            names.append(item.strip())
        if len(set(names)) != len(names):
            raise ResolveError("RESOLVE_UPSTREAM_INVALID",
                               "duplicate upstream names")
        if len(names) > self.max_upstreams:
            raise ResolveError("RESOLVE_UPSTREAM_LIMIT",
                               f"at most {self.max_upstreams} upstreams")
        with self._lock:
            self._upstreams = names
            self._inflight = {name: 0 for name in names}
            self._failures = {name: 0 for name in names}
            self._cooldown_until = {name: None for name in names}
        return len(names)

    def upstreams(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._upstreams)

    def resolve(self, query: str, *, now: int) -> dict:
        qname = self._normalize_query(query)
        with self._lock:
            cached = self._results.get(qname)
            if cached is not None:
                self._counters["cache_hits"] += 1
                return dict(cached)
            if not self._upstreams:
                raise ResolveError("RESOLVE_UPSTREAM_INVALID",
                                   "no upstreams registered")
            candidates = [name for name in self._upstreams
                          if not self._in_cooldown(name, now)]
            if not candidates:
                candidates = list(self._upstreams)
                self._counters["fallback"] += 1
            responses = []
            for name in candidates:
                response = self._fetch(name, qname, "udp")
                if response is None:
                    self._note_failure(name, now)
                else:
                    responses.append((name, response))
            if not responses:
                self._counters["servfail"] += 1
                result = {"query": qname, "status": "servfail",
                          "answers": (), "upstream": None, "rcode": 2,
                          "transport": "udp"}
            else:
                winner_name, winner_response = responses[0]
                for name, response in responses[1:]:
                    if response["latency_ms"] < winner_response["latency_ms"]:
                        winner_name, winner_response = name, response
                self._counters["cancelled"] += len(responses) - 1
                if winner_response.get("truncated"):
                    result = self._tcp_fetch(qname, winner_name, now)
                else:
                    result = self._classify(qname, winner_name,
                                            winner_response, "udp", now)
            self._results[qname] = result
            return dict(result)

    def tcp_retry(self, query: str, upstream: str, *, now: int) -> dict:
        qname = self._normalize_query(query)
        with self._lock:
            if qname not in self._results:
                raise ResolveError("RESOLVE_QUERY_UNKNOWN",
                                   f"unknown query: {qname}")
            if upstream not in self._inflight:
                raise ResolveError("RESOLVE_UPSTREAM_UNKNOWN",
                                   f"unknown upstream: {upstream}")
            return self._tcp_fetch(qname, upstream, now)

    def health(self, upstream: str | None = None,
               *, now: int | None = None) -> dict:
        with self._lock:
            if upstream is not None:
                if upstream not in self._inflight:
                    raise ResolveError("RESOLVE_UPSTREAM_UNKNOWN",
                                       f"unknown upstream: {upstream}")
                return self._health_of(upstream, now)
            return {name: self._health_of(name, now)
                    for name in self._upstreams}

    def result_of(self, query: str) -> dict | None:
        if not isinstance(query, str):
            return None
        with self._lock:
            result = self._results.get(query.strip())
            return dict(result) if result is not None else None

    def deliver_late(self, query: str, upstream: str, response: dict,
                     *, now: int) -> bool:
        with self._lock:
            self._counters["late_dropped"] += 1
            return False

    def stats(self) -> dict:
        with self._lock:
            return dict(self._counters)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": _SNAPSHOT_VERSION,
                "upstreams": [
                    {"name": name,
                     "failures": self._failures.get(name, 0),
                     "cooldown_until": self._cooldown_until.get(name)}
                    for name in self._upstreams
                ],
                "results": {
                    qname: {**result, "answers": list(result["answers"])}
                    for qname, result in self._results.items()
                },
                "counters": dict(self._counters),
            }

    def restore(self, state: dict) -> None:
        parsed = self._validate_snapshot(state)
        with self._lock:
            self._upstreams = parsed["upstreams"]
            self._inflight = {name: 0 for name in parsed["upstreams"]}
            self._failures = parsed["failures"]
            self._cooldown_until = parsed["cooldown_until"]
            self._results = parsed["results"]
            self._counters = parsed["counters"]

    @staticmethod
    def _normalize_query(query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ResolveError("RESOLVE_QUERY_INVALID",
                               "query must be a non-empty string")
        return query.strip()

    def _expire_cooldown(self, name: str, now) -> None:
        until = self._cooldown_until.get(name)
        if until is not None and now is not None and now >= until:
            self._cooldown_until[name] = None
            self._failures[name] = 0

    def _in_cooldown(self, name: str, now) -> bool:
        self._expire_cooldown(name, now)
        return self._cooldown_until.get(name) is not None

    def _note_failure(self, name: str, now) -> None:
        self._expire_cooldown(name, now)
        self._failures[name] = self._failures.get(name, 0) + 1
        if (self._failures[name] >= self.penalty_threshold
                and self._cooldown_until.get(name) is None):
            self._cooldown_until[name] = now + self.cooldown_seconds
            self._counters["penalized"] += 1

    def _fetch(self, name: str, qname: str, transport: str):
        self._inflight[name] += 1
        try:
            return self._responder(name, qname, transport)
        except Exception:
            return None
        finally:
            self._inflight[name] -= 1

    def _classify(self, qname: str, name: str, response: dict,
                  transport: str, now) -> dict:
        rcode = response["rcode"]
        if rcode == 0:
            self._counters["resolved"] += 1
            return {"query": qname, "status": "ok",
                    "answers": tuple(response["answers"]),
                    "upstream": name, "rcode": rcode,
                    "transport": transport}
        if rcode == 3:
            self._counters["nxdomain"] += 1
            return {"query": qname, "status": "nxdomain", "answers": (),
                    "upstream": name, "rcode": rcode,
                    "transport": transport}
        self._counters["servfail"] += 1
        self._note_failure(name, now)
        return {"query": qname, "status": "servfail", "answers": (),
                "upstream": name, "rcode": rcode, "transport": transport}

    def _tcp_fetch(self, qname: str, name: str, now) -> dict:
        self._counters["tcp_retries"] += 1
        response = self._fetch(name, qname, "tcp")
        if response is None or response.get("truncated"):
            self._counters["servfail"] += 1
            self._note_failure(name, now)
            return {"query": qname, "status": "servfail", "answers": (),
                    "upstream": name, "rcode": 2, "transport": "tcp"}
        return self._classify(qname, name, response, "tcp", now)

    def _health_of(self, name: str, now) -> dict:
        self._expire_cooldown(name, now)
        until = self._cooldown_until.get(name)
        return {"upstream": name,
                "state": "cooldown" if until is not None else "ok",
                "inflight": self._inflight.get(name, 0),
                "failures": self._failures.get(name, 0),
                "cooldown_until": until}

    def _validate_snapshot(self, state) -> dict:
        def invalid(detail):
            raise ResolveError("RESOLVE_SNAPSHOT_INVALID", detail)

        if not isinstance(state, dict):
            invalid("snapshot must be a dict")
        if state.get("version") != _SNAPSHOT_VERSION:
            invalid("unsupported snapshot version")
        for key in ("upstreams", "results", "counters"):
            if key not in state:
                invalid(f"missing key: {key}")

        raw_upstreams = state["upstreams"]
        if not isinstance(raw_upstreams, list):
            invalid("upstreams must be a list")
        if len(raw_upstreams) > self.max_upstreams:
            invalid("too many upstreams")
        names = []
        failures = {}
        cooldowns = {}
        for entry in raw_upstreams:
            if not isinstance(entry, dict):
                invalid("upstream entry must be a dict")
            name = entry.get("name")
            fails = entry.get("failures")
            until = entry.get("cooldown_until")
            if not isinstance(name, str) or not name.strip():
                invalid("upstream name must be a non-empty string")
            if name in failures:
                invalid(f"duplicate upstream: {name}")
            if (not isinstance(fails, int) or isinstance(fails, bool)
                    or fails < 0):
                invalid("failures must be a non-negative int")
            if until is not None and (not isinstance(until, (int, float))
                                      or isinstance(until, bool)):
                invalid("cooldown_until must be a number or None")
            names.append(name)
            failures[name] = fails
            cooldowns[name] = until

        raw_results = state["results"]
        if not isinstance(raw_results, dict):
            invalid("results must be a dict")
        results = {}
        for qname, entry in raw_results.items():
            if not isinstance(qname, str) or not isinstance(entry, dict):
                invalid("result entries must be dicts keyed by query")
            if any(key not in entry for key in _RESULT_KEYS):
                invalid("result entry is missing fields")
            if not isinstance(entry["query"], str):
                invalid("result query must be a string")
            if not isinstance(entry["status"], str):
                invalid("result status must be a string")
            if not isinstance(entry["answers"], (list, tuple)):
                invalid("result answers must be a list")
            if (entry["upstream"] is not None
                    and not isinstance(entry["upstream"], str)):
                invalid("result upstream must be a string or None")
            if (not isinstance(entry["rcode"], int)
                    or isinstance(entry["rcode"], bool)):
                invalid("result rcode must be an int")
            if not isinstance(entry["transport"], str):
                invalid("result transport must be a string")
            results[qname] = {
                "query": entry["query"], "status": entry["status"],
                "answers": tuple(entry["answers"]),
                "upstream": entry["upstream"], "rcode": entry["rcode"],
                "transport": entry["transport"],
            }

        raw_counters = state["counters"]
        if not isinstance(raw_counters, dict):
            invalid("counters must be a dict")
        counters = {}
        for counter in _COUNTER_NAMES:
            value = raw_counters.get(counter)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                invalid(f"counter must be a non-negative int: {counter}")
            counters[counter] = value

        return {"upstreams": names, "failures": failures,
                "cooldown_until": cooldowns, "results": results,
                "counters": counters}
