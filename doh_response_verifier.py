"""DNS-over-HTTPS answer verification, resolver quorum and response cache."""

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from typing import Any


_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_QTYPES = frozenset({"A", "AAAA", "TXT"})
_STAT_KEYS = (
    "queries",
    "submissions",
    "rejected",
    "resolved",
    "cache_writes",
    "cache_hits",
    "cache_evictions",
)


class DohVerifyError(ValueError):
    """Raised when a DoH query, submission or resolution is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DohResponseVerifier:
    """Validate DoH answers against the query and require resolver agreement."""

    def __init__(self, *, min_ttl: int = 30, max_ttl: int = 3600,
                 quorum: int = 2, max_resolvers: int = 8,
                 max_cache: int = 128, chain_depth: int = 8) -> None:
        self.min_ttl = min_ttl
        self.max_ttl = max_ttl
        self.quorum = quorum
        self.max_resolvers = max_resolvers
        self.max_cache = max_cache
        self.chain_depth = chain_depth

        self._lock = threading.RLock()
        self._resolvers: dict[str, dict[str, Any]] = {}
        self._queries: dict[str, dict[str, Any]] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._query_seq = 0
        self._stats = {key: 0 for key in _STAT_KEYS}

    def register_resolver(self, resolver_id: str, endpoint: str, *,
                          spki_sha256: str, now: int = 0) -> dict:
        with self._lock:
            if (not isinstance(resolver_id, str) or not resolver_id
                    or not isinstance(endpoint, str)
                    or not endpoint.startswith("https://")
                    or not isinstance(spki_sha256, str)
                    or _HEX_SHA256.fullmatch(spki_sha256) is None):
                raise DohVerifyError(
                    "DOH_RESOLVER_INVALID",
                    {"resolver_id": resolver_id, "endpoint": endpoint},
                )

            if resolver_id in self._resolvers:
                raise DohVerifyError(
                    "DOH_RESOLVER_EXISTS",
                    {"resolver_id": resolver_id},
                )

            if len(self._resolvers) >= self.max_resolvers:
                raise DohVerifyError(
                    "DOH_CAPACITY",
                    {
                        "resolver_id": resolver_id,
                        "max_resolvers": self.max_resolvers,
                    },
                )

            record = {
                "resolver_id": resolver_id,
                "endpoint": endpoint,
                "spki_sha256": spki_sha256,
                "registered_at": now,
                "retired": False,
                "retired_at": None,
            }
            self._resolvers[resolver_id] = record
            return deepcopy(record)

    def retire_resolver(self, resolver_id: str, *, now: int = 0) -> bool:
        with self._lock:
            record = self._resolvers.get(resolver_id)
            if record is None:
                raise DohVerifyError(
                    "DOH_RESOLVER_UNKNOWN",
                    {"resolver_id": resolver_id},
                )
            if record["retired"]:
                return False

            record["retired"] = True
            record["retired_at"] = now
            return True

    def resolvers(self) -> list:
        with self._lock:
            return deepcopy(list(self._resolvers.values()))

    def begin_query(self, name: str, qtype: str, *, nonce: str,
                    now: int = 0) -> dict:
        with self._lock:
            normalized_name = self._normalize_name(name)
            normalized_qtype = self._normalize_qtype(qtype)

            if (normalized_name is None or not normalized_name
                    or normalized_qtype is None
                    or not isinstance(nonce, str) or not nonce):
                raise DohVerifyError(
                    "DOH_QUERY_INVALID",
                    {"name": name, "qtype": qtype, "nonce": nonce},
                )

            if any(query["nonce"] == nonce
                   for query in self._queries.values()):
                raise DohVerifyError(
                    "DOH_NONCE_REUSED",
                    {"nonce": nonce},
                )

            self._query_seq += 1
            query_id = f"q-{self._query_seq}"
            query = {
                "query_id": query_id,
                "name": normalized_name,
                "qtype": normalized_qtype,
                "nonce": nonce,
                "created_at": now,
                "ballots": {},
                "resolved": False,
                "resolved_at": None,
                "result": None,
            }
            self._queries[query_id] = query
            self._stats["queries"] += 1
            return deepcopy({
                "query_id": query_id,
                "name": normalized_name,
                "qtype": normalized_qtype,
                "nonce": nonce,
                "created_at": now,
            })

    def submit(self, query_id: str, resolver_id: str, response: dict, *,
               now: int = 0) -> dict:
        with self._lock:
            self._stats["submissions"] += 1
            try:
                return self._submit_locked(
                    query_id, resolver_id, response, now=now
                )
            except DohVerifyError:
                self._stats["rejected"] += 1
                raise

    def resolve(self, query_id: str, *, now: int = 0) -> dict:
        with self._lock:
            query = self._queries.get(query_id)
            if query is None:
                raise DohVerifyError(
                    "DOH_QUERY_UNKNOWN",
                    {"query_id": query_id},
                )

            if query["result"] is not None:
                return deepcopy(query["result"])

            ballots = list(query["ballots"].values())
            if len(ballots) < self.quorum:
                raise DohVerifyError(
                    "DOH_QUORUM_UNMET",
                    {
                        "query_id": query_id,
                        "votes": len(ballots),
                        "quorum": self.quorum,
                    },
                )

            groups: dict[str, list[dict[str, Any]]] = {}
            for ballot in ballots:
                groups.setdefault(ballot["vote_key"], []).append(ballot)

            if not groups:
                raise DohVerifyError(
                    "DOH_QUORUM_UNMET",
                    {"query_id": query_id, "votes": 0,
                     "quorum": self.quorum},
                )

            ranked_groups = sorted(
                groups.values(),
                key=lambda group: len(group),
                reverse=True,
            )
            top_count = len(ranked_groups[0])
            top_groups = [
                group for group in ranked_groups
                if len(group) == top_count
            ]

            if len(top_groups) > 1:
                ad_groups = [
                    group for group in top_groups
                    if any(ballot["validated"]["ad"]
                           for ballot in group)
                ]
                if len(ad_groups) == 1:
                    winning_ballots = ad_groups[0]
                else:
                    raise DohVerifyError(
                        "DOH_CONFLICT",
                        {
                            "query_id": query_id,
                            "groups": len(top_groups),
                            "votes": top_count,
                        },
                    )
            else:
                winning_ballots = top_groups[0]

            votes = len(winning_ballots)
            if votes < self.quorum:
                raise DohVerifyError(
                    "DOH_QUORUM_UNMET",
                    {
                        "query_id": query_id,
                        "votes": votes,
                        "quorum": self.quorum,
                    },
                )

            ttl = min(
                ballot["validated"]["ttl"]
                for ballot in winning_ballots
            )
            ttl_source = min(
                winning_ballots,
                key=lambda ballot: ballot["validated"]["ttl"],
            )
            ad = any(
                ballot["validated"]["ad"]
                for ballot in winning_ballots
            )
            result = {
                "name": query["name"],
                "qtype": query["qtype"],
                "answers": deepcopy(
                    ttl_source["validated"]["answers"]
                ),
                "ttl": ttl,
                "negative": winning_ballots[0]["validated"]["negative"],
                "votes": votes,
                "ad": ad,
            }

            cache_key = self._cache_key(query["name"], query["qtype"])
            entry = deepcopy(result)
            entry["expires_at"] = now + ttl

            # Refresh write order when the same normalized key is rewritten.
            self._cache.pop(cache_key, None)
            self._cache[cache_key] = entry
            while self._cache and len(self._cache) > self.max_cache:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._stats["cache_evictions"] += 1

            self._stats["cache_writes"] += 1
            self._stats["resolved"] += 1
            query["resolved"] = True
            query["resolved_at"] = now
            query["result"] = deepcopy(result)
            return deepcopy(result)

    def cache_get(self, name: str, qtype: str, *, now: int = 0):
        with self._lock:
            cache_key = self._cache_key(name, qtype)
            if cache_key is None:
                return None

            entry = self._cache.get(cache_key)
            if entry is None:
                return None

            if now >= entry["expires_at"]:
                del self._cache[cache_key]
                return None

            self._stats["cache_hits"] += 1
            return deepcopy(entry)

    def flush(self, *, now: int = 0) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy({
                "version": 1,
                "query_seq": self._query_seq,
                "resolvers": self._resolvers,
                "queries": self._queries,
                "cache": self._cache,
                "stats": self._stats,
            })

    def restore(self, state: dict) -> None:
        with self._lock:
            loaded = self._build_restored_state(state)
            self._resolvers = loaded["resolvers"]
            self._queries = loaded["queries"]
            self._cache = loaded["cache"]
            self._query_seq = loaded["query_seq"]
            self._stats = loaded["stats"]

    def stats(self) -> dict:
        with self._lock:
            return deepcopy(self._stats)

    def _submit_locked(self, query_id: str, resolver_id: str,
                       response: dict, *, now: int) -> dict:
        query = self._queries.get(query_id)
        if query is None:
            raise DohVerifyError(
                "DOH_QUERY_UNKNOWN",
                {"query_id": query_id},
            )

        resolver = self._resolvers.get(resolver_id)
        if resolver is None:
            raise DohVerifyError(
                "DOH_RESOLVER_UNKNOWN",
                {"resolver_id": resolver_id},
            )
        if resolver["retired"]:
            raise DohVerifyError(
                "DOH_RESOLVER_RETIRED",
                {"resolver_id": resolver_id},
            )

        validated = self._validate_response_locked(query, response)
        fingerprint = self._response_fingerprint(validated)
        existing = query["ballots"].get(resolver_id)
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                raise DohVerifyError(
                    "DOH_RESOLVER_CONFLICT",
                    {
                        "query_id": query_id,
                        "resolver_id": resolver_id,
                    },
                )
            duplicate = deepcopy(existing["public"])
            duplicate["duplicate"] = True
            return duplicate

        vote_key = self._vote_key(validated)
        public = {
            "query_id": query_id,
            "resolver_id": resolver_id,
            "duplicate": False,
            "name": query["name"],
            "qtype": query["qtype"],
            "status": validated["status"],
            "ad": validated["ad"],
            "negative": validated["negative"],
            "ttl": validated["ttl"],
            "answers": deepcopy(validated["answers"]),
        }
        query["ballots"][resolver_id] = {
            "fingerprint": fingerprint,
            "vote_key": vote_key,
            "validated": deepcopy(validated),
            "public": deepcopy(public),
            "submitted_at": now,
        }
        return public

    def _validate_response_locked(self, query: dict[str, Any],
                                  response: dict) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise DohVerifyError(
                "DOH_RESPONSE_INVALID",
                {"response_type": type(response).__name__},
            )

        required = ("status", "content_type", "question", "answers")
        if any(key not in response for key in required):
            raise DohVerifyError(
                "DOH_RESPONSE_INVALID",
                {
                    "missing": [
                        key for key in required if key not in response
                    ],
                },
            )

        if response["content_type"] != "application/dns-json":
            raise DohVerifyError(
                "DOH_CONTENT_TYPE",
                {"content_type": response["content_type"]},
            )

        status = response["status"]
        if not isinstance(status, int) or isinstance(status, bool):
            raise DohVerifyError(
                "DOH_RESPONSE_INVALID",
                {"status": status},
            )

        if status == 2 or status not in (0, 3):
            raise DohVerifyError(
                "DOH_UPSTREAM_FAILURE",
                {"status": status},
            )

        question = response["question"]
        if (not isinstance(question, dict)
                or question.get("name") != query["name"]
                or question.get("qtype") != query["qtype"]):
            raise DohVerifyError(
                "DOH_QUESTION_MISMATCH",
                {
                    "expected_name": query["name"],
                    "expected_qtype": query["qtype"],
                    "question": question,
                },
            )

        raw_answers = response["answers"]
        if not isinstance(raw_answers, list):
            raise DohVerifyError(
                "DOH_RESPONSE_INVALID",
                {"answers_type": type(raw_answers).__name__},
            )

        ad = response.get("ad", False) is True

        if status == 3:
            if raw_answers:
                raise DohVerifyError(
                    "DOH_RESPONSE_INVALID",
                    {"reason": "NXDOMAIN answers must be empty"},
                )
            ttl = self._clamp_ttl(
                response.get("soa_ttl", self.min_ttl)
            )
            return {
                "status": status,
                "negative": True,
                "ad": ad,
                "answers": [],
                "ttl": ttl,
            }

        answers: list[dict[str, Any]] = []
        for record in raw_answers:
            if (not isinstance(record, dict)
                    or any(key not in record
                           for key in ("name", "type", "data"))):
                raise DohVerifyError(
                    "DOH_RESPONSE_INVALID",
                    {"record": record},
                )
            if "ttl" not in record:
                raise DohVerifyError(
                    "DOH_TTL_INVALID",
                    {"ttl": None},
                )

            ttl = self._clamp_ttl(record["ttl"])
            normalized_record = deepcopy(record)
            normalized_record["ttl"] = ttl
            answers.append(normalized_record)

        self._validate_chain(
            query["name"], query["qtype"], answers
        )
        ttl = min(
            (record["ttl"] for record in answers),
            default=self.min_ttl,
        )
        return {
            "status": status,
            "negative": False,
            "ad": ad,
            "answers": answers,
            "ttl": ttl,
        }

    def _validate_chain(self, query_name: str, qtype: str,
                        answers: list[dict[str, Any]]) -> None:
        if not answers:
            return

        for record in answers:
            if record.get("type") not in ("CNAME", qtype):
                raise DohVerifyError(
                    "DOH_CHAIN_BROKEN",
                    {
                        "name": record.get("name"),
                        "type": record.get("type"),
                        "qtype": qtype,
                    },
                )

        reachable = {query_name}
        current: Any = query_name
        hops = 0
        seen: set[Any] = set()
        chain_error: DohVerifyError | None = None

        while True:
            if not isinstance(current, str) or current in seen:
                chain_error = DohVerifyError(
                    "DOH_CHAIN_BROKEN",
                    {"name": current, "reason": "cycle"},
                )
                break
            seen.add(current)

            cname_records = [
                record for record in answers
                if record.get("type") == "CNAME"
                and record.get("name") == current
            ]
            if cname_records:
                targets = []
                for record in cname_records:
                    target = record.get("data")
                    if not isinstance(target, str) or not target:
                        chain_error = DohVerifyError(
                            "DOH_CHAIN_BROKEN",
                            {"record": record},
                        )
                        break
                    targets.append(target)

                if chain_error is not None:
                    break
                if len(set(targets)) != 1:
                    chain_error = DohVerifyError(
                        "DOH_CHAIN_BROKEN",
                        {"name": current, "targets": targets},
                    )
                    break

                target = targets[0]
                hops += 1
                if hops > self.chain_depth:
                    raise DohVerifyError(
                        "DOH_CHAIN_TOO_DEEP",
                        {
                            "hops": hops,
                            "chain_depth": self.chain_depth,
                        },
                    )

                reachable.add(target)
                current = target
                continue

            terminal_records = [
                record for record in answers
                if record.get("type") == qtype
                and record.get("name") == current
            ]
            if terminal_records:
                break

            chain_error = DohVerifyError(
                "DOH_CHAIN_BROKEN",
                {"missing_link_from": current},
            )
            break

        for record in answers:
            if record.get("name") not in reachable:
                raise DohVerifyError(
                    "DOH_QUESTION_MISMATCH",
                    {
                        "record_name": record.get("name"),
                        "query_name": query_name,
                    },
                )

        if chain_error is not None:
            raise chain_error

    def _clamp_ttl(self, ttl: object) -> int:
        if (not isinstance(ttl, int) or isinstance(ttl, bool)
                or ttl < 0):
            raise DohVerifyError(
                "DOH_TTL_INVALID",
                {"ttl": ttl},
            )
        return max(self.min_ttl, min(self.max_ttl, ttl))

    @staticmethod
    def _normalize_name(name: object) -> str | None:
        if not isinstance(name, str):
            return None
        return name.lower().rstrip(".")

    @staticmethod
    def _normalize_qtype(qtype: object) -> str | None:
        if not isinstance(qtype, str):
            return None
        normalized = qtype.upper()
        if normalized not in _QTYPES:
            return None
        return normalized

    def _cache_key(self, name: object, qtype: object) -> str | None:
        normalized_name = self._normalize_name(name)
        normalized_qtype = self._normalize_qtype(qtype)
        if normalized_name is None or normalized_qtype is None:
            return None
        return f"{normalized_name}\x1f{normalized_qtype}"

    @staticmethod
    def _vote_key(validated: dict[str, Any]) -> str:
        identities = sorted(
            json.dumps(
                [
                    record.get("name"),
                    record.get("type"),
                    record.get("data"),
                ],
                sort_keys=True,
                separators=(",", ":"),
                default=repr,
            )
            for record in validated["answers"]
        )
        return json.dumps(
            [validated["negative"], identities],
            separators=(",", ":"),
        )

    @staticmethod
    def _response_fingerprint(validated: dict[str, Any]) -> str:
        return json.dumps(
            {
                "status": validated["status"],
                "negative": validated["negative"],
                "ad": validated["ad"],
                "ttl": validated["ttl"],
                "answers": validated["answers"],
            },
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        )

    def _build_restored_state(self, state: object) -> dict[str, Any]:
        try:
            if not isinstance(state, dict):
                raise DohVerifyError(
                    "DOH_SNAPSHOT_INVALID",
                    {"state_type": type(state).__name__},
                )

            required = ("resolvers", "queries", "cache", "stats")
            if any(key not in state for key in required):
                raise DohVerifyError(
                    "DOH_SNAPSHOT_INVALID",
                    {
                        "missing": [
                            key for key in required if key not in state
                        ],
                    },
                )

            resolvers = deepcopy(state["resolvers"])
            queries = deepcopy(state["queries"])
            raw_cache = deepcopy(state["cache"])
            stats = deepcopy(state["stats"])

            if (not isinstance(resolvers, dict)
                    or not isinstance(queries, dict)
                    or not isinstance(stats, dict)
                    or not isinstance(raw_cache, (dict, list))):
                raise DohVerifyError(
                    "DOH_SNAPSHOT_INVALID",
                    {"reason": "invalid snapshot collection type"},
                )

            for resolver_id, record in resolvers.items():
                if (not isinstance(resolver_id, str)
                        or not isinstance(record, dict)):
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"resolver_id": resolver_id},
                    )

            nonces: set[str] = set()
            for query_id, query in queries.items():
                if (not isinstance(query_id, str)
                        or not isinstance(query, dict)
                        or not isinstance(query.get("ballots"), dict)):
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"query_id": query_id},
                    )
                nonce = query.get("nonce")
                if (not isinstance(nonce, str)
                        or not nonce
                        or nonce in nonces):
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"query_id": query_id, "nonce": nonce},
                    )
                nonces.add(nonce)

            cache_entries = (
                list(raw_cache.values())
                if isinstance(raw_cache, dict)
                else raw_cache
            )
            cache: dict[str, dict[str, Any]] = {}
            for entry in cache_entries:
                if not isinstance(entry, dict):
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"cache_entry": entry},
                    )
                cache_key = self._cache_key(
                    entry.get("name"), entry.get("qtype")
                )
                if cache_key is None:
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"cache_entry": entry},
                    )
                cache[cache_key] = deepcopy(entry)

            restored_stats: dict[str, int] = {}
            for key in _STAT_KEYS:
                if key not in stats:
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"missing_stat": key},
                    )
                value = stats[key]
                if (not isinstance(value, int) or isinstance(value, bool)
                        or value < 0):
                    raise DohVerifyError(
                        "DOH_SNAPSHOT_INVALID",
                        {"stat": key, "value": value},
                    )
                restored_stats[key] = value

            query_seq = state.get("query_seq")
            if query_seq is None:
                numbers = []
                for query_id in queries:
                    if query_id.startswith("q-") and query_id[2:].isdigit():
                        numbers.append(int(query_id[2:]))
                query_seq = max(numbers, default=0)
            elif (not isinstance(query_seq, int)
                  or isinstance(query_seq, bool)
                  or query_seq < 0):
                raise DohVerifyError(
                    "DOH_SNAPSHOT_INVALID",
                    {"query_seq": query_seq},
                )

            return {
                "resolvers": resolvers,
                "queries": queries,
                "cache": cache,
                "query_seq": query_seq,
                "stats": restored_stats,
            }
        except DohVerifyError:
            raise
        except Exception as exc:
            raise DohVerifyError(
                "DOH_SNAPSHOT_INVALID",
                {"error": repr(exc)},
            ) from exc
