"""Signed CVSS score bundles, weight-table integrity and revision ledger."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import threading


class CvssScoreError(ValueError):
    """Raised when a bundle, table or weight override is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


_TABLE_METRICS = ("AV", "AC", "PR_U", "PR_C", "UI", "C", "I", "A")
_BASE_METRICS = ("AV", "AC", "PR", "UI", "S", "C", "I", "A")
_BASE_LEVELS = {
    "AV": {"N", "A", "L", "P"},
    "AC": {"L", "H"},
    "PR": {"N", "L", "H"},
    "UI": {"N", "R"},
    "S": {"U", "C"},
    "C": {"H", "L", "N"},
    "I": {"H", "L", "N"},
    "A": {"H", "L", "N"},
}
_CVE_RE = re.compile(r"^CVE-\d{4}-\d+$")
_COUNTER_KEYS = (
    "ingested",
    "published",
    "duplicates",
    "quarantined",
    "signature_failures",
    "stale_revisions",
    "superseded",
    "overrides",
    "entries",
)


class _SnapshotStats(dict):
    """Stats mapping carrying non-JSON bookkeeping used by restore."""


def _canon(obj: object) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(obj: object) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


def _sign(secret: str, prefix: str, obj: object) -> str:
    return hmac.new(
        secret.encode("utf-8"), prefix.encode("utf-8") + _canon(obj),
        hashlib.sha256,
    ).hexdigest()


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class CvssScoreLedger:
    """Verify signed score bundles and recompute CVSS 3.1 base scores."""

    def __init__(self, *, max_entries: int = 128, max_keys: int = 4,
                 max_revisions: int = 8) -> None:
        self.max_entries = max_entries
        self.max_keys = max_keys
        self.max_revisions = max_revisions

        self._lock = threading.RLock()
        self._keys: dict[str, dict] = {}
        self._table: dict | None = None
        self._signed_table: dict | None = None
        self._table_digest: str | None = None
        self._entries: dict[str, list[dict]] = {}
        self._seen: dict[tuple[str, int], dict] = {}
        self._quarantine: list[str] = []
        self._counts = {
            "ingested": 0,
            "published": 0,
            "duplicates": 0,
            "quarantined": 0,
            "signature_failures": 0,
            "stale_revisions": 0,
            "superseded": 0,
            "overrides": 0,
        }

    # ------------------------------------------------------------------
    # Key and table administration

    def register_key(self, key_id: str, secret: str, *,
                     break_glass: bool = False) -> dict:
        with self._lock:
            if (
                not isinstance(key_id, str)
                or not key_id
                or not isinstance(secret, str)
                or len(secret) < 16
            ):
                raise CvssScoreError("CVSS_KEY_INVALID")
            if key_id in self._keys:
                raise CvssScoreError("CVSS_KEY_EXISTS")
            if len(self._keys) >= self.max_keys:
                raise CvssScoreError("CVSS_CAPACITY_EXCEEDED")
            self._keys[key_id] = {
                "secret": secret,
                "break_glass": bool(break_glass),
                "revoked": False,
            }
            return {"key_id": key_id, "break_glass": bool(break_glass)}

    def revoke_key(self, key_id: str) -> bool:
        with self._lock:
            if not isinstance(key_id, str):
                return False
            key = self._keys.get(key_id)
            if key is None or key["revoked"]:
                return False
            key["revoked"] = True
            return True

    def _validate_table(self, table: object) -> None:
        if not isinstance(table, dict):
            raise CvssScoreError("CVSS_TABLE_INVALID")
        for metric in _TABLE_METRICS:
            levels = table.get(metric)
            if not isinstance(levels, dict):
                raise CvssScoreError("CVSS_TABLE_INVALID")
            required = _BASE_LEVELS[
                "PR" if metric in ("PR_U", "PR_C") else metric
            ]
            if not required.issubset(levels):
                raise CvssScoreError("CVSS_TABLE_INVALID")
            for level, value in levels.items():
                if not isinstance(level, str) or not _is_number(value):
                    raise CvssScoreError("CVSS_TABLE_INVALID")

    def _active_key(self, key_id: str) -> dict | None:
        if not isinstance(key_id, str):
            return None
        key = self._keys.get(key_id)
        if key is None or key["revoked"]:
            return None
        return key

    def _verify(self, key: dict, prefix: str, obj: object,
                signature: object) -> bool:
        if not isinstance(signature, str):
            return False
        try:
            expected = _sign(key["secret"], prefix, obj)
        except (TypeError, ValueError):
            return False
        try:
            return hmac.compare_digest(expected, signature)
        except TypeError:
            return False

    def install_table(self, table: dict, *, key_id: str,
                      signature: str) -> dict:
        with self._lock:
            self._validate_table(table)
            key = self._active_key(key_id)
            if key is None or not self._verify(key, "table:", table, signature):
                raise CvssScoreError("CVSS_SIGNATURE_INVALID")

            installed = copy.deepcopy(table)
            self._table = copy.deepcopy(installed)
            self._signed_table = installed
            self._table_digest = _digest(installed)
            return {
                "digest": self._table_digest,
                "metrics": sorted(_TABLE_METRICS),
            }

    def _drifted_metrics(self) -> list[str]:
        if self._signed_table is None:
            return []
        metrics = set(self._signed_table) | set(self._table or {})
        return sorted(
            metric
            for metric in metrics
            if (self._table or {}).get(metric)
            != self._signed_table.get(metric)
        )

    def audit_table(self) -> dict:
        with self._lock:
            if self._signed_table is None:
                raise CvssScoreError("CVSS_TABLE_MISSING")
            drifted = self._drifted_metrics()
            return {
                "ok": not drifted,
                "drifted": drifted,
                "digest": _digest(self._table),
            }

    def override_weight(self, metric: str, level: str, value: float, *,
                        key_id: str, signature: str, now: int = 0) -> dict:
        del now
        with self._lock:
            if self._signed_table is None:
                raise CvssScoreError("CVSS_TABLE_MISSING")
            key = self._active_key(key_id)
            if key is None:
                raise CvssScoreError("CVSS_SIGNATURE_INVALID")
            if not key["break_glass"]:
                raise CvssScoreError("CVSS_OVERRIDE_DENIED")

            if (
                not isinstance(metric, str)
                or metric not in self._signed_table
                or not isinstance(self._signed_table[metric], dict)
                or not isinstance(level, str)
                or level not in self._signed_table[metric]
                or not _is_number(value)
            ):
                raise CvssScoreError("CVSS_TABLE_INVALID")

            body = {"metric": metric, "level": level, "value": value}
            if not self._verify(key, "override:", body, signature):
                raise CvssScoreError("CVSS_SIGNATURE_INVALID")

            self._table[metric][level] = value
            self._counts["overrides"] += 1
            return {
                "metric": metric,
                "level": level,
                "value": value,
                "drifted": bool(self._drifted_metrics()),
            }

    # ------------------------------------------------------------------
    # CVSS computation

    def _parse_vector(self, vector: object) -> dict:
        if not isinstance(vector, str) or not vector.startswith("CVSS:3.1/"):
            raise CvssScoreError("CVSS_VECTOR_INVALID")
        parts = vector[len("CVSS:3.1/"):].split("/")
        if not parts or any(not part for part in parts):
            raise CvssScoreError("CVSS_VECTOR_INVALID")

        parsed: dict[str, str] = {}
        for part in parts:
            pieces = part.split(":")
            if len(pieces) != 2:
                raise CvssScoreError("CVSS_VECTOR_INVALID")
            metric, level = pieces
            if metric not in _BASE_METRICS or not level:
                raise CvssScoreError("CVSS_VECTOR_INVALID")
            if metric in parsed:
                raise CvssScoreError("CVSS_VECTOR_INVALID")
            if metric == "S" and level not in _BASE_LEVELS["S"]:
                raise CvssScoreError("CVSS_VECTOR_INVALID")
            if metric != "S" and level not in _BASE_LEVELS[metric]:
                # A table may deliberately define additional levels, but
                # without a table there is no legitimate source for one.
                if self._table is None:
                    raise CvssScoreError("CVSS_VECTOR_INVALID")
            parsed[metric] = level

        if set(parsed) != set(_BASE_METRICS):
            raise CvssScoreError("CVSS_VECTOR_INVALID")

        table = self._table
        if table is None:
            raise CvssScoreError("CVSS_TABLE_MISSING")
        for metric in ("AV", "AC", "UI", "C", "I", "A"):
            if parsed[metric] not in table.get(metric, {}):
                raise CvssScoreError("CVSS_VECTOR_INVALID")
        pr_key = "PR_C" if parsed["S"] == "C" else "PR_U"
        if parsed["PR"] not in table.get(pr_key, {}):
            raise CvssScoreError("CVSS_VECTOR_INVALID")
        return parsed

    @staticmethod
    def _roundup(value: float) -> float:
        if value <= 0:
            return 0.0
        return math.ceil(round(value * 10, 9)) / 10.0

    def _compute_locked(self, parsed: dict) -> float:
        table = self._table
        assert table is not None

        weights = [
            table["C"][parsed["C"]],
            table["I"][parsed["I"]],
            table["A"][parsed["A"]],
        ]
        iss = 1.0 - (1.0 - weights[0]) * (1.0 - weights[1]) * (
            1.0 - weights[2]
        )
        scope_changed = parsed["S"] == "C"
        if scope_changed:
            impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
        else:
            impact = 6.42 * iss

        if impact <= 0:
            return 0.0

        pr_key = "PR_C" if scope_changed else "PR_U"
        exploitability = (
            8.22
            * table["AV"][parsed["AV"]]
            * table["AC"][parsed["AC"]]
            * table[pr_key][parsed["PR"]]
            * table["UI"][parsed["UI"]]
        )
        if scope_changed:
            base = min(1.08 * (impact + exploitability), 10.0)
        else:
            base = min(impact + exploitability, 10.0)
        return self._roundup(base)

    def compute_score(self, vector: str) -> float:
        with self._lock:
            return self._compute_locked(self._parse_vector(vector))

    @staticmethod
    def severity(score: float) -> str:
        if score <= 0.0:
            return "None"
        if score < 4.0:
            return "Low"
        if score < 7.0:
            return "Medium"
        if score < 9.0:
            return "High"
        return "Critical"

    # ------------------------------------------------------------------
    # Bundle ingestion and ledger reads

    @staticmethod
    def _public_record(record: dict) -> dict:
        return {
            key: copy.deepcopy(record[key])
            for key in ("cve", "vector", "score", "severity", "revised",
                        "key_id")
        }

    @staticmethod
    def _validate_score(score: object) -> bool:
        if not _is_number(score) or score < 0.0 or score > 10.0:
            return False
        return abs(score * 10.0 - round(score * 10.0)) <= 1e-9

    def ingest(self, cve: str, vector: str, score: float, *, revised: int,
               key_id: str, signature: str, now: int = 0) -> dict:
        del now
        with self._lock:
            body = {
                "cve": cve,
                "vector": vector,
                "score": score,
                "revised": revised,
            }
            key = self._active_key(key_id)
            if key is None or not self._verify(key, "bundle:", body, signature):
                self._counts["signature_failures"] += 1
                raise CvssScoreError("CVSS_SIGNATURE_INVALID")

            if self._signed_table is None:
                raise CvssScoreError("CVSS_TABLE_MISSING")
            if self._drifted_metrics():
                raise CvssScoreError("CVSS_TABLE_DRIFT")

            if not isinstance(cve, str) or _CVE_RE.fullmatch(cve) is None:
                raise CvssScoreError("CVSS_CVE_INVALID")
            if not _is_positive_int(revised):
                raise CvssScoreError("CVSS_REVISION_INVALID")

            history = self._entries.get(cve)
            seen = self._seen.get((cve, revised))
            if seen is not None and seen["signature"] == signature:
                self._counts["duplicates"] += 1
                out = copy.deepcopy(seen["record"])
                out["status"] = "duplicate"
                return out

            computed = self._compute_locked(self._parse_vector(vector))
            if not self._validate_score(score):
                raise CvssScoreError("CVSS_SCORE_INVALID")
            if abs(float(score) - computed) > 1e-9:
                if cve not in self._quarantine:
                    self._quarantine.append(cve)
                    self._counts["quarantined"] += 1
                raise CvssScoreError(
                    "CVSS_SCORE_MISMATCH",
                    {"claimed": score, "computed": computed},
                )

            if history and revised <= history[-1]["revised"]:
                self._counts["stale_revisions"] += 1
                raise CvssScoreError("CVSS_REVISION_STALE")

            if history is None and len(self._entries) >= self.max_entries:
                raise CvssScoreError("CVSS_CAPACITY_EXCEEDED")

            record = {
                "cve": cve,
                "vector": vector,
                "score": score,
                "severity": self.severity(score),
                "revised": revised,
                "key_id": key_id,
                "signature": signature,
            }
            if history is None:
                history = []
                self._entries[cve] = history
            history.append(record)
            self._seen[(cve, revised)] = {
                "signature": signature,
                "record": self._public_record(record),
            }
            if len(history) > self.max_revisions:
                removed = len(history) - self.max_revisions
                del history[:removed]
                self._counts["superseded"] += removed

            self._counts["ingested"] += 1
            self._counts["published"] += 1
            out = self._public_record(record)
            out["status"] = "published"
            return out

    def get(self, cve: str) -> dict | None:
        with self._lock:
            if not isinstance(cve, str):
                return None
            history = self._entries.get(cve)
            if not history:
                return None
            return self._public_record(history[-1])

    def history(self, cve: str) -> list:
        with self._lock:
            if not isinstance(cve, str):
                return []
            return [self._public_record(record)
                    for record in self._entries.get(cve, [])]

    def quarantined(self) -> list:
        with self._lock:
            return list(self._quarantine)

    def stats(self) -> dict:
        with self._lock:
            out = dict(self._counts)
            out["entries"] = len(self._entries)
            out["quarantined"] = len(self._quarantine)
            return out

    # ------------------------------------------------------------------
    # Snapshots

    def snapshot(self) -> dict:
        with self._lock:
            snapshot_stats = _SnapshotStats(self.stats())
            if self._signed_table is not None:
                snapshot_stats._signed_table = copy.deepcopy(
                    self._signed_table
                )
                snapshot_stats._table_digest = self._table_digest
            snapshot_stats._seen = copy.deepcopy(self._seen)
            return {
                "keys": copy.deepcopy(self._keys),
                "table": copy.deepcopy(self._table),
                "entries": copy.deepcopy(self._entries),
                "quarantined": list(self._quarantine),
                "stats": snapshot_stats,
            }

    def _snapshot_error(self, detail: object = None) -> CvssScoreError:
        return CvssScoreError("CVSS_SNAPSHOT_INVALID", detail)

    def _parse_snapshot(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise self._snapshot_error()
        required = ("keys", "table", "entries", "quarantined", "stats")
        if any(name not in state for name in required):
            raise self._snapshot_error()

        keys = state["keys"]
        if not isinstance(keys, dict):
            raise self._snapshot_error()
        parsed_keys: dict[str, dict] = {}
        for key_id, info in keys.items():
            if (
                not isinstance(key_id, str)
                or not key_id
                or not isinstance(info, dict)
                or not isinstance(info.get("secret"), str)
                or len(info["secret"]) < 16
                or not isinstance(info.get("break_glass"), bool)
                or not isinstance(info.get("revoked"), bool)
            ):
                raise self._snapshot_error()
            parsed_keys[key_id] = {
                "secret": info["secret"],
                "break_glass": info["break_glass"],
                "revoked": info["revoked"],
            }

        table = state["table"]
        if table is not None:
            try:
                self._validate_table(table)
            except CvssScoreError:
                raise self._snapshot_error()
            table = copy.deepcopy(table)

        entries = state["entries"]
        if not isinstance(entries, dict):
            raise self._snapshot_error()
        parsed_entries: dict[str, list[dict]] = {}
        for cve, history in entries.items():
            if (
                not isinstance(cve, str)
                or _CVE_RE.fullmatch(cve) is None
                or not isinstance(history, list)
            ):
                raise self._snapshot_error()
            parsed_history: list[dict] = []
            previous_revision = 0
            for record in history:
                if not isinstance(record, dict):
                    raise self._snapshot_error()
                required_record = (
                    "cve", "vector", "score", "severity", "revised",
                    "key_id",
                )
                if any(name not in record for name in required_record):
                    raise self._snapshot_error()
                if (
                    record["cve"] != cve
                    or not _is_positive_int(record["revised"])
                    or record["revised"] <= previous_revision
                    or not _is_number(record["score"])
                    or not isinstance(record["key_id"], str)
                    or not isinstance(record["vector"], str)
                    or not isinstance(record["severity"], str)
                ):
                    raise self._snapshot_error()
                previous_revision = record["revised"]
                record_copy = copy.deepcopy(record)
                record_copy.setdefault("signature", None)
                if (
                    record_copy["signature"] is not None
                    and not isinstance(record_copy["signature"], str)
                ):
                    raise self._snapshot_error()
                parsed_history.append(record_copy)
            parsed_entries[cve] = parsed_history

        quarantined = state["quarantined"]
        if (
            not isinstance(quarantined, list)
            or any(not isinstance(cve, str) for cve in quarantined)
        ):
            raise self._snapshot_error()

        stats = state["stats"]
        if not isinstance(stats, dict) or any(
            name not in stats for name in _COUNTER_KEYS
        ):
            raise self._snapshot_error()
        counts: dict[str, int] = {}
        for name in _COUNTER_KEYS[:-1]:
            value = stats[name]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise self._snapshot_error()
            counts[name] = value
        if (
            not isinstance(stats["entries"], int)
            or isinstance(stats["entries"], bool)
            or stats["entries"] < 0
            or stats["entries"] != len(parsed_entries)
        ):
            raise self._snapshot_error()

        signed_table = getattr(stats, "_signed_table", table)
        if signed_table is not None:
            try:
                self._validate_table(signed_table)
            except CvssScoreError:
                raise self._snapshot_error()
            signed_table = copy.deepcopy(signed_table)
        if (table is None) != (signed_table is None):
            raise self._snapshot_error()

        table_digest = getattr(stats, "_table_digest", None)
        if signed_table is None:
            table_digest = None
        else:
            expected_digest = _digest(signed_table)
            if table_digest is None:
                table_digest = expected_digest
            elif table_digest != expected_digest:
                raise self._snapshot_error()

        raw_seen = getattr(stats, "_seen", None)
        if raw_seen is None:
            seen: dict[tuple[str, int], dict] = {}
            for cve, history in parsed_entries.items():
                for record in history:
                    seen[(cve, record["revised"])] = {
                        "signature": record.get("signature"),
                        "record": self._public_record(record),
                    }
        else:
            if not isinstance(raw_seen, dict):
                raise self._snapshot_error()
            seen = {}
            for identity, item in raw_seen.items():
                if (
                    not isinstance(identity, tuple)
                    or len(identity) != 2
                    or not isinstance(identity[0], str)
                    or not _is_positive_int(identity[1])
                    or not isinstance(item, dict)
                    or not isinstance(item.get("signature"), str)
                    or not isinstance(item.get("record"), dict)
                ):
                    raise self._snapshot_error()
                record = item["record"]
                if (
                    record.get("cve") != identity[0]
                    or record.get("revised") != identity[1]
                    or any(
                        name not in record
                        for name in (
                            "cve", "vector", "score", "severity", "revised",
                            "key_id",
                        )
                    )
                ):
                    raise self._snapshot_error()
                seen[identity] = copy.deepcopy(item)

        return {
            "keys": parsed_keys,
            "table": table,
            "signed_table": signed_table,
            "table_digest": table_digest,
            "entries": parsed_entries,
            "seen": seen,
            "quarantine": list(quarantined),
            "counts": counts,
        }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                parsed = self._parse_snapshot(state)
            except CvssScoreError as exc:
                if exc.code == "CVSS_SNAPSHOT_INVALID":
                    raise
                raise self._snapshot_error() from exc
            except Exception as exc:
                raise self._snapshot_error() from exc

            # Only replace state after every part has been validated.
            self._keys = parsed["keys"]
            self._table = parsed["table"]
            self._signed_table = parsed["signed_table"]
            self._table_digest = parsed["table_digest"]
            self._entries = parsed["entries"]
            self._seen = parsed["seen"]
            self._quarantine = parsed["quarantine"]
            self._counts = parsed["counts"]
