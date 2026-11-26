"""ACL snapshot export, comparison and journaled restore.

The reconciler keeps the simulated live subtree in ``targets``.  Writes are
delivered through the optional ``on_write(path, record)`` callback and are
recorded in a write-ahead journal so an interrupted run can be transferred to
a new instance and resumed without rewriting completed paths.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass


_SNAPSHOT_FORMAT = "acl-reconciler-snapshot/1"
_STATE_VERSION = 1
_RESTORE_POLICIES = frozenset({"keep", "merge", "replace"})
_LINK_POLICIES = frozenset({"link", "target", "follow"})
_SKIP_STRATEGIES = frozenset({"skip", "ignore", "keep_current", "abandon"})
_OVERRIDE_STRATEGIES = frozenset(
    {"accept", "force", "adopt", "overwrite", "accept_owner", "adopt_identity"}
)
_RESOLUTION_STRATEGIES = _SKIP_STRATEGIES | _OVERRIDE_STRATEGIES | frozenset({"retry"})


class AclSnapshotError(ValueError):
    """Raised when a snapshot or restore operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Record:
    """One path as recorded in a snapshot or in the live subtree."""

    path: str
    dev: int
    inode: int
    owner_uid: int
    owner_gid: int
    access: tuple = ()
    default: tuple = ()
    is_dir: bool = True
    symlink: object = None


@dataclass(frozen=True)
class Snapshot:
    """A canonicalized view of one subtree."""

    root: str
    entries: tuple
    digest: str
    stable: bool = True
    revision: int | None = None
    attempts: int = 1


@dataclass(frozen=True)
class Conflict:
    """One path that cannot be restored without a decision."""

    path: str
    reason: str
    current: object
    snapshot: object


@dataclass(frozen=True)
class RestoreReport:
    """Per-path outcome of a restore run."""

    applied: tuple = ()
    created: tuple = ()
    skipped: tuple = ()
    conflicted: tuple = ()
    interrupted: bool = False
    failed: tuple = ()
    next_path: str | None = None
    run_id: str | None = None
    symlink_policy: str | None = None
    symlink_actions: tuple = ()
    pending: tuple = ()
    error: object = None


class SegmentLease:
    """Small context-manager token returned by :meth:`acquire_segment`."""

    def __init__(self, reconciler: "AclSnapshotReconciler", segment: str) -> None:
        self._reconciler = reconciler
        self.segment = segment

    def release(self) -> None:
        self._reconciler.release_segment(self.segment)

    def __enter__(self) -> "SegmentLease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _acl_key(entry: object) -> str:
    text = str(entry)
    if ":" not in text:
        raise AclSnapshotError("ACL_ENTRY_INVALID", {"entry": entry})
    return text.rsplit(":", 1)[0]


def _union_permissions(left: object, right: object) -> str:
    left_text = str(left)
    right_text = str(right)
    if left_text.isdigit() and right_text.isdigit():
        return format(int(left_text, 8) | int(right_text, 8), "o")
    permissions = {
        char
        for char in left_text + right_text
        if char in {"r", "w", "x"}
    }
    return "".join(char for char in "rwx" if char in permissions)


def _merge_acl_entries(desired, current, policy: str) -> tuple:
    desired = tuple(desired or ())
    current = tuple(current or ())

    if policy == "replace":
        return desired

    if policy == "keep":
        desired_keys = {_acl_key(entry) for entry in desired}
        extras = tuple(entry for entry in current if _acl_key(entry) not in desired_keys)
        return desired + extras

    if policy == "merge":
        desired_by_key = {}
        for entry in desired:
            desired_by_key[_acl_key(entry)] = entry

        merged = []
        seen = set()
        for entry in current:
            key = _acl_key(entry)
            wanted = desired_by_key.get(key)
            if wanted is None:
                merged.append(entry)
            else:
                prefix = str(wanted).rsplit(":", 1)[0]
                permissions = _union_permissions(
                    str(wanted).rsplit(":", 1)[1],
                    str(entry).rsplit(":", 1)[1],
                )
                merged.append(f"{prefix}:{permissions}")
            seen.add(key)

        for entry in desired:
            key = _acl_key(entry)
            if key not in seen:
                merged.append(entry)
        return tuple(merged)

    raise AclSnapshotError(
        "ACL_POLICY_INVALID",
        {"policy": policy, "allowed": sorted(_RESTORE_POLICIES)},
    )


class AclSnapshotReconciler:
    """Export, compare and restore ACL state with a resumable journal."""

    _inode_registry_lock = threading.Lock()
    _inode_locks: dict[tuple, threading.RLock] = {}
    _STABILITY_ATTEMPTS = 128

    def __init__(
        self,
        *,
        max_conflicts: int = 8,
        known_uids=None,
        known_gids=None,
        symlink_policy: str = "link",
        on_write=None,
    ) -> None:
        if isinstance(max_conflicts, bool) or not isinstance(max_conflicts, int):
            raise AclSnapshotError(
                "ACL_CONFIG_INVALID",
                {"field": "max_conflicts", "value": max_conflicts},
            )
        if max_conflicts < 0:
            raise AclSnapshotError(
                "ACL_CONFIG_INVALID",
                {"field": "max_conflicts", "value": max_conflicts},
            )
        if symlink_policy not in _LINK_POLICIES:
            raise AclSnapshotError(
                "ACL_SYMLINK_POLICY_INVALID",
                {"policy": symlink_policy, "allowed": sorted(_LINK_POLICIES)},
            )
        if on_write is not None and not callable(on_write):
            raise AclSnapshotError("ACL_CONFIG_INVALID", {"field": "on_write"})

        self.max_conflicts = max_conflicts
        self.known_uids = None if known_uids is None else {int(uid) for uid in known_uids}
        self.known_gids = None if known_gids is None else {int(gid) for gid in known_gids}
        self.symlink_policy = symlink_policy
        self.on_write = on_write

        self.targets: dict[str, Record] = {}
        self._lock = threading.RLock()
        self._busy_segments: set[str] = set()
        self._journal: list[dict] = []
        self._pending: dict | None = None
        self._snapshot: Snapshot | None = None
        self._resolutions: dict[str, dict] = {}
        self._revision = 0

    # ------------------------------------------------------------------
    # Path ledger
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_path(path: object) -> str:
        if not isinstance(path, str) or not path:
            raise AclSnapshotError("ACL_PATH_INVALID", {"path": path})
        normalized = os.path.normpath(path)
        return "." if normalized == "" else normalized

    @staticmethod
    def _within_segment(path: str, segment: str) -> bool:
        if segment == os.sep:
            return path.startswith(os.sep)
        return path == segment or path.startswith(segment.rstrip(os.sep) + os.sep)

    @classmethod
    def _inode_lock_for(cls, key: tuple) -> threading.RLock:
        with cls._inode_registry_lock:
            lock = cls._inode_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._inode_locks[key] = lock
            return lock

    def _journal_event(self, event: str, **fields) -> dict:
        entry = {
            "seq": len(self._journal) + 1,
            "time": time.time(),
            "thread": threading.get_ident(),
            "event": event,
        }
        for key, value in fields.items():
            entry[key] = _jsonable(value)
        self._journal.append(entry)
        return copy.deepcopy(entry)

    def put(
        self,
        path,
        *,
        dev,
        inode,
        owner_uid,
        owner_gid,
        access=(),
        default=(),
        is_dir=True,
        symlink=None,
    ) -> Record:
        with self._lock:
            normalized_path = self._normalize_path(path)
            record = Record(
                path=normalized_path,
                dev=int(dev),
                inode=int(inode),
                owner_uid=int(owner_uid),
                owner_gid=int(owner_gid),
                access=tuple(str(item) for item in access),
                default=tuple(str(item) for item in default),
                is_dir=bool(is_dir),
                symlink=symlink,
            )
            self.targets[normalized_path] = record
            self._revision += 1
            self._journal_event("target_put", path=normalized_path)
            return record

    def drop(self, path) -> bool:
        with self._lock:
            normalized_path = self._normalize_path(path)
            existed = self.targets.pop(normalized_path, None) is not None
            if existed:
                self._revision += 1
                self._journal_event("target_drop", path=normalized_path)
            return existed

    # ------------------------------------------------------------------
    # Export and import
    # ------------------------------------------------------------------

    def _collect_entries(self, root: str) -> tuple[Record, ...]:
        if root == ".":
            return tuple(sorted(self.targets.values(), key=lambda item: item.path))
        entries = [
            record
            for path, record in self.targets.items()
            if path == root or self._within_segment(path, root)
        ]
        entries.sort(key=lambda item: item.path)
        return tuple(entries)

    @staticmethod
    def _snapshot_document(root: str, entries) -> dict:
        return {
            "format": _SNAPSHOT_FORMAT,
            "root": root,
            "entries": [_jsonable(record) for record in entries],
        }

    @classmethod
    def _digest(cls, root: str, entries) -> str:
        return hashlib.sha256(
            _canonical_json(cls._snapshot_document(root, entries))
        ).hexdigest()

    def snapshot(self, root) -> Snapshot:
        normalized_root = self._normalize_path(root)
        last_error = None

        for attempt in range(1, self._STABILITY_ATTEMPTS + 1):
            with self._lock:
                before = self._collect_entries(normalized_root)

            before_fingerprints = {
                record.path: self.fingerprint(record) for record in before
            }
            encoded = _canonical_json(
                self._snapshot_document(normalized_root, before)
            )

            with self._lock:
                after = self._collect_entries(normalized_root)
                revision_after = self._revision
            after_fingerprints = {
                record.path: self.fingerprint(record) for record in after
            }

            if before == after and before_fingerprints == after_fingerprints:
                digest = hashlib.sha256(encoded).hexdigest()
                snapshot = Snapshot(
                    root=normalized_root,
                    entries=before,
                    digest=digest,
                    stable=True,
                    revision=revision_after,
                    attempts=attempt,
                )
                with self._lock:
                    self._snapshot = snapshot
                if attempt > 1:
                    self._journal_event(
                        "snapshot_stable_after_retry",
                        root=normalized_root,
                        attempts=attempt,
                    )
                return snapshot

            last_error = {
                "root": normalized_root,
                "attempt": attempt,
                "before": sorted(before_fingerprints),
                "after": sorted(after_fingerprints),
            }
            self._journal_event("snapshot_unstable_retry", **last_error)
            time.sleep(0)

        raise AclSnapshotError("ACL_SNAPSHOT_UNSTABLE", last_error)

    def export_bytes(self, snap: Snapshot) -> bytes:
        if not isinstance(snap, Snapshot):
            raise AclSnapshotError("ACL_SNAPSHOT_INVALID", {"type": type(snap).__name__})
        return _canonical_json(self._snapshot_document(snap.root, snap.entries))

    @staticmethod
    def _record_from_doc(data: object) -> Record:
        if isinstance(data, Record):
            return data
        if not isinstance(data, dict):
            raise AclSnapshotError("ACL_SNAPSHOT_INVALID", {"record": data})
        try:
            return Record(
                path=str(data["path"]),
                dev=int(data["dev"]),
                inode=int(data["inode"]),
                owner_uid=int(data["owner_uid"]),
                owner_gid=int(data["owner_gid"]),
                access=tuple(data.get("access", ())),
                default=tuple(data.get("default", ())),
                is_dir=bool(data.get("is_dir", True)),
                symlink=data.get("symlink"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AclSnapshotError("ACL_SNAPSHOT_INVALID", {"error": str(exc)}) from exc

    @classmethod
    def _conflict_from_doc(cls, data: dict) -> Conflict:
        return Conflict(
            path=str(data["path"]),
            reason=str(data["reason"]),
            current=cls._record_from_doc(data["current"])
            if data.get("current") is not None
            else None,
            snapshot=cls._record_from_doc(data["snapshot"])
            if data.get("snapshot") is not None
            else None,
        )

    def import_bytes(self, data) -> Snapshot:
        if isinstance(data, str):
            raw = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray, memoryview)):
            raw = bytes(data)
        else:
            raise AclSnapshotError("ACL_IMPORT_INVALID", {"type": type(data).__name__})

        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AclSnapshotError("ACL_IMPORT_INVALID", {"error": str(exc)}) from exc
        if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
            raise AclSnapshotError("ACL_IMPORT_INVALID", {"document": _jsonable(document)})

        entries = tuple(self._record_from_doc(item) for item in document["entries"])
        root = self._normalize_path(document.get("root", "."))
        return Snapshot(
            root=root,
            entries=entries,
            digest=self._digest(root, entries),
            stable=bool(document.get("stable", True)),
            revision=document.get("revision"),
            attempts=int(document.get("attempts", 1) or 1),
        )

    @staticmethod
    def fingerprint(record) -> str:
        if not isinstance(record, Record):
            raise AclSnapshotError("ACL_RECORD_INVALID", {"type": type(record).__name__})
        document = {
            "path": record.path,
            "dev": record.dev,
            "inode": record.inode,
            "owner_uid": record.owner_uid,
            "owner_gid": record.owner_gid,
            "access": [str(item) for item in record.access],
            "default": [str(item) for item in record.default],
            "is_dir": record.is_dir,
            "symlink": record.symlink,
        }
        return hashlib.sha256(_canonical_json(document)).hexdigest()

    @staticmethod
    def merge_entries(desired, current, policy):
        return _merge_acl_entries(desired, current, policy)

    # ------------------------------------------------------------------
    # Conflict preflight
    # ------------------------------------------------------------------

    @staticmethod
    def _identity_reason(current: Record, wanted: Record) -> str | None:
        if current.inode != wanted.inode:
            return "INODE_CHANGED"
        if current.dev != wanted.dev:
            return "DEV_CHANGED"
        if bool(current.is_dir) != bool(wanted.is_dir):
            return "FILE_TYPE_CHANGED"
        if (current.symlink is not None) != (wanted.symlink is not None):
            return "SYMLINK_TYPE_CHANGED"
        return None

    def _owner_problems(self, wanted: Record) -> tuple[str, ...]:
        problems = []
        if self.known_uids is not None and wanted.owner_uid not in self.known_uids:
            problems.append("owner_uid")
        if self.known_gids is not None and wanted.owner_gid not in self.known_gids:
            problems.append("owner_gid")
        return tuple(problems)

    def _resolution_for(self, path: str) -> dict | None:
        resolution = self._resolutions.get(path)
        return copy.deepcopy(resolution) if resolution else None

    def _symlink_action(self, wanted: Record, current: Record | None) -> dict | None:
        target = None
        if current is not None and current.symlink is not None:
            target = current.symlink
        elif wanted.symlink is not None:
            target = wanted.symlink
        if target is None:
            return None

        scope = "link" if self.symlink_policy == "link" else "target"
        write_path = wanted.path
        resolved_target = None
        if scope == "target" and isinstance(target, str):
            if os.path.isabs(target):
                resolved_target = self._normalize_path(target)
            else:
                resolved_target = self._normalize_path(
                    os.path.join(os.path.dirname(wanted.path), target)
                )
            write_path = resolved_target

        return {
            "path": wanted.path,
            "target": target,
            "resolved_target": resolved_target,
            "scope": scope,
            "policy": self.symlink_policy,
            "write_path": write_path,
        }

    def _build_actions(
        self, snapshot: Snapshot, *, policy: str, allow_create: bool
    ) -> tuple[list[dict], list[Conflict]]:
        actions: list[dict] = []
        conflicts: list[Conflict] = []

        for wanted in snapshot.entries:
            path = wanted.path
            current = self.targets.get(path)
            resolution = self._resolution_for(path)
            strategy = resolution.get("strategy") if resolution else None

            if strategy in _SKIP_STRATEGIES:
                actions.append(
                    {
                        "op": "skip",
                        "path": path,
                        "reason": "RESOLVED_SKIP",
                        "resolution": resolution,
                        "symlink_action": self._symlink_action(wanted, current),
                    }
                )
                continue

            if current is None:
                if allow_create:
                    record = Record(
                        path=path,
                        dev=wanted.dev,
                        inode=wanted.inode,
                        owner_uid=wanted.owner_uid,
                        owner_gid=wanted.owner_gid,
                        access=tuple(wanted.access),
                        default=tuple(wanted.default),
                        is_dir=wanted.is_dir,
                        symlink=wanted.symlink,
                    )
                    actions.append(
                        {
                            "op": "create",
                            "path": path,
                            "record": _jsonable(record),
                            "symlink_action": self._symlink_action(wanted, None),
                        }
                    )
                else:
                    actions.append(
                        {
                            "op": "skip",
                            "path": path,
                            "reason": "MISSING",
                            "symlink_action": self._symlink_action(wanted, None),
                        }
                    )
                continue

            identity_reason = self._identity_reason(current, wanted)
            identity_overridden = (
                identity_reason is not None and strategy in _OVERRIDE_STRATEGIES
            )
            if identity_reason is not None and not identity_overridden:
                conflict = Conflict(path, identity_reason, current, wanted)
                conflicts.append(conflict)
                actions.append(
                    {
                        "op": "conflict",
                        "path": path,
                        "conflict": _jsonable(conflict),
                        "symlink_action": self._symlink_action(wanted, current),
                    }
                )
                continue

            owner_problems = self._owner_problems(wanted)
            owner_overridden = bool(owner_problems) and strategy in _OVERRIDE_STRATEGIES
            if owner_problems and not owner_overridden:
                conflict = Conflict(path, "OWNER_UNKNOWN", current, wanted)
                conflicts.append(conflict)
                actions.append(
                    {
                        "op": "conflict",
                        "path": path,
                        "conflict": _jsonable(conflict),
                        "owner_problems": list(owner_problems),
                        "symlink_action": self._symlink_action(wanted, current),
                    }
                )
                continue

            symlink_action = self._symlink_action(wanted, current)
            plan_current = current
            write_path = path
            if symlink_action and symlink_action["scope"] == "target":
                resolved_target = symlink_action.get("resolved_target")
                if resolved_target:
                    write_path = resolved_target
                    plan_current = self.targets.get(resolved_target)

            if plan_current is None:
                access_plan = tuple(wanted.access)
                default_plan = tuple(wanted.default)
                changes = ["owner_uid", "owner_gid"]
                if access_plan:
                    changes.append("access")
                if default_plan:
                    changes.append("default")
                next_record = Record(
                    path=write_path,
                    dev=wanted.dev,
                    inode=wanted.inode,
                    owner_uid=wanted.owner_uid,
                    owner_gid=wanted.owner_gid,
                    access=access_plan,
                    default=default_plan,
                    is_dir=wanted.is_dir,
                    symlink=None,
                )
                skip_reason = None
            else:
                access_plan = tuple(
                    _merge_acl_entries(wanted.access, plan_current.access, policy)
                )
                default_plan = tuple(
                    _merge_acl_entries(wanted.default, plan_current.default, policy)
                )

                changes = []
                if plan_current.owner_uid != wanted.owner_uid:
                    changes.append("owner_uid")
                if plan_current.owner_gid != wanted.owner_gid:
                    changes.append("owner_gid")
                if tuple(plan_current.access) != access_plan:
                    changes.append("access")
                if tuple(plan_current.default) != default_plan:
                    changes.append("default")

                next_record = Record(
                    path=write_path,
                    dev=plan_current.dev,
                    inode=plan_current.inode,
                    owner_uid=wanted.owner_uid
                    if "owner_uid" in changes
                    else plan_current.owner_uid,
                    owner_gid=wanted.owner_gid
                    if "owner_gid" in changes
                    else plan_current.owner_gid,
                    access=access_plan if "access" in changes else plan_current.access,
                    default=default_plan
                    if "default" in changes
                    else plan_current.default,
                    is_dir=plan_current.is_dir,
                    symlink=plan_current.symlink,
                )
                skip_reason = "UNCHANGED"

            if not changes:
                actions.append(
                    {
                        "op": "skip",
                        "path": path,
                        "write_path": write_path,
                        "reason": skip_reason or "UNCHANGED",
                        "symlink_action": symlink_action,
                    }
                )
                continue

            actions.append(
                {
                    "op": "write",
                    "path": path,
                    "write_path": write_path,
                    "record": _jsonable(next_record),
                    "changes": changes,
                    "policy": policy,
                    "identity_overridden": identity_overridden,
                    "owner_overridden": owner_overridden,
                    "resolution": resolution,
                    "symlink_action": symlink_action,
                }
            )

        return actions, conflicts

    def compare(self, snap: Snapshot):
        if not isinstance(snap, Snapshot):
            raise AclSnapshotError("ACL_SNAPSHOT_INVALID", {"type": type(snap).__name__})
        with self._lock:
            _, conflicts = self._build_actions(
                snap, policy="keep", allow_create=False
            )
            return list(conflicts)

    # ------------------------------------------------------------------
    # Segments and conflict decisions
    # ------------------------------------------------------------------

    def _paths_busy(self, paths) -> str | None:
        for segment in sorted(self._busy_segments):
            for path in paths:
                if path and self._within_segment(path, segment):
                    return segment
        return None

    def _assert_paths_not_busy(self, paths) -> None:
        segment = self._paths_busy(paths)
        if segment is not None:
            raise AclSnapshotError(
                "ACL_SEGMENT_BUSY",
                {"segment": segment, "paths": list(paths)},
            )

    def acquire_segment(self, segment) -> SegmentLease:
        normalized = self._normalize_path(segment)
        with self._lock:
            if normalized in self._busy_segments:
                raise AclSnapshotError(
                    "ACL_SEGMENT_BUSY", {"segment": normalized, "holder": "unknown"}
                )
            self._busy_segments.add(normalized)
            self._journal_event("segment_acquired", segment=normalized)
            return SegmentLease(self, normalized)

    def release_segment(self, segment) -> bool:
        normalized = self._normalize_path(segment)
        with self._lock:
            if normalized not in self._busy_segments:
                raise AclSnapshotError("ACL_SEGMENT_NOT_HELD", {"segment": normalized})
            self._busy_segments.remove(normalized)
            self._journal_event("segment_released", segment=normalized)
            return True

    def resolve_conflict(self, path, *, strategy):
        normalized_path = self._normalize_path(path)
        if strategy not in _RESOLUTION_STRATEGIES:
            raise AclSnapshotError(
                "ACL_RESOLUTION_INVALID",
                {"strategy": strategy, "allowed": sorted(_RESOLUTION_STRATEGIES)},
            )
        with self._lock:
            decision = {
                "path": normalized_path,
                "strategy": strategy,
                "time": time.time(),
                "seq": len(self._journal) + 1,
            }
            self._resolutions[normalized_path] = decision
            self._journal_event("conflict_resolved", **decision)
            return copy.deepcopy(decision)

    # ------------------------------------------------------------------
    # Restore execution and resume
    # ------------------------------------------------------------------

    def _make_pending(self, snap, *, policy, allow_create, actions) -> dict:
        run_id = uuid.uuid4().hex
        return {
            "version": _STATE_VERSION,
            "run_id": run_id,
            "status": "ready",
            "root": snap.root,
            "snapshot": self._snapshot_document(snap.root, snap.entries),
            "snapshot_digest": snap.digest,
            "policy": policy,
            "allow_create": bool(allow_create),
            "symlink_policy": self.symlink_policy,
            "cursor": 0,
            "actions": _jsonable(actions),
            "last_error": None,
        }

    def _apply_action(self, action: dict, pending: dict) -> None:
        op = action.get("op")
        if op not in {"create", "write"}:
            return

        report_path = action["path"]
        callback_path = action.get("write_path") or report_path
        record = self._record_from_doc(action["record"])
        key = (record.dev, record.inode)

        with self._lock:
            aliases = sorted(
                alias_path
                for alias_path, alias_record in self.targets.items()
                if alias_path != record.path
                and (alias_record.dev, alias_record.inode) == key
            )

        inode_lock = self._inode_lock_for(key)
        with inode_lock:
            self._journal_event(
                "write_begin",
                run_id=pending.get("run_id"),
                op=op,
                path=report_path,
                write_path=callback_path,
                changes=action.get("changes", ()),
                inode_key=list(key),
                hardlink_aliases=aliases,
                symlink_action=action.get("symlink_action"),
                writer=str(
                    getattr(self.on_write, "__qualname__", None)
                    or getattr(self.on_write, "__name__", None)
                    or self.on_write.__class__.__name__
                ),
            )
            if self.on_write is not None:
                self.on_write(callback_path, record)

            with self._lock:
                self.targets[record.path] = record
                self._revision += 1

            self._journal_event(
                "write_committed",
                run_id=pending.get("run_id"),
                op=op,
                path=report_path,
                write_path=callback_path,
                changes=action.get("changes", ()),
                inode_key=list(key),
                writer=str(
                    getattr(self.on_write, "__qualname__", None)
                    or getattr(self.on_write, "__name__", None)
                    or (
                        self.on_write.__class__.__name__
                        if self.on_write is not None
                        else "ledger"
                    )
                ),
            )

    def _run_pending(self, pending: dict) -> RestoreReport:
        run_id = pending.get("run_id")
        actions = pending.get("actions", [])
        applied: list[str] = []
        created: list[str] = []
        skipped: list[str] = []
        conflicted: list[Conflict] = []
        symlink_actions: list[dict] = []

        with self._lock:
            while int(pending.get("cursor", 0)) < len(actions):
                index = int(pending.get("cursor", 0))
                action = actions[index]
                path = action.get("path")
                symlink_action = action.get("symlink_action")

                try:
                    self._apply_action(action, pending)
                except Exception as exc:  # reported as interrupted and resumable
                    error = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "code": getattr(exc, "code", None),
                        "detail": getattr(exc, "detail", None),
                    }
                    pending["status"] = "interrupted"
                    pending["last_error"] = error
                    self._journal_event(
                        "restore_interrupted",
                        run_id=run_id,
                        path=path,
                        cursor=index,
                        error=error,
                    )
                    if symlink_action:
                        symlink_actions.append(symlink_action)
                    remaining = tuple(
                        item.get("path")
                        for item in actions[index:]
                        if item.get("path") is not None
                    )
                    return RestoreReport(
                        applied=tuple(applied),
                        created=tuple(created),
                        skipped=tuple(skipped),
                        conflicted=tuple(conflicted),
                        interrupted=True,
                        failed=(path,) if path is not None else (),
                        next_path=path,
                        run_id=run_id,
                        symlink_policy=pending.get("symlink_policy", self.symlink_policy),
                        symlink_actions=tuple(symlink_actions),
                        pending=remaining,
                        error=error,
                    )

                op = action.get("op")
                if op == "write":
                    applied.append(path)
                elif op == "create":
                    created.append(path)
                elif op == "skip":
                    skipped.append(path)
                elif op == "conflict":
                    conflicted.append(self._conflict_from_doc(action["conflict"]))

                if symlink_action:
                    symlink_actions.append(symlink_action)

                pending["cursor"] = index + 1

            pending["status"] = "completed"
            pending["last_error"] = None
            self._pending = None
            self._journal_event("restore_completed", run_id=run_id)
            return RestoreReport(
                applied=tuple(applied),
                created=tuple(created),
                skipped=tuple(skipped),
                conflicted=tuple(conflicted),
                interrupted=False,
                run_id=run_id,
                symlink_policy=pending.get("symlink_policy", self.symlink_policy),
                symlink_actions=tuple(symlink_actions),
            )

    def restore(
        self,
        snap: Snapshot,
        *,
        allow_create: bool = False,
        policy: str = "keep",
    ) -> RestoreReport:
        if not isinstance(snap, Snapshot):
            raise AclSnapshotError("ACL_SNAPSHOT_INVALID", {"type": type(snap).__name__})
        if policy not in _RESTORE_POLICIES:
            raise AclSnapshotError(
                "ACL_POLICY_INVALID",
                {"policy": policy, "allowed": sorted(_RESTORE_POLICIES)},
            )

        with self._lock:
            affected_paths = [snap.root] + [record.path for record in snap.entries]
            self._assert_paths_not_busy(affected_paths)

            actions, conflicts = self._build_actions(
                snap, policy=policy, allow_create=allow_create
            )
            if len(conflicts) > self.max_conflicts:
                detail = {
                    "conflict_count": len(conflicts),
                    "max_conflicts": self.max_conflicts,
                    "count": len(conflicts),
                    "limit": self.max_conflicts,
                    "paths": [conflict.path for conflict in conflicts],
                    "reasons": [conflict.reason for conflict in conflicts],
                    "conflicts": _jsonable(conflicts),
                    "items": _jsonable(conflicts),
                }
                self._journal_event("restore_aborted_conflict_limit", **detail)
                raise AclSnapshotError("ACL_CONFLICT_LIMIT", detail)

            pending = self._make_pending(
                snap,
                policy=policy,
                allow_create=allow_create,
                actions=actions,
            )
            self._pending = pending
            self._journal_event(
                "restore_started",
                run_id=pending["run_id"],
                root=snap.root,
                policy=policy,
                allow_create=allow_create,
                action_count=len(actions),
                conflict_count=len(conflicts),
            )
            return self._run_pending(pending)

    def resume(self) -> RestoreReport:
        with self._lock:
            if not self._pending:
                return RestoreReport(
                    run_id=None,
                    symlink_policy=self.symlink_policy,
                )

            pending = self._pending
            remaining_paths = [
                action.get("path")
                for action in pending.get("actions", [])[int(pending.get("cursor", 0)) :]
                if action.get("path") is not None
            ]
            self._assert_paths_not_busy(remaining_paths)
            pending["status"] = "running"
            self._journal_event("restore_resumed", run_id=pending.get("run_id"))
            return self._run_pending(pending)

    # ------------------------------------------------------------------
    # Journal and portable state
    # ------------------------------------------------------------------

    def journal(self):
        with self._lock:
            return copy.deepcopy(self._journal)

    def save_state(self):
        with self._lock:
            state = {
                "version": _STATE_VERSION,
                "config": {
                    "max_conflicts": self.max_conflicts,
                    "known_uids": None
                    if self.known_uids is None
                    else sorted(self.known_uids),
                    "known_gids": None
                    if self.known_gids is None
                    else sorted(self.known_gids),
                    "symlink_policy": self.symlink_policy,
                },
                "targets": [_jsonable(record) for record in self.targets.values()],
                "busy_segments": sorted(self._busy_segments),
                "journal": copy.deepcopy(self._journal),
                "pending": copy.deepcopy(self._pending),
                "resolutions": copy.deepcopy(self._resolutions),
                "revision": self._revision,
                "last_snapshot": _jsonable(self._snapshot) if self._snapshot else None,
            }
            return json.loads(json.dumps(state, default=str))

    def load_state(self, state) -> "AclSnapshotReconciler":
        if isinstance(state, (bytes, bytearray, memoryview)):
            state = json.loads(bytes(state).decode("utf-8"))
        elif isinstance(state, str):
            state = json.loads(state)
        if not isinstance(state, dict):
            raise AclSnapshotError("ACL_STATE_INVALID", {"type": type(state).__name__})

        config = state.get("config") or {}
        with self._lock:
            if "max_conflicts" in config:
                self.max_conflicts = int(config["max_conflicts"])
            self.known_uids = (
                None
                if config.get("known_uids") is None
                else {int(uid) for uid in config["known_uids"]}
            )
            self.known_gids = (
                None
                if config.get("known_gids") is None
                else {int(gid) for gid in config["known_gids"]}
            )
            if config.get("symlink_policy") in _LINK_POLICIES:
                self.symlink_policy = config["symlink_policy"]

            self.targets = {}
            for item in state.get("targets", []):
                record = self._record_from_doc(item)
                self.targets[record.path] = record

            self._busy_segments = set(state.get("busy_segments", []))
            self._journal = copy.deepcopy(state.get("journal", []))
            self._pending = copy.deepcopy(state.get("pending"))
            self._resolutions = copy.deepcopy(state.get("resolutions", {}))
            self._revision = int(state.get("revision", len(self.targets)))
            snapshot_doc = state.get("last_snapshot")
            if snapshot_doc and isinstance(snapshot_doc, dict):
                try:
                    entries = tuple(
                        self._record_from_doc(item)
                        for item in snapshot_doc.get("entries", [])
                    )
                    self._snapshot = Snapshot(
                        root=str(snapshot_doc.get("root", ".")),
                        entries=entries,
                        digest=str(snapshot_doc.get("digest", "")),
                        stable=bool(snapshot_doc.get("stable", True)),
                        revision=snapshot_doc.get("revision"),
                        attempts=int(snapshot_doc.get("attempts", 1) or 1),
                    )
                except AclSnapshotError:
                    self._snapshot = None
            else:
                self._snapshot = None

            return self
