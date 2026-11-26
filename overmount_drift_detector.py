"""Mount-table drift detection by stable mount identity, parentage and baseline."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Mapping


class DriftError(ValueError):
    """Refused or inconsistent drift-detector operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


_REQUIRED_FIELDS = (
    "mount_id",
    "parent_id",
    "path",
    "device",
    "fs_type",
    "mounted_at",
    "namespace",
)
_BASELINE_FIELDS = (
    "mount_id",
    "parent_id",
    "device",
    "fs_type",
    "mounted_at",
)
_STATS_KEYS = ("scans", "alerts", "confirmed", "recovered", "rejected")
_DRIFT_STATES = ("new", "overmount", "vanished", "reused")


def _invalid_row(index: int, missing: object = None, reason: str = "invalid_row") -> DriftError:
    return DriftError("INVALID_ROW", {"index": index, "reason": reason, "missing": missing})


def _validate_rows(rows) -> list[dict]:
    """Return defensive copies of rows after whole-table consistency checks."""
    if isinstance(rows, (str, bytes)) or not hasattr(rows, "__iter__"):
        raise DriftError("INVALID_TABLE", {"reason": "rows_not_iterable"})

    normalized: list[dict] = []
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise DriftError("INVALID_TABLE", {"reason": "rows_not_iterable"}) from exc

    for index, raw_row in enumerate(iterator):
        if not isinstance(raw_row, Mapping):
            raise _invalid_row(index, reason="row_not_mapping")
        missing = [field for field in _REQUIRED_FIELDS if field not in raw_row]
        if missing:
            raise _invalid_row(index, missing=missing, reason="missing_fields")

        row = {field: raw_row[field] for field in _REQUIRED_FIELDS}
        mount_id = row["mount_id"]
        parent_id = row["parent_id"]
        try:
            hash(mount_id)
            hash(parent_id)
        except TypeError as exc:
            raise _invalid_row(index, reason="unhashable_identity") from exc
        if not isinstance(row["path"], str) or not row["path"]:
            raise _invalid_row(index, reason="invalid_path")
        if not isinstance(row["namespace"], str) or not row["namespace"]:
            raise _invalid_row(index, reason="invalid_namespace")
        normalized.append(row)

    by_id: dict = {}
    for row in normalized:
        mount_id = row["mount_id"]
        if mount_id in by_id:
            raise DriftError(
                "TABLE_INCONSISTENT",
                {"reason": "duplicate_mount_id", "mount_id": mount_id},
            )
        by_id[mount_id] = row

    paths: dict[tuple[str, str], int] = {}
    for index, row in enumerate(normalized):
        path_key = (row["namespace"], row["path"])
        if path_key in paths:
            raise DriftError(
                "TABLE_INCONSISTENT",
                {
                    "reason": "duplicate_namespace_path",
                    "namespace": row["namespace"],
                    "path": row["path"],
                    "indexes": [paths[path_key], index],
                },
            )
        paths[path_key] = index

    cycle_state: dict = {}
    for start_id in by_id:
        if cycle_state.get(start_id) == 2:
            continue
        current_id = start_id
        chain = []
        while current_id in by_id:
            state = cycle_state.get(current_id)
            if state == 1:
                cycle_start = chain.index(current_id) if current_id in chain else 0
                raise DriftError(
                    "TABLE_INCONSISTENT",
                    {
                        "reason": "parent_cycle",
                        "mount_id": current_id,
                        "chain": chain[cycle_start:] + [current_id],
                    },
                )
            if state == 2:
                break
            cycle_state[current_id] = 1
            chain.append(current_id)
            current_id = by_id[current_id]["parent_id"]
        for mount_id in chain:
            cycle_state[mount_id] = 2

    return normalized


def _string_key(namespace: str, path: str) -> str:
    return f"{namespace}:{path}"


def _split_string_key(key: str) -> tuple[str, str]:
    marker = ":\x2f"
    position = key.find(marker)
    if position <= 0:
        raise DriftError("STATE_INVALID", {"reason": "invalid_baseline_key", "key": key})
    namespace = key[:position]
    path = key[position + 1 :]
    if not namespace or not path.startswith("/"):
        raise DriftError("STATE_INVALID", {"reason": "invalid_baseline_key", "key": key})
    return namespace, path


def _build_tree(rows: list[dict]) -> dict:
    by_id = {row["mount_id"]: row for row in rows}
    children: dict = {mount_id: [] for mount_id in by_id}
    depths: dict = {}

    def has_real_parent(row: dict) -> bool:
        parent_id = row["parent_id"]
        if parent_id == 0 or parent_id is None:
            return False
        parent = by_id.get(parent_id)
        return parent is not None and parent["namespace"] == row["namespace"]

    for row in rows:
        parent_id = row["parent_id"]
        if has_real_parent(row):
            children[parent_id].append(row["mount_id"])

    for mount_id in by_id:
        if mount_id in depths:
            continue
        chain = []
        current_id = mount_id
        base_depth = 0
        while current_id in by_id:
            if current_id in depths:
                base_depth = depths[current_id]
                break
            row = by_id[current_id]
            if not has_real_parent(row):
                depths[current_id] = 0
                base_depth = 0
                break
            chain.append(current_id)
            current_id = row["parent_id"]
        for chained_id in reversed(chain):
            base_depth += 1
            depths[chained_id] = base_depth

    ordered_ids = sorted(
        by_id,
        key=lambda mount_id: depths[mount_id],
    )
    tree = {}
    for mount_id in ordered_ids:
        row = by_id[mount_id]
        tree[mount_id] = {
            **{field: row[field] for field in _REQUIRED_FIELDS},
            "depth": depths[mount_id],
            "children": list(children[mount_id]),
        }
    return tree


class OvermountDriftDetector:
    """Compare successive mount tables against an explicitly confirmed baseline."""

    def __init__(self, *, max_entries: int = 32, alert_window: float = 2.0,
                 clock=None) -> None:
        if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries < 0:
            raise DriftError("INVALID_CONFIG", {"field": "max_entries", "value": max_entries})
        if (
            not isinstance(alert_window, (int, float))
            or isinstance(alert_window, bool)
            or alert_window < 0
        ):
            raise DriftError("INVALID_CONFIG", {"field": "alert_window", "value": alert_window})
        if clock is not None and not callable(clock):
            raise DriftError("INVALID_CONFIG", {"field": "clock", "value": clock})
        self.max_entries = max_entries
        self.alert_window = alert_window
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._baseline: dict[tuple[str, str], dict] = {}
        self._baseline_version = 0
        self._current_rows: list[dict] = []
        self._previous_rows: list[dict] = []
        self._has_scan = False
        self._last_result: dict | None = None
        self._scan_id = 0
        self._alerts: list[dict] = []
        self._alert_times: dict[str, float] = {}
        self._recovered: set[str] = set()
        self._overmount_layers: dict[str, list[dict]] = {}
        self._transient: list[dict] = []
        self._stats = {key: 0 for key in _STATS_KEYS}
        self._events: list[dict] = []
        self._event_active: dict[str, dict] = {}
        self._change_counter = 0

    def _now(self) -> float:
        return float(self._clock())

    def _reject_operation(self, code: str, detail: object = None) -> None:
        self._stats["rejected"] += 1
        raise DriftError(code, detail)

    def _baseline_rows(self) -> list[dict]:
        rows = []
        for (namespace, path), record in self._baseline.items():
            rows.append(
                {
                    "mount_id": record["mount_id"],
                    "parent_id": record["parent_id"],
                    "path": path,
                    "device": record["device"],
                    "fs_type": record["fs_type"],
                    "mounted_at": record["mounted_at"],
                    "namespace": namespace,
                }
            )
        return rows

    def _parent_is_missing(self, row: dict, by_id: dict) -> object:
        parent_id = row["parent_id"]
        if parent_id == 0 or parent_id is None:
            return None
        parent = by_id.get(parent_id)
        if parent is None or parent["namespace"] != row["namespace"]:
            return parent_id
        return None

    def _classify_current(self, row: dict) -> dict:
        key = (row["namespace"], row["path"])
        baseline = self._baseline.get(key)
        entry = copy.deepcopy(row)
        if baseline is None:
            entry["state"] = "new"
            entry["overmount"] = False
            return entry

        if baseline["mount_id"] != row["mount_id"]:
            entry["state"] = "overmount"
            entry["overmount"] = True
            entry["covered_path"] = row["path"]
            entry["covered_mount_id"] = baseline["mount_id"]
        elif baseline["mounted_at"] != row["mounted_at"]:
            entry["state"] = "reused"
            entry["overmount"] = False
        else:
            entry["state"] = "unchanged"
            entry["overmount"] = False
        return entry

    def _diff_valid_rows(self, rows: list[dict]) -> dict:
        by_id = {row["mount_id"]: row for row in rows}
        diff = {state: [] for state in _DRIFT_STATES}
        current_keys = set()

        for row in rows:
            key = (row["namespace"], row["path"])
            current_keys.add(key)
            missing_parent = self._parent_is_missing(row, by_id)
            if missing_parent is not None:
                continue
            entry = self._classify_current(row)
            if entry["state"] in ("new", "overmount", "reused"):
                diff[entry["state"]].append(copy.deepcopy(entry))

        for key, baseline in self._baseline.items():
            if key in current_keys:
                continue
            namespace, path = key
            diff["vanished"].append(
                {
                    "path": path,
                    "namespace": namespace,
                    "state": "vanished",
                    "overmount": False,
                    "mount_id": baseline["mount_id"],
                    "parent_id": baseline["parent_id"],
                    "device": baseline["device"],
                    "fs_type": baseline["fs_type"],
                    "mounted_at": baseline["mounted_at"],
                    "covered_path": None,
                }
            )
        return diff

    def _merge_overmount_layers(self, diff: dict) -> dict:
        merged = []
        for item in diff["overmount"]:
            layer_key = _string_key(item["namespace"], item["path"])
            layers = self._overmount_layers.get(layer_key)
            if layers and layers[-1].get("mount_id") == item.get("mount_id"):
                merged.extend(copy.deepcopy(layers))
            else:
                merged.append(item)
        diff["overmount"] = merged
        return diff

    def _current_drift(self) -> dict:
        return self._merge_overmount_layers(self._diff_valid_rows(self._current_rows))

    def _update_overmount_layers(self, rows: list[dict]) -> None:
        current_diff = self._diff_valid_rows(rows)
        for item in current_diff["overmount"]:
            layer_key = _string_key(item["namespace"], item["path"])
            layers = self._overmount_layers.setdefault(layer_key, [])
            if not layers:
                layer = copy.deepcopy(item)
                layer["sequence"] = 1
                layers.append(layer)
            elif layers[-1].get("mount_id") != item.get("mount_id"):
                layer = copy.deepcopy(item)
                layer["sequence"] = len(layers) + 1
                layer["covered_mount_id"] = layers[-1].get("mount_id")
                layers.append(layer)

    def _update_transient(
        self, previous_rows: list[dict], current_rows: list[dict]
    ) -> None:
        if not previous_rows:
            return
        current_keys = {(row["namespace"], row["path"]) for row in current_rows}
        now = self._now()
        for previous in previous_rows:
            key = (previous["namespace"], previous["path"])
            if key in current_keys or key in self._baseline:
                continue
            item = copy.deepcopy(previous)
            item["state"] = "transient"
            item["seen_until"] = now
            self._transient.append(item)

    @staticmethod
    def _dedup_key(namespace: str, path: str, state: str) -> str:
        return "\x1f".join((namespace, path, state))

    def _fingerprint(self, item: Mapping) -> str:
        return "\x1e".join(
            (
                str(item.get("namespace", "")),
                str(item.get("path", "")),
                str(item.get("state", "")),
                str(item.get("mount_id", "")),
                str(item.get("covered_mount_id", item.get("baseline_mount_id", ""))),
                str(item.get("mounted_at", "")),
                str(item.get("sequence", "")),
            )
        )

    def _emit_drift_alerts(self, rows: list[dict], diff: Mapping, orphans: list[dict]) -> None:
        now = self._now()
        candidates = []
        by_id = {row["mount_id"]: row for row in rows}
        for row in rows:
            if self._parent_is_missing(row, by_id) is not None:
                continue
            entry = self._classify_current(row)
            if entry["state"] in ("new", "overmount", "reused"):
                candidates.append(entry)
        candidates.extend(diff["vanished"])
        candidates.extend(orphans)

        for item in candidates:
            state = item.get("state", "orphan")
            namespace = item.get("namespace", "host")
            path = item["path"]
            dedup_key = self._dedup_key(namespace, path, state)
            last_alert = self._alert_times.get(dedup_key)
            if last_alert is not None and now - last_alert < self.alert_window:
                for alert in reversed(self._alerts):
                    if (
                        alert.get("path") == path
                        and alert.get("namespace") == namespace
                        and alert.get("state") == state
                    ):
                        alert["count"] = int(alert.get("count", 1)) + 1
                        alert["last_at"] = now
                        break
                continue
            self._alert_times[dedup_key] = now
            alert = {
                "path": path,
                "namespace": namespace,
                "state": state,
                "count": 1,
                "mount_id": item.get("mount_id"),
                "covered_path": item.get("covered_path"),
                "at": now,
                "first_at": now,
                "last_at": now,
                "scan_id": self._scan_id,
            }
            self._alerts.append(alert)
            self._stats["alerts"] += 1

    def baseline(self, rows) -> dict:
        with self._lock:
            try:
                valid_rows = _validate_rows(rows)
            except DriftError:
                self._stats["rejected"] += 1
                raise

            self._baseline = {
                (row["namespace"], row["path"]): {
                    field: row[field] for field in _BASELINE_FIELDS
                }
                for row in valid_rows
            }
            self._baseline_version += 1
            self._previous_rows = []
            self._overmount_layers.clear()
            self._transient.clear()
            return {"count": len(self._baseline), "version": self._baseline_version}

    def mount_tree(self, rows=None) -> dict:
        with self._lock:
            if rows is None:
                if self._has_scan:
                    rows = copy.deepcopy(self._current_rows)
                elif self._baseline:
                    rows = self._baseline_rows()
                else:
                    return {}
            try:
                valid_rows = _validate_rows(rows)
            except DriftError:
                self._stats["rejected"] += 1
                raise
            return _build_tree(valid_rows)

    def scan(self, rows) -> dict:
        with self._lock:
            try:
                valid_rows = _validate_rows(rows)
            except DriftError:
                self._stats["rejected"] += 1
                raise

            by_id = {row["mount_id"]: row for row in valid_rows}
            entries = []
            orphans = []
            for row in valid_rows:
                missing_parent = self._parent_is_missing(row, by_id)
                if missing_parent is not None:
                    orphan = copy.deepcopy(row)
                    orphan["missing_parent"] = missing_parent
                    orphan["state"] = "orphan"
                    orphan["orphan"] = True
                    orphans.append(orphan)
                else:
                    entries.append(self._classify_current(row))

            total_entries = len(entries)
            truncated = max(0, total_entries - self.max_entries)
            public_result = {
                "entries": copy.deepcopy(entries[: self.max_entries]),
                "orphans": copy.deepcopy(orphans),
                "truncated": truncated,
            }

            previous_rows = copy.deepcopy(self._current_rows)
            self._update_overmount_layers(valid_rows)
            self._update_transient(previous_rows, valid_rows)
            self._previous_rows = previous_rows
            self._current_rows = copy.deepcopy(valid_rows)
            self._has_scan = True
            self._recovered.clear()
            self._last_result = copy.deepcopy(public_result)
            self._scan_id += 1
            self._stats["scans"] += 1
            diff = self._diff_valid_rows(valid_rows)
            self._emit_drift_alerts(valid_rows, diff, orphans)
            return copy.deepcopy(public_result)

    def diff(self, current=None) -> dict:
        with self._lock:
            try:
                if current is None:
                    if not self._has_scan:
                        return {state: [] for state in _DRIFT_STATES}
                    valid_rows = _validate_rows(copy.deepcopy(self._current_rows))
                    diff = self._diff_valid_rows(valid_rows)
                    return self._merge_overmount_layers(diff)
                else:
                    rows = self._coerce_current_rows(current)
                    valid_rows = _validate_rows(rows)
                    return self._diff_valid_rows(valid_rows)
            except DriftError:
                if current is not None:
                    self._stats["rejected"] += 1
                raise

    def _coerce_current_rows(self, current) -> list:
        if isinstance(current, list):
            return current
        if isinstance(current, tuple):
            return list(current)
        if isinstance(current, Mapping):
            if all(field in current for field in _REQUIRED_FIELDS):
                return [current]
            if "entries" in current:
                rows = list(current.get("entries") or [])
                rows.extend(current.get("orphans") or [])
                return rows
            values = list(current.values())
            if all(isinstance(value, Mapping) and "mount_id" in value for value in values):
                return values
            if "rows" in current:
                return current["rows"]
        raise DriftError("INVALID_CURRENT", {"reason": "unsupported_current"})

    def confirm(self) -> int:
        with self._lock:
            if not self._has_scan:
                self._reject_operation("NO_SCAN", {"reason": "no_recent_scan"})
            self._baseline = {
                (row["namespace"], row["path"]): {
                    field: row[field] for field in _BASELINE_FIELDS
                }
                for row in self._current_rows
            }
            self._baseline_version += 1
            self._stats["confirmed"] += 1
            self._recovered.clear()
            self._overmount_layers.clear()
            self._transient.clear()
            self._previous_rows = copy.deepcopy(self._current_rows)
            return len(self._baseline)

    def recover(self, target: str) -> dict:
        with self._lock:
            if not self._has_scan:
                self._reject_operation("NOT_DRIFTED", {"target": target})
            diff = self._current_drift()
            by_target: dict[str, list[Mapping]] = {}
            for state in _DRIFT_STATES:
                for item in diff[state]:
                    fingerprints = self._fingerprint(item)
                    if fingerprints in self._recovered:
                        continue
                    by_target.setdefault(item["path"], []).append(item)
                    by_target.setdefault(
                        _string_key(item["namespace"], item["path"]), []
                    ).append(item)
                    if item.get("mount_id") is not None:
                        by_target.setdefault(str(item["mount_id"]), []).append(item)

            matches = by_target.get(target, [])
            if not matches:
                self._reject_operation("NOT_DRIFTED", {"target": target})

            for item in matches:
                self._recovered.add(self._fingerprint(item))
            self._stats["recovered"] += 1
            return {"recovered": True, "state": matches[0]["state"]}

    def alerts(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._alerts)

    def clear_alerts(self) -> int:
        with self._lock:
            count = len(self._alerts)
            self._alerts.clear()
            return count

    def record_event(self, event=None, **fields) -> dict:
        """Record an optional kernel mount event between table scans.

        The periodic mount table cannot prove a mount that appeared and
        disappeared between two reads.  Event/change-counter feeds close that
        gap without changing scan's baseline-read-only contract.
        """
        with self._lock:
            if event is None:
                data = {}
            elif isinstance(event, Mapping):
                data = dict(event)
            elif isinstance(event, str):
                data = {"type": event}
            else:
                self._reject_operation("INVALID_EVENT", {"reason": "event_not_mapping"})

            data.update(fields)
            kind = str(
                data.get("type")
                or data.get("kind")
                or data.get("event")
                or data.get("op")
                or "change"
            ).lower()
            aliases = {
                "add": "mount",
                "added": "mount",
                "mount": "mount",
                "bind": "mount",
                "overmount": "overmount",
                "cover": "overmount",
                "covered": "overmount",
                "remove": "unmount",
                "removed": "unmount",
                "umount": "unmount",
                "unmount": "unmount",
                "delete": "unmount",
                "deleted": "unmount",
            }
            event_type = aliases.get(kind, "change")
            namespace = data.get("namespace", "host")
            path = data.get("path") or data.get("mount_point") or data.get("target")
            mount_id = data.get("mount_id")
            if mount_id is None:
                mount_id = data.get("id")
            at = data.get("at", data.get("timestamp", data.get("time", self._now())))
            try:
                at = float(at)
            except (TypeError, ValueError):
                at = self._now()

            normalized = {
                "seq": len(self._events) + 1,
                "type": event_type,
                "path": path,
                "namespace": namespace,
                "mount_id": mount_id,
                "parent_id": data.get("parent_id"),
                "covered_path": data.get("covered_path", path),
                "covered_mount_id": data.get("covered_mount_id"),
                "at": at,
            }
            for key, value in data.items():
                normalized.setdefault(key, value)

            self._events.append(copy.deepcopy(normalized))
            increment = data.get("change_count", data.get("count", 1))
            try:
                increment = int(increment)
            except (TypeError, ValueError):
                increment = 1
            self._change_counter += max(1, increment)

            identity = self._event_identity(namespace, mount_id, path)
            if event_type in ("mount", "overmount"):
                self._event_active[identity] = copy.deepcopy(normalized)
            elif event_type == "unmount":
                started = self._event_active.pop(identity, None)
                if started is None and path is not None:
                    for active_key, active in list(self._event_active.items()):
                        if active.get("path") == path and active.get("namespace") == namespace:
                            started = self._event_active.pop(active_key)
                            break
                transient = copy.deepcopy(normalized)
                transient["state"] = "transient"
                transient["seen_at"] = (started or {}).get("at")
                self._transient.append(transient)

            if event_type == "overmount" and path is not None:
                self._record_event_layer(normalized)

            return copy.deepcopy(normalized)

    def record_kernel_event(self, event=None, **fields) -> dict:
        return self.record_event(event, **fields)

    def record_change(self, count: int = 1) -> dict:
        with self._lock:
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                self._reject_operation("INVALID_COUNT", {"count": count})
            self._change_counter += count
            return {"change_count": self._change_counter}

    def events(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._events)

    def change_count(self) -> int:
        with self._lock:
            return self._change_counter

    def transient(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._transient)

    def overmount_history(self, target: str | None = None) -> dict | list[dict]:
        with self._lock:
            layers = copy.deepcopy(self._overmount_layers)
            if target is None:
                return layers
            if target in layers:
                return layers[target]
            for key, value in layers.items():
                if key.endswith(":" + target) or any(
                    layer.get("path") == target for layer in value
                ):
                    return value
            return []

    @staticmethod
    def _event_identity(namespace: object, mount_id: object, path: object) -> str:
        if mount_id is not None:
            return f"id:{namespace}:{mount_id}"
        return f"path:{namespace}:{path}"

    def _record_event_layer(self, event: Mapping) -> None:
        namespace = event.get("namespace", "host")
        path = event["path"]
        layer_key = _string_key(namespace, path)
        layers = self._overmount_layers.setdefault(layer_key, [])
        mount_id = event.get("mount_id")
        if mount_id is not None and any(layer.get("mount_id") == mount_id for layer in layers):
            return
        covered_mount_id = event.get("covered_mount_id")
        if covered_mount_id is None and layers:
            covered_mount_id = layers[-1].get("mount_id")
        if covered_mount_id is None:
            baseline = self._baseline.get((namespace, path))
            covered_mount_id = baseline.get("mount_id") if baseline else None
        layer = dict(event)
        layer.update(
            {
                "namespace": namespace,
                "path": path,
                "state": "overmount",
                "overmount": True,
                "covered_path": path,
                "covered_mount_id": covered_mount_id,
                "sequence": len(layers) + 1,
            }
        )
        layers.append(layer)

    def snapshot(self) -> dict:
        with self._lock:
            state = {
                "schema": 1,
                "max_entries": self.max_entries,
                "alert_window": self.alert_window,
                "baseline_version": self._baseline_version,
                "baseline": {
                    _string_key(namespace, path): copy.deepcopy(record)
                    for (namespace, path), record in self._baseline.items()
                },
                "alerts": copy.deepcopy(self._alerts),
                "alert_times": copy.deepcopy(self._alert_times),
                "stats": copy.deepcopy(self._stats),
                "current_rows": copy.deepcopy(self._current_rows),
                "has_scan": self._has_scan,
                "last_result": copy.deepcopy(self._last_result),
                "scan_id": self._scan_id,
                "recovered": sorted(self._recovered),
                "overmount_layers": copy.deepcopy(self._overmount_layers),
                "transient": copy.deepcopy(self._transient),
                "events": copy.deepcopy(self._events),
                "event_active": copy.deepcopy(self._event_active),
                "change_counter": self._change_counter,
            }
            return copy.deepcopy(state)

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                if not isinstance(state, Mapping):
                    raise DriftError("STATE_INVALID", {"reason": "state_not_mapping"})
                candidate = copy.deepcopy(dict(state))
                for key in ("baseline", "alerts", "stats"):
                    if key not in candidate:
                        raise DriftError("STATE_INVALID", {"reason": "missing_key", "key": key})

                restored_max_entries = self.max_entries
                if "max_entries" in candidate:
                    value = candidate["max_entries"]
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise DriftError("STATE_INVALID", {"reason": "invalid_max_entries"})
                    restored_max_entries = value

                restored_alert_window = self.alert_window
                if "alert_window" in candidate:
                    value = candidate["alert_window"]
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or value < 0
                    ):
                        raise DriftError("STATE_INVALID", {"reason": "invalid_alert_window"})
                    restored_alert_window = float(value)

                baseline_in = candidate["baseline"]
                if not isinstance(baseline_in, Mapping):
                    raise DriftError("STATE_INVALID", {"reason": "baseline_not_mapping"})

                baseline_rows = []
                for key, record in baseline_in.items():
                    if not isinstance(key, str) or not isinstance(record, Mapping):
                        raise DriftError("STATE_INVALID", {"reason": "invalid_baseline_entry"})
                    missing_baseline_fields = [
                        field for field in _BASELINE_FIELDS if field not in record
                    ]
                    if missing_baseline_fields:
                        raise DriftError(
                            "STATE_INVALID",
                            {
                                "reason": "missing_baseline_fields",
                                "fields": missing_baseline_fields,
                            },
                        )
                    namespace, path = _split_string_key(key)
                    row = {field: record.get(field) for field in _BASELINE_FIELDS}
                    row["namespace"] = record.get("namespace", namespace)
                    row["path"] = record.get("path", path)
                    if row["namespace"] != namespace or row["path"] != path:
                        raise DriftError("STATE_INVALID", {"reason": "baseline_key_mismatch"})
                    baseline_rows.append(row)
                validated_baseline_rows = _validate_rows(baseline_rows)

                alerts_in = candidate["alerts"]
                if not isinstance(alerts_in, list):
                    raise DriftError("STATE_INVALID", {"reason": "alerts_not_list"})
                alerts = []
                for alert in alerts_in:
                    if not isinstance(alert, Mapping) or "path" not in alert or "state" not in alert:
                        raise DriftError("STATE_INVALID", {"reason": "invalid_alert"})
                    alerts.append(dict(alert))

                stats_in = candidate["stats"]
                if not isinstance(stats_in, Mapping):
                    raise DriftError("STATE_INVALID", {"reason": "stats_not_mapping"})
                stats = {}
                for key in _STATS_KEYS:
                    value = stats_in.get(key)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise DriftError("STATE_INVALID", {"reason": "invalid_stat", "key": key})
                    stats[key] = value

                alert_times = {}
                if "alert_times" in candidate and candidate["alert_times"] is not None:
                    raw_times = candidate["alert_times"]
                    if not isinstance(raw_times, Mapping):
                        raise DriftError("STATE_INVALID", {"reason": "alert_times_not_mapping"})
                    for key, value in raw_times.items():
                        if not isinstance(key, str) or not isinstance(value, (int, float)):
                            raise DriftError("STATE_INVALID", {"reason": "invalid_alert_time"})
                        alert_times[key] = float(value)
                else:
                    for alert in alerts:
                        at = alert.get("at")
                        if isinstance(at, (int, float)) and not isinstance(at, bool):
                            alert_times[
                                self._dedup_key(
                                    alert.get("namespace", "host"), alert["path"], alert["state"]
                                )
                            ] = float(at)

                current_rows = []
                if candidate.get("current_rows") is not None:
                    if not isinstance(candidate["current_rows"], list):
                        raise DriftError("STATE_INVALID", {"reason": "current_rows_not_list"})
                    current_rows = _validate_rows(candidate["current_rows"])

                recovered = set()
                if "recovered" in candidate and candidate["recovered"] is not None:
                    raw_recovered = candidate["recovered"]
                    if not isinstance(raw_recovered, list) or not all(
                        isinstance(item, str) for item in raw_recovered
                    ):
                        raise DriftError("STATE_INVALID", {"reason": "recovered_not_list"})
                    recovered = set(raw_recovered)

                raw_layers = candidate.get("overmount_layers", {})
                if not isinstance(raw_layers, Mapping):
                    raise DriftError("STATE_INVALID", {"reason": "layers_not_mapping"})
                overmount_layers = {}
                for layer_key, layers in raw_layers.items():
                    if not isinstance(layer_key, str) or not isinstance(layers, list):
                        raise DriftError("STATE_INVALID", {"reason": "invalid_layers"})
                    if not all(isinstance(layer, Mapping) for layer in layers):
                        raise DriftError("STATE_INVALID", {"reason": "invalid_layer"})
                    overmount_layers[layer_key] = [dict(layer) for layer in layers]

                raw_transient = candidate.get("transient", [])
                if not isinstance(raw_transient, list):
                    raise DriftError("STATE_INVALID", {"reason": "transient_not_list"})
                if not all(isinstance(item, Mapping) for item in raw_transient):
                    raise DriftError("STATE_INVALID", {"reason": "invalid_transient"})
                transient = [dict(item) for item in raw_transient]

                events = []
                if "events" in candidate and candidate["events"] is not None:
                    if not isinstance(candidate["events"], list):
                        raise DriftError("STATE_INVALID", {"reason": "events_not_list"})
                    events = copy.deepcopy(candidate["events"])

                event_active = {}
                raw_event_active = candidate.get("event_active", {})
                if not isinstance(raw_event_active, Mapping):
                    raise DriftError("STATE_INVALID", {"reason": "event_active_not_mapping"})
                for identity, event in raw_event_active.items():
                    if not isinstance(identity, str) or not isinstance(event, Mapping):
                        raise DriftError("STATE_INVALID", {"reason": "invalid_active_event"})
                    event_active[identity] = dict(event)

                scan_id = candidate.get("scan_id", stats["scans"])
                baseline_version = candidate.get("baseline_version", 1 if baseline_rows else 0)
                change_counter = candidate.get("change_counter", 0)
                if isinstance(scan_id, bool) or not isinstance(scan_id, int) or scan_id < 0:
                    raise DriftError("STATE_INVALID", {"reason": "invalid_scan_id"})
                if (
                    isinstance(baseline_version, bool)
                    or not isinstance(baseline_version, int)
                    or baseline_version < 0
                ):
                    raise DriftError("STATE_INVALID", {"reason": "invalid_baseline_version"})
                if (
                    isinstance(change_counter, bool)
                    or not isinstance(change_counter, int)
                    or change_counter < 0
                ):
                    raise DriftError("STATE_INVALID", {"reason": "invalid_change_counter"})

                has_scan = candidate.get("has_scan", candidate.get("scan_id", stats["scans"]) > 0)
                if not isinstance(has_scan, bool):
                    raise DriftError("STATE_INVALID", {"reason": "invalid_has_scan"})
            except DriftError:
                raise
            except Exception as exc:
                raise DriftError("STATE_INVALID", {"reason": "unparseable_state"}) from exc

            self.max_entries = restored_max_entries
            self.alert_window = restored_alert_window
            self._baseline = {
                (row["namespace"], row["path"]): {
                    field: row[field] for field in _BASELINE_FIELDS
                }
                for row in validated_baseline_rows
            }
            self._baseline_version = baseline_version
            self._alerts = copy.deepcopy(alerts)
            self._alert_times = alert_times
            self._stats = stats
            self._current_rows = copy.deepcopy(current_rows)
            self._has_scan = has_scan
            self._last_result = copy.deepcopy(candidate.get("last_result"))
            self._scan_id = scan_id
            self._recovered = recovered
            self._overmount_layers = overmount_layers
            self._transient = transient
            self._previous_rows = copy.deepcopy(current_rows)
            self._events = events
            self._event_active = event_active
            self._change_counter = change_counter

    def stats(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)
