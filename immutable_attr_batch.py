"""Batch set/clear of the immutable attribute over protected paths."""

from __future__ import annotations

import posixpath
import threading
from dataclasses import dataclass


class ImmutableError(ValueError):
    """Refusal raised by the batch attribute tool."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PathNode:
    path: str
    kind: str
    inode: int
    parent: object
    immutable: bool
    fs_immutable: bool
    mount: bool
    digest: str


@dataclass(frozen=True)
class CredentialReport:
    ok: bool
    missing: tuple
    action_ok: bool
    digest_ok: bool


@dataclass(frozen=True)
class BatchPlan:
    action: str
    ordered: tuple
    skipped: tuple


@dataclass(frozen=True)
class BatchResult:
    action: str
    applied: tuple
    skipped: tuple
    rolled_back: int


class ImmutableAttrBatch:
    """Plan, apply and recover immutable-attribute batches."""

    def __init__(self, *, allowed_roots, max_paths: int = 64,
                 max_depth: int = 8, clock=None) -> None:
        if isinstance(allowed_roots, str):
            allowed_roots = (allowed_roots,)
        self.allowed_roots = tuple(self._normalize_path(root)
                                   for root in allowed_roots)
        self.max_paths = max_paths
        self.max_depth = max_depth
        self._clock = clock
        self._lock = threading.RLock()
        self._nodes: dict = {}
        self._inode_state: dict = {}
        self._journal: list = []
        self._faults: dict = {}
        self._crash_after = None
        self._seq = 0
        self._batch_seq = 0
        self._inode_seq = 0
        self._batches: dict = {}
        self._last_batch_id = None
        self._counters = {"applied": 0, "skipped": 0, "unset": 0,
                          "rolled_back": 0, "conflicts": 0, "recovered": 0}

    @staticmethod
    def _normalize_path(path) -> str:
        return posixpath.normpath(str(path))

    @staticmethod
    def _parts(path: str) -> tuple:
        return tuple(part for part in path.split("/") if part not in ("", "."))

    def _root_info_locked(self, path: str):
        path_parts = self._parts(path)
        best = None
        for root in self.allowed_roots:
            root_parts = self._parts(root)
            if (len(root_parts) <= len(path_parts)
                    and path_parts[:len(root_parts)] == root_parts):
                if best is None or len(root_parts) > len(best[1]):
                    best = (root, root_parts)
        return best

    def _is_allowed_locked(self, path: str) -> bool:
        return self._root_info_locked(path) is not None

    def _depth_locked(self, path: str) -> int:
        root_info = self._root_info_locked(path)
        if root_info is None:
            return len(self._parts(path))
        return len(self._parts(path)) - len(root_info[1])

    def _new_inode_locked(self) -> int:
        while True:
            self._inode_seq += 1
            if self._inode_seq not in self._inode_state:
                return self._inode_seq

    def _get_node_locked(self, path):
        normalized = self._normalize_path(path)
        node = self._nodes.get(normalized)
        if node is None:
            raise ImmutableError("PATH_UNKNOWN", normalized)
        return node

    def _attributes_dict_locked(self, node: dict) -> dict:
        inode = node["inode"]
        inode_state = self._inode_state.setdefault(
            inode, {"immutable": bool(node["immutable"])})
        return {
            "path": node["path"],
            "kind": node["kind"],
            "inode": inode,
            "parent": node["parent"],
            "immutable": bool(inode_state["immutable"]),
            "fs_immutable": node["fs_immutable"],
            "mount": node["mount"],
            "digest": node["digest"],
        }

    def add_path(self, path, *, kind="file", inode=None, mount=False,
                 fs_immutable=True, digest="", immutable=False):
        with self._lock:
            normalized = self._normalize_path(path)
            parent = posixpath.dirname(normalized) or "/"
            existing = self._nodes.get(normalized)

            if inode is None:
                inode = existing["inode"] if existing is not None \
                    else self._new_inode_locked()
            else:
                inode = int(inode)

            if inode not in self._inode_state:
                self._inode_state[inode] = {"immutable": bool(immutable)}
            elif immutable:
                self._inode_state[inode]["immutable"] = True

            node = {
                "path": normalized,
                "kind": kind,
                "inode": inode,
                "parent": parent,
                "immutable": bool(immutable),
                "fs_immutable": bool(fs_immutable),
                "mount": bool(mount),
                "digest": digest,
            }
            self._nodes[normalized] = node
            return PathNode(**self._attributes_dict_locked(node))

    def attributes(self, path):
        with self._lock:
            node = self._get_node_locked(path)
            return self._attributes_dict_locked(node)

    def credential_check(self, paths, credential, *, action, now=0):
        with self._lock:
            normalized_paths = tuple(self._normalize_path(path)
                                     for path in paths)
            cred_paths = (credential or {}).get("paths", {}) or {}
            missing = tuple(sorted({
                path for path in normalized_paths if path not in cred_paths
            }))

            actions = (credential or {}).get("actions", ())
            if isinstance(actions, str):
                action_set = {actions}
            elif actions is None:
                action_set = set()
            else:
                action_set = set(actions)
            action_ok = (action in action_set or "*" in action_set
                         or "all" in action_set)

            digest_ok = True
            for path in normalized_paths:
                if path not in cred_paths:
                    continue
                actual = self._nodes.get(path, {}).get("digest", "")
                expected = cred_paths[path]
                if expected is not None and actual != expected:
                    digest_ok = False
                    break

            expired = self._credential_expired_locked(credential, now)
            ok = not missing and action_ok and digest_ok and not expired
            return CredentialReport(ok, missing, action_ok, digest_ok)

    @staticmethod
    def _credential_expired_locked(credential, now) -> bool:
        if credential is not None and credential.get("expired") is True:
            return True
        if not credential:
            return False

        expires_at = None
        for key in ("expires_at", "expire_at", "exp", "expiry", "expires",
                    "valid_until", "not_after", "deadline"):
            value = credential.get(key)
            if value is not None:
                expires_at = value
                break

        if expires_at is None:
            issued_at = credential.get(
                "issued_at", credential.get("iat", credential.get("not_before")))
            ttl = credential.get(
                "ttl", credential.get("expires_in", credential.get("lifetime")))
            if issued_at is not None and ttl is not None:
                try:
                    expires_at = issued_at + ttl
                except TypeError:
                    expires_at = None

        if expires_at is None:
            return False
        try:
            return now >= expires_at
        except TypeError:
            return False

    def _digest_mismatches_locked(self, paths, credential) -> tuple:
        cred_paths = (credential or {}).get("paths", {}) or {}
        mismatches = set()
        for path in paths:
            if path not in cred_paths or path not in self._nodes:
                continue
            expected = cred_paths[path]
            actual = self._nodes[path]["digest"]
            if expected is not None and actual != expected:
                mismatches.add(path)
        return tuple(sorted(mismatches))

    def _validate_credential_locked(self, paths, credential, *, action, now):
        if credential is None:
            raise ImmutableError("CREDENTIAL_REQUIRED", action)

        report = self.credential_check(
            paths, credential, action=action, now=now)
        if report.missing:
            raise ImmutableError("CREDENTIAL_INCOMPLETE", report.missing)
        if not report.action_ok:
            raise ImmutableError("CREDENTIAL_ACTION_DENIED", action)
        if self._credential_expired_locked(credential, now):
            raise ImmutableError(
                "CREDENTIAL_EXPIRED", credential.get("id") if credential else None)
        if action == "set" and not report.digest_ok:
            raise ImmutableError(
                "DIGEST_MISMATCH",
                self._digest_mismatches_locked(paths, credential))

    def _build_plan_locked(self, paths, *, action, credential,
                           strict_order, now):
        if action not in ("set", "unset"):
            raise ImmutableError("INVALID_ACTION", action)

        raw_paths = list(paths)
        if len(raw_paths) > self.max_paths:
            raise ImmutableError("PATH_LIMIT", len(raw_paths))

        planned_paths = []
        for raw_path in raw_paths:
            path = self._normalize_path(raw_path)
            if not self._is_allowed_locked(path):
                raise ImmutableError("PATH_NOT_ALLOWED", raw_path)

            node = self._nodes.get(path)
            if node is None:
                raise ImmutableError("PATH_UNKNOWN", path)

            if node["kind"] in ("symlink", "link"):
                raise ImmutableError("SYMLINK_REFUSED", path)

            depth = self._depth_locked(path)
            if depth > self.max_depth:
                raise ImmutableError("DEPTH_LIMIT", path)

            if node["mount"] and not node["fs_immutable"]:
                raise ImmutableError("FS_UNSUPPORTED", path)

            planned_paths.append(path)

        reverse_depth = action == "unset"
        ordered_paths = tuple(sorted(
            planned_paths,
            key=lambda path: (-self._depth_locked(path)
                              if reverse_depth else self._depth_locked(path),
                              path)))

        if strict_order and tuple(planned_paths) != ordered_paths:
            raise ImmutableError("ORDER_VIOLATION",
                                 (tuple(planned_paths), ordered_paths))

        if action == "set":
            batch_path_set = set(planned_paths)
            shadowed = set()
            for path in planned_paths:
                node = self._nodes[path]
                if node["kind"] not in ("dir", "directory"):
                    continue
                parent_parts = self._parts(path)
                for child_path in self._nodes:
                    child_parts = self._parts(child_path)
                    is_child = (
                        len(child_parts) > len(parent_parts)
                        and child_parts[:len(parent_parts)] == parent_parts
                    )
                    if is_child and child_path not in batch_path_set:
                        shadowed.add(child_path)
            if shadowed:
                raise ImmutableError(
                    "SHADOWED_CHILDREN", tuple(sorted(shadowed)))

        if credential is not None:
            self._validate_credential_locked(
                ordered_paths, credential, action=action, now=now)

        target_immutable = action == "set"
        skipped = []
        seen_inodes = set()
        items = []
        for path in ordered_paths:
            node = self._nodes[path]
            inode = node["inode"]
            current = self._attributes_dict_locked(node)["immutable"]
            duplicate_object = inode in seen_inodes
            is_skipped = duplicate_object or current == target_immutable
            if is_skipped:
                skipped.append(path)
            seen_inodes.add(inode)
            items.append({
                "path": path,
                "inode": inode,
                "skip": is_skipped,
                "duplicate": duplicate_object,
                "original": current,
                "changed": False,
                "mutated": False,
                "entry": None,
            })

        return {
            "action": action,
            "ordered": ordered_paths,
            "skipped": tuple(skipped),
            "items": items,
        }

    def plan(self, paths, *, action, credential=None, strict_order=False, now=0):
        with self._lock:
            planned = self._build_plan_locked(
                paths, action=action, credential=credential,
                strict_order=strict_order, now=now)
            return BatchPlan(planned["action"], planned["ordered"],
                             planned["skipped"])

    def set(self, paths, *, credential, strict_order=False, now=0):
        return self._run_batch("set", paths, credential=credential,
                               strict_order=strict_order, now=now)

    def unset(self, paths, *, credential, strict_order=False, now=0):
        return self._run_batch("unset", paths, credential=credential,
                               strict_order=strict_order, now=now)

    def verify(self, path):
        with self._lock:
            node = self._get_node_locked(path)
            actual = self._readback_value_locked(
                node, self._attributes_dict_locked(node)["immutable"],
                consume=False)
            current = self._attributes_dict_locked(node)["immutable"]
            return {
                "path": node["path"],
                "inode": node["inode"],
                "immutable": current,
                "readback": actual,
            }

    def journal_entries(self):
        with self._lock:
            return [dict(entry) for entry in self._journal]

    def recover(self, *, now=0):
        with self._lock:
            batch = self._batches.get(self._last_batch_id)
            if batch is None or batch["status"] not in ("active", "crashed"):
                return {"unfinished": 0, "rolled_back": 0, "conflicts": 0}

            unfinished = len(batch["entries"])
            rolled_back = 0
            conflicts = 0

            for item in reversed(batch["items"]):
                entry = item["entry"]
                if entry is None or entry["state"] != "done" \
                        or not item["changed"]:
                    continue

                node = self._nodes.get(item["path"])
                if node is None or node["inode"] != entry["inode"]:
                    conflicts += 1
                    self._counters["conflicts"] += 1
                    if entry is not None:
                        entry["state"] = "failed"
                    continue

                self._restore_item_locked(item)
                entry["state"] = "rolled_back"
                rolled_back += 1

            for entry in batch["entries"]:
                if entry["state"] == "pending":
                    entry["state"] = "cancelled"

            batch["status"] = "recovered"
            self._counters["recovered"] += rolled_back
            return {
                "unfinished": unfinished,
                "rolled_back": rolled_back,
                "conflicts": conflicts,
            }

    def inject_fault(self, path, *, code="READBACK_MISMATCH", when="apply"):
        with self._lock:
            normalized = self._normalize_path(path)
            fault = {"code": code, "when": when, "remaining": 1}
            self._faults[normalized] = fault

    def crash_after(self, count):
        with self._lock:
            self._crash_after = int(count)

    def stats(self):
        with self._lock:
            return dict(self._counters)

    def _take_fault_locked(self, path: str, phase: str):
        fault = self._faults.get(path)
        if fault is None or fault.get("remaining", 0) <= 0:
            return None
        if fault.get("when") not in (phase, "any"):
            return None
        fault["remaining"] -= 1
        if fault["remaining"] <= 0:
            self._faults.pop(path, None)
        return fault

    def _readback_value_locked(self, node: dict, expected: bool,
                               *, consume: bool = True) -> bool:
        path = node["path"]
        fault = None
        if consume:
            fault = self._faults.get(path)
            if (fault is not None
                    and fault.get("code") == "READBACK_MISMATCH"
                    and fault.get("when") in ("apply", "readback", "any")):
                self._take_fault_locked(
                    path, fault.get("when", "apply"))
                return not expected
        inode_state = self._inode_state.setdefault(
            node["inode"], {"immutable": bool(node["immutable"])})
        return bool(inode_state["immutable"])

    def _apply_change_locked(self, item: dict, action: str) -> None:
        path = item["path"]
        node = self._get_node_locked(path)
        planned_inode = item["inode"]

        fault = self._faults.get(path)
        if fault is not None and fault.get("code") == "READBACK_MISMATCH" \
                and fault.get("when") in ("apply", "readback"):
            pass
        else:
            triggered = self._take_fault_locked(path, "apply")
            if triggered is not None:
                code = triggered["code"]
                if code in ("INODE_CHANGE", "CONFLICT"):
                    node["inode"] = self._new_inode_locked()
                    self._inode_state.setdefault(
                        node["inode"], {"immutable": False})
                    self._counters["conflicts"] += 1
                    raise ImmutableError("CONFLICT", path)
                raise ImmutableError(code, path)

        if node["inode"] != planned_inode:
            self._counters["conflicts"] += 1
            raise ImmutableError("CONFLICT", path)

        target = action == "set"
        inode = node["inode"]
        inode_state = self._inode_state.setdefault(
            inode, {"immutable": bool(node["immutable"])})
        item["original"] = bool(inode_state["immutable"])
        inode_state["immutable"] = target
        item["mutated"] = True

        readback = self._readback_value_locked(node, target)
        if readback != target:
            raise ImmutableError("READBACK_MISMATCH", path)

    def _restore_item_locked(self, item: dict) -> None:
        if not item.get("mutated"):
            return
        node = self._nodes.get(item["path"])
        if node is None:
            return
        inode_state = self._inode_state.setdefault(
            node["inode"], {"immutable": bool(node["immutable"])})
        inode_state["immutable"] = bool(item["original"])
        item["mutated"] = False

    def _run_batch(self, action, paths, *, credential, strict_order, now):
        with self._lock:
            planned = self._build_plan_locked(
                paths, action=action, credential=credential,
                strict_order=strict_order, now=now)

            self._batch_seq += 1
            batch_id = self._batch_seq
            crash_after = self._crash_after
            self._crash_after = None

            entries = []
            for item in planned["items"]:
                self._seq += 1
                entry = {
                    "seq": self._seq,
                    "op": action,
                    "path": item["path"],
                    "inode": item["inode"],
                    "state": "pending",
                    "batch": batch_id,
                }
                self._journal.append(entry)
                item["entry"] = entry
                entries.append(entry)

            batch = {
                "id": batch_id,
                "action": action,
                "status": "active",
                "entries": entries,
                "items": planned["items"],
            }
            self._batches[batch_id] = batch
            self._last_batch_id = batch_id

            applied_paths = []
            skipped_paths = []
            applied_items = []

            if crash_after == 0:
                batch["status"] = "crashed"
                raise ImmutableError("CRASH_SIMULATED", {
                    "batch": batch_id,
                    "after": 0,
                })

            for position, item in enumerate(planned["items"]):
                entry = item["entry"]
                if item["skip"]:
                    entry["state"] = "done"
                    skipped_paths.append(item["path"])
                    self._counters["skipped"] += 1
                else:
                    try:
                        self._apply_change_locked(item, action)
                    except ImmutableError:
                        entry["state"] = "failed"
                        self._restore_item_locked(item)

                        rolled_back = 0
                        for previous in reversed(applied_items):
                            self._restore_item_locked(previous)
                            previous["entry"]["state"] = "rolled_back"
                            rolled_back += 1
                        self._counters["rolled_back"] += rolled_back

                        for later in planned["items"][position + 1:]:
                            later_entry = later["entry"]
                            if later_entry["state"] == "pending":
                                later_entry["state"] = "cancelled"

                        batch["status"] = "failed"
                        raise

                    item["changed"] = True
                    entry["state"] = "done"
                    applied_items.append(item)
                    applied_paths.append(item["path"])

                if crash_after is not None and crash_after == position + 1:
                    batch["status"] = "crashed"
                    raise ImmutableError("CRASH_SIMULATED", {
                        "batch": batch_id,
                        "after": position + 1,
                    })

            batch["status"] = "done"
            if action == "set":
                self._counters["applied"] += len(applied_paths)
            else:
                self._counters["unset"] += len(applied_paths)

            return BatchResult(
                action=action,
                applied=tuple(applied_paths),
                skipped=tuple(skipped_paths),
                rolled_back=0,
            )
