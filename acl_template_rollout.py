"""ACL template rollout: plan, apply, rollback and crash recovery."""

from __future__ import annotations

import copy
import hashlib
import threading
import uuid
import warnings
from dataclasses import asdict, dataclass, replace


class AclRolloutError(ValueError):
    """Raised when a template rollout is refused or fails."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AclEntry:
    """One normalized access or default ACL entry."""

    tag: str
    qualifier: str
    perms: str


@dataclass(frozen=True)
class TemplateEntry:
    """One line of an ACL template, with its source line number."""

    tag: str
    qualifier: str
    perms: str
    default: bool = False
    line: int = 0


@dataclass(frozen=True)
class PathAcl:
    """In-memory stand-in for the ACL of one path."""

    dev: int
    inode: int
    is_dir: bool
    access: tuple = ()
    default: tuple = ()
    mtime_ns: int = 0


@dataclass(frozen=True)
class PathDiff:
    """Planned change for a single path."""

    path: str
    dev: int
    inode: int
    is_dir: bool
    original_access: tuple
    original_default: tuple
    desired_access: tuple
    desired_default: tuple
    current_fingerprint: str
    target_fingerprint: str


@dataclass(frozen=True)
class RolloutPlan:
    """A plan produced from a set of paths and the current ledger."""

    plan_id: str
    diffs: tuple


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying a plan."""

    plan_id: str
    applied: tuple
    rolled_back: tuple
    failed_path: object
    error_code: object
    written: int


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of rolling a plan back after the fact."""

    plan_id: str
    reverted: tuple
    skipped: tuple


@dataclass(frozen=True)
class _Location:
    """Current ledger location of a planned dev/inode identity."""

    path: str
    acl: object
    status: str


_TAG_ORDER = {"user": 0, "group": 1, "other": 2, "mask": 3}
_TAG_ALIASES = {"u": "user", "g": "group", "o": "other", "m": "mask"}
_STORE_LOCKS = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _lock_for_store(acl_store):
    key = id(acl_store)
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock


class AclTemplateRollout:
    """Apply longest-prefix ACL templates as a journaled transaction."""

    def __init__(self, acl_store=None, *, uid_map=None, gid_map=None,
                 max_paths: int = 256, max_entries: int = 64,
                 max_depth: int = 8, on_write=None) -> None:
        self.acl_store = {} if acl_store is None else acl_store
        self.uid_map = dict(uid_map or {})
        self.gid_map = dict(gid_map or {})
        self.max_paths = max_paths
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.on_write = on_write
        self._lock = _lock_for_store(self.acl_store)
        self._templates = {}
        self._journal = []
        self._plans = {}
        self._plan_seq = 0
        self._journal_seq = 0

    def add_path(self, path, *, dev, inode, is_dir, access=(),
                 default=(), mtime_ns=0):
        path = str(path)
        acl = PathAcl(
            dev=dev,
            inode=inode,
            is_dir=bool(is_dir),
            access=tuple(_coerce_acl_entry(item) for item in access),
            default=tuple(_coerce_acl_entry(item) for item in default),
            mtime_ns=mtime_ns,
        )
        with self._lock:
            self.acl_store[path] = acl
        return acl

    def add_template(self, prefix, entries):
        with self._lock:
            self._templates[str(prefix)] = tuple(entries)

    def effective_template(self, path):
        path = str(path)
        with self._lock:
            prefix = self._matching_prefix_locked(path)
            resolved = []
            for index, raw_entry in enumerate(self._templates[prefix]):
                entry = _coerce_template_entry(raw_entry, index)
                qualifier = self.resolve_principal(
                    entry.tag, entry.qualifier, entry.line or index + 1
                )
                resolved.append(replace(entry, qualifier=qualifier))
            return tuple(resolved)

    def resolve_principal(self, kind, name, line=None):
        kind = _TAG_ALIASES.get(kind, kind)
        if kind not in _TAG_ORDER:
            raise AclRolloutError(
                "ACL_ENTRY_INVALID",
                {"kind": kind, "name": name, "line": line},
            )
        if name is None or name == "":
            return ""

        name = str(name)
        principal_map = self.uid_map if kind == "user" else self.gid_map if kind == "group" else None
        if principal_map is not None:
            if name in principal_map:
                return str(principal_map[name])
            try:
                numeric_name = int(name)
            except ValueError:
                numeric_name = None
            if numeric_name is not None and numeric_name in principal_map:
                return str(principal_map[numeric_name])
            if name.isdigit():
                return name
            raise AclRolloutError(
                "ACL_PRINCIPAL_UNKNOWN",
                {"line": line, "name": name, "kind": kind},
            )
        return ""

    def recompute_masks(self, entries):
        normalized = tuple(_coerce_acl_entry(entry) for entry in entries)
        if not any(entry.tag == "mask" for entry in normalized):
            named = [
                entry
                for entry in normalized
                if entry.tag in ("user", "group") and entry.qualifier
            ]
            if named:
                bits = set()
                for entry in named:
                    bits.update(char for char in entry.perms if char in "rwx")
                mask_perms = "".join(char for char in "rwx" if char in bits)
                normalized += (AclEntry("mask", "", mask_perms),)
        return tuple(sorted(normalized, key=_entry_sort_key))

    def fingerprint(self, entries):
        if isinstance(entries, PathAcl):
            return _path_fingerprint(entries.access, entries.default)
        return _fingerprint_material(_entry_material(entries))

    def plan(self, paths):
        requested = tuple(paths)
        if len(requested) > self.max_paths:
            raise AclRolloutError(
                "ACL_PATH_LIMIT",
                {"count": len(requested), "limit": self.max_paths},
            )

        with self._lock:
            diffs = []
            for raw_path in requested:
                path = str(raw_path)
                depth = _path_depth(path)
                if depth > self.max_depth:
                    raise AclRolloutError(
                        "ACL_DEPTH_LIMIT",
                        {"path": path, "depth": depth, "limit": self.max_depth},
                    )
                if path not in self.acl_store:
                    raise AclRolloutError("ACL_PATH_NOT_FOUND", {"path": path})

                current = self.acl_store[path]
                prefix = self._matching_prefix_locked(path)
                raw_template = self._templates[prefix]
                if len(raw_template) > self.max_entries:
                    raise AclRolloutError(
                        "ACL_ENTRY_LIMIT",
                        {
                            "path": path,
                            "count": len(raw_template),
                            "limit": self.max_entries,
                        },
                    )
                template = self.effective_template(path)

                access_template = [entry for entry in template if not entry.default]
                default_template = [entry for entry in template if entry.default]
                desired_access = self.recompute_masks(access_template)
                desired_default = tuple(
                    sorted(
                        (
                            AclEntry(entry.tag, entry.qualifier, entry.perms)
                            for entry in default_template
                        ),
                        key=_entry_sort_key,
                    )
                )
                entry_count = len(desired_access) + len(desired_default)
                if entry_count > self.max_entries:
                    raise AclRolloutError(
                        "ACL_ENTRY_LIMIT",
                        {"path": path, "count": entry_count, "limit": self.max_entries},
                    )
                if desired_default and not current.is_dir:
                    raise AclRolloutError(
                        "ACL_DEFAULT_ON_FILE",
                        {"path": path},
                    )

                current_fingerprint = _path_fingerprint(
                    current.access, current.default
                )
                target_fingerprint = _path_fingerprint(
                    desired_access, desired_default
                )
                if current_fingerprint == target_fingerprint:
                    continue

                diffs.append(
                    PathDiff(
                        path=path,
                        dev=current.dev,
                        inode=current.inode,
                        is_dir=current.is_dir,
                        original_access=tuple(current.access),
                        original_default=tuple(current.default),
                        desired_access=desired_access,
                        desired_default=desired_default,
                        current_fingerprint=current_fingerprint,
                        target_fingerprint=target_fingerprint,
                    )
                )

            self._plan_seq += 1
            plan_id = f"plan-{self._plan_seq:06d}-{uuid.uuid4().hex[:12]}"
            plan = RolloutPlan(plan_id=plan_id, diffs=tuple(diffs))
            self._plans[plan_id] = plan
            return plan

    def diff(self, plan):
        with self._lock:
            plan = self._coerce_plan_locked(plan)
            drifted = []
            for diff in plan.diffs:
                location = self._locate_locked(diff.dev, diff.inode, diff.path)
                if location.status in ("missing", "replaced"):
                    drifted.append(diff.path)
                    continue
                current_fingerprint = _path_fingerprint(
                    location.acl.access, location.acl.default
                )
                if current_fingerprint != diff.current_fingerprint:
                    drifted.append(
                        location.path if location.status == "renamed" else diff.path
                    )
            return tuple(drifted)

    def apply(self, plan, *, now_ns=0):
        with self._lock:
            plan = self._coerce_plan_locked(plan)
            applied = []
            written_state = []
            rolled_back = []
            failed_path = None
            error_code = None

            for diff in plan.diffs:
                location = self._locate_locked(diff.dev, diff.inode, diff.path)
                if location.status == "missing":
                    raise AclRolloutError(
                        "ACL_PATH_NOT_FOUND", {"path": diff.path}
                    )
                if location.status == "replaced":
                    raise AclRolloutError(
                        "ACL_INODE_CHANGED",
                        {
                            "path": diff.path,
                            "planned_dev": diff.dev,
                            "planned_inode": diff.inode,
                            "current_dev": location.acl.dev,
                            "current_inode": location.acl.inode,
                        },
                    )

                current = location.acl
                current_fingerprint = _path_fingerprint(
                    current.access, current.default
                )
                if current_fingerprint == diff.target_fingerprint:
                    continue

                desired = replace(
                    current,
                    access=diff.desired_access,
                    default=diff.desired_default,
                    mtime_ns=now_ns,
                )
                target_fingerprint = _path_fingerprint(
                    desired.access, desired.default
                )
                begin = self._journal_entry_locked(
                    "begin",
                    plan.plan_id,
                    location.path,
                    diff.path,
                    current,
                    desired,
                    current_fingerprint,
                    target_fingerprint,
                    now_ns=now_ns,
                )
                self._journal.append(begin)

                try:
                    self.acl_store[location.path] = desired
                    if self.on_write is not None:
                        self.on_write(location.path, desired)
                except OSError:
                    failed_path = location.path
                    error_code = "ACL_WRITE_FAILED"
                    compensation = [
                        (
                            location.path,
                            diff.path,
                            current,
                            begin["begin_id"],
                            current_fingerprint,
                            target_fingerprint,
                        )
                    ]
                    compensation.extend(reversed(written_state))

                    for (
                        rollback_path,
                        planned_path,
                        original_acl,
                        begin_id,
                        original_fingerprint,
                        failed_fingerprint,
                    ) in compensation:
                        failed_acl = self.acl_store.get(rollback_path)
                        self.acl_store[rollback_path] = original_acl
                        restored_fingerprint = _path_fingerprint(
                            original_acl.access, original_acl.default
                        )
                        self._journal.append(
                            self._journal_entry_locked(
                                "rollback",
                                plan.plan_id,
                                rollback_path,
                                planned_path,
                                failed_acl,
                                original_acl,
                                _path_fingerprint(
                                    getattr(failed_acl, "access", ()),
                                    getattr(failed_acl, "default", ()),
                                )
                                if failed_acl is not None
                                else failed_fingerprint,
                                restored_fingerprint,
                                now_ns=now_ns,
                                begin_id=begin_id,
                            )
                        )
                        rolled_back.append(rollback_path)

                    return ApplyResult(
                        plan_id=plan.plan_id,
                        applied=tuple(applied),
                        rolled_back=tuple(rolled_back),
                        failed_path=failed_path,
                        error_code=error_code,
                        written=len(applied),
                    )

                done = self._journal_entry_locked(
                    "done",
                    plan.plan_id,
                    location.path,
                    diff.path,
                    current,
                    desired,
                    current_fingerprint,
                    target_fingerprint,
                    now_ns=now_ns,
                    begin_id=begin["begin_id"],
                )
                self._journal.append(done)
                applied.append(location.path)
                written_state.append(
                    (
                        location.path,
                        diff.path,
                        current,
                        begin["begin_id"],
                        current_fingerprint,
                        target_fingerprint,
                    )
                )

            return ApplyResult(
                plan_id=plan.plan_id,
                applied=tuple(applied),
                rolled_back=(),
                failed_path=None,
                error_code=None,
                written=len(applied),
            )

    def rollback(self, plan, *, request_id=None):
        with self._lock:
            plan = self._coerce_plan_locked(plan)
            reverted = []
            skipped = []

            for diff in reversed(plan.diffs):
                location = self._locate_locked(diff.dev, diff.inode, diff.path)
                if location.status == "replaced":
                    warnings.warn(
                        (
                            f"ACL rollback skipped {diff.path!r}: dev/inode changed "
                            f"from ({diff.dev}, {diff.inode}) to "
                            f"({location.acl.dev}, {location.acl.inode})"
                        ),
                        UserWarning,
                        stacklevel=2,
                    )
                    skipped.append(diff.path)
                    self._append_skip_locked(plan.plan_id, diff, location, request_id)
                    continue
                if location.status == "missing":
                    skipped.append(diff.path)
                    self._append_skip_locked(plan.plan_id, diff, location, request_id)
                    continue

                current = location.acl
                current_fingerprint = _path_fingerprint(
                    current.access, current.default
                )
                if current_fingerprint != diff.target_fingerprint:
                    skipped.append(location.path)
                    self._append_skip_locked(
                        plan.plan_id,
                        diff,
                        location,
                        request_id,
                        current_fingerprint=current_fingerprint,
                    )
                    continue

                original = self._find_original_acl_locked(plan.plan_id, diff, location.path)
                if original is None:
                    original = replace(
                        current,
                        access=diff.original_access,
                        default=diff.original_default,
                    )
                original_fingerprint = _path_fingerprint(
                    original.access, original.default
                )
                self.acl_store[location.path] = original
                self._journal.append(
                    self._journal_entry_locked(
                        "rollback",
                        plan.plan_id,
                        location.path,
                        diff.path,
                        current,
                        original,
                        current_fingerprint,
                        original_fingerprint,
                        request_id=request_id,
                    )
                )
                reverted.append(location.path)

            return RollbackResult(
                plan_id=plan.plan_id,
                reverted=tuple(reverted),
                skipped=tuple(skipped),
            )

    def journal(self):
        with self._lock:
            return copy.deepcopy(self._journal)

    def snapshot(self):
        with self._lock:
            return {
                "version": 1,
                "journal": copy.deepcopy(self._journal),
                "journal_seq": self._journal_seq,
                "plan_seq": self._plan_seq,
                "plans": {
                    plan_id: _jsonable(asdict(plan))
                    for plan_id, plan in self._plans.items()
                },
                "templates": {
                    prefix: [_jsonable(entry) for entry in entries]
                    for prefix, entries in self._templates.items()
                },
                "uid_map": dict(self.uid_map),
                "gid_map": dict(self.gid_map),
                "max_paths": self.max_paths,
                "max_entries": self.max_entries,
                "max_depth": self.max_depth,
            }

    def restore(self, state):
        if not isinstance(state, dict):
            raise AclRolloutError("ACL_INVALID_SNAPSHOT", {"type": type(state).__name__})
        with self._lock:
            self._journal = copy.deepcopy(state.get("journal", []))
            self._journal_seq = int(state.get("journal_seq", len(self._journal)))
            self._plan_seq = int(state.get("plan_seq", len(state.get("plans", {}))))
            self._plans = {}
            for plan_id, plan_state in state.get("plans", {}).items():
                if isinstance(plan_state, RolloutPlan):
                    plan = plan_state
                else:
                    plan = _plan_from_dict(plan_state)
                self._plans[plan_id] = plan

            self._templates = {
                str(prefix): tuple(entries)
                for prefix, entries in state.get("templates", {}).items()
            }
            if "uid_map" in state:
                self.uid_map = dict(state["uid_map"])
            if "gid_map" in state:
                self.gid_map = dict(state["gid_map"])
            for key in ("max_paths", "max_entries", "max_depth"):
                if key in state:
                    setattr(self, key, state[key])
            return None

    def recover(self):
        with self._lock:
            pending = self._pending_begins_locked()
            reverted = []
            for begin in pending:
                original = _path_acl_from_dict(begin.get("before_acl"))
                if original is None:
                    original = _path_acl_from_dict(begin.get("original_acl"))
                if original is None:
                    raise AclRolloutError(
                        "ACL_RECOVERY_FAILED",
                        {"plan_id": begin.get("plan_id"), "path": begin.get("path")},
                    )

                planned_path = begin.get("planned_path", begin.get("path"))
                location = self._locate_locked(
                    original.dev, original.inode, begin.get("path", planned_path)
                )
                if location.status in ("missing", "replaced"):
                    warnings.warn(
                        (
                            f"ACL recovery skipped {begin.get('path')!r}: "
                            f"identity status {location.status}"
                        ),
                        UserWarning,
                        stacklevel=2,
                    )
                    continue

                current = location.acl
                before_fingerprint = _path_fingerprint(
                    current.access, current.default
                )
                after_fingerprint = _path_fingerprint(
                    original.access, original.default
                )
                self.acl_store[location.path] = original
                self._journal.append(
                    self._journal_entry_locked(
                        "done",
                        begin.get("plan_id"),
                        location.path,
                        planned_path,
                        current,
                        original,
                        before_fingerprint,
                        after_fingerprint,
                        begin_id=begin.get("begin_id"),
                        extra={"recovered": True},
                    )
                )
                reverted.append(begin.get("path"))

            return tuple(reverted)

    def _matching_prefix_locked(self, path):
        path_segments = _path_segments(path)
        best_prefix = None
        best_segments = None
        for prefix in self._templates:
            prefix_segments = _path_segments(prefix)
            if path_segments[: len(prefix_segments)] != prefix_segments:
                continue
            if best_segments is None or len(prefix_segments) > len(best_segments):
                best_prefix = prefix
                best_segments = prefix_segments
        if best_prefix is None:
            raise AclRolloutError("ACL_NO_TEMPLATE", {"path": path})
        return best_prefix

    def _coerce_plan_locked(self, plan):
        if isinstance(plan, RolloutPlan):
            self._plans.setdefault(plan.plan_id, plan)
            return plan
        if isinstance(plan, str) and plan in self._plans:
            return self._plans[plan]
        if isinstance(plan, dict) and "plan_id" in plan:
            restored = _plan_from_dict(plan)
            self._plans.setdefault(restored.plan_id, restored)
            return restored
        raise AclRolloutError("ACL_PLAN_UNKNOWN", {"plan": str(plan)})

    def _locate_locked(self, dev, inode, preferred_path):
        preferred_path = str(preferred_path)
        preferred = self.acl_store.get(preferred_path)
        if (
            preferred is not None
            and getattr(preferred, "dev", None) == dev
            and getattr(preferred, "inode", None) == inode
        ):
            return _Location(preferred_path, preferred, "same")

        matches = []
        for path, acl in self.acl_store.items():
            if getattr(acl, "dev", None) == dev and getattr(acl, "inode", None) == inode:
                matches.append((str(path), acl))
        if matches:
            matches.sort(key=lambda item: item[0])
            actual_path, acl = matches[0]
            status = "same" if actual_path == preferred_path else "renamed"
            return _Location(actual_path, acl, status)

        if preferred is not None:
            return _Location(preferred_path, preferred, "replaced")
        return _Location(preferred_path, preferred, "missing")

    def _journal_entry_locked(
        self,
        kind,
        plan_id,
        path,
        planned_path,
        before_acl,
        after_acl,
        before_fingerprint,
        after_fingerprint,
        *,
        now_ns=0,
        request_id=None,
        begin_id=None,
        extra=None,
    ):
        self._journal_seq += 1
        dev = getattr(after_acl, "dev", None)
        inode = getattr(after_acl, "inode", None)
        if dev is None:
            dev = getattr(before_acl, "dev", None)
        if inode is None:
            inode = getattr(before_acl, "inode", None)
        entry = {
            "seq": self._journal_seq,
            "begin_id": begin_id or uuid.uuid4().hex,
            "kind": kind,
            "plan_id": plan_id,
            "request_id": request_id,
            "path": str(path),
            "planned_path": None if planned_path is None else str(planned_path),
            "dev": dev,
            "inode": inode,
            "at_ns": now_ns,
            "before_fingerprint": before_fingerprint,
            "after_fingerprint": after_fingerprint,
            "before_fp": before_fingerprint,
            "after_fp": after_fingerprint,
            "before_acl": _path_acl_to_dict(before_acl),
            "after_acl": _path_acl_to_dict(after_acl),
        }
        if extra:
            entry.update(extra)
        return entry

    def _append_skip_locked(self, plan_id, diff, location, request_id, *, current_fingerprint=None):
        if current_fingerprint is None:
            if location.acl is not None:
                current_fingerprint = _path_fingerprint(
                    location.acl.access, location.acl.default
                )
            else:
                current_fingerprint = _EMPTY_FINGERPRINT
        self._journal.append(
            self._journal_entry_locked(
                "skip",
                plan_id,
                location.path,
                diff.path,
                location.acl,
                location.acl,
                current_fingerprint,
                current_fingerprint,
                request_id=request_id,
                extra={"reason": location.status},
            )
        )

    def _pending_begins_locked(self):
        pending = {}
        order = []
        for record in self._journal:
            kind = record.get("kind")
            if kind == "begin":
                key = record.get("begin_id") or (
                    f"{record.get('plan_id')}:{record.get('path')}:{record.get('seq')}"
                )
                pending[key] = record
                order.append(key)
            elif kind in ("done", "rollback", "abort", "recovered"):
                key = record.get("begin_id")
                if key and key in pending:
                    pending.pop(key)
                    continue
                for candidate in reversed(order):
                    begin = pending.get(candidate)
                    if (
                        begin is not None
                        and begin.get("plan_id") == record.get("plan_id")
                        and begin.get("path") == record.get("path")
                    ):
                        pending.pop(candidate)
                        break
        return [pending[key] for key in order if key in pending]

    def _find_original_acl_locked(self, plan_id, diff, actual_path):
        for record in reversed(self._journal):
            if record.get("plan_id") != plan_id:
                continue
            if record.get("kind") not in ("begin", "done"):
                continue
            paths = {record.get("path"), record.get("planned_path")}
            if diff.path not in paths and actual_path not in paths:
                continue
            original = _path_acl_from_dict(record.get("before_acl"))
            if original is not None:
                return original
        return None


def _path_segments(path):
    return [segment for segment in str(path).split("/") if segment]


def _path_depth(path):
    return len(_path_segments(path))


def _entry_sort_key(entry):
    tag = _TAG_ALIASES.get(entry.tag, entry.tag)
    return (_TAG_ORDER.get(tag, len(_TAG_ORDER)), entry.qualifier or "")


def _coerce_template_entry(value, index=None):
    fallback_line = index + 1 if isinstance(index, int) else 0
    if isinstance(value, TemplateEntry):
        entry = value
        line = entry.line or fallback_line
        tag = _TAG_ALIASES.get(entry.tag, entry.tag)
        coerced = TemplateEntry(
            tag=tag,
            qualifier="" if entry.qualifier is None else str(entry.qualifier),
            perms="" if entry.perms is None else str(entry.perms),
            default=bool(entry.default),
            line=int(line or 0),
        )
    elif isinstance(value, AclEntry):
        tag = _TAG_ALIASES.get(value.tag, value.tag)
        coerced = TemplateEntry(tag, str(value.qualifier or ""), str(value.perms or ""), False, fallback_line)
    elif isinstance(value, dict):
        raw_tag = value.get("tag")
        tag = _TAG_ALIASES.get(raw_tag, raw_tag)
        line = value.get("line", fallback_line)
        coerced = TemplateEntry(
            tag=tag,
            qualifier=str(value.get("qualifier", "") or ""),
            perms=str(value.get("perms", "") or ""),
            default=bool(value.get("default", value.get("is_default", False))),
            line=int(line or 0),
        )
    elif isinstance(value, str):
        text = value.strip()
        parts = text.split(":")
        default = False
        if parts and parts[0] in ("default", "d"):
            default = True
            parts = parts[1:]
        if len(parts) != 3:
            raise AclRolloutError(
                "ACL_ENTRY_INVALID", {"entry": value, "line": fallback_line}
            )
        raw_tag, qualifier, perms = parts
        tag = _TAG_ALIASES.get(raw_tag, raw_tag)
        coerced = TemplateEntry(tag, qualifier, perms, default, fallback_line)
    elif isinstance(value, (tuple, list)):
        if len(value) == 3:
            raw_tag, qualifier, perms = value
            default = False
            line = fallback_line
        elif len(value) == 4:
            raw_tag, qualifier, perms, fourth = value
            if isinstance(fourth, bool):
                default = fourth
                line = fallback_line
            else:
                default = False
                line = fourth
        elif len(value) >= 5:
            raw_tag, qualifier, perms, default, line = value[:5]
        else:
            raise AclRolloutError(
                "ACL_ENTRY_INVALID", {"entry": value, "line": fallback_line}
            )
        tag = _TAG_ALIASES.get(raw_tag, raw_tag)
        coerced = TemplateEntry(
            tag,
            "" if qualifier is None else str(qualifier),
            "" if perms is None else str(perms),
            bool(default),
            int(line or fallback_line or 0),
        )
    else:
        tag = getattr(value, "tag", None)
        if tag is None:
            raise AclRolloutError(
                "ACL_ENTRY_INVALID", {"entry": str(value), "line": fallback_line}
            )
        tag = _TAG_ALIASES.get(tag, tag)
        coerced = TemplateEntry(
            tag,
            str(getattr(value, "qualifier", "") or ""),
            str(getattr(value, "perms", "") or ""),
            bool(getattr(value, "default", False)),
            int(getattr(value, "line", fallback_line) or 0),
        )

    if coerced.tag not in _TAG_ORDER:
        raise AclRolloutError(
            "ACL_ENTRY_INVALID",
            {"tag": coerced.tag, "line": coerced.line, "qualifier": coerced.qualifier},
        )
    if coerced.tag in ("other", "mask") and coerced.qualifier not in ("", None):
        raise AclRolloutError(
            "ACL_ENTRY_INVALID",
            {
                "tag": coerced.tag,
                "qualifier": coerced.qualifier,
                "line": coerced.line,
            },
        )
    return coerced


def _coerce_acl_entry(value):
    if isinstance(value, AclEntry):
        if value.tag not in _TAG_ORDER:
            raise AclRolloutError("ACL_ENTRY_INVALID", {"tag": value.tag})
        return value
    template_entry = _coerce_template_entry(value)
    return AclEntry(template_entry.tag, template_entry.qualifier, template_entry.perms)


def _entry_material(entries):
    normalized = [_coerce_acl_entry(entry) for entry in entries]
    normalized.sort(key=_entry_sort_key)
    return "\n".join(
        "\x1f".join((entry.tag, entry.qualifier or "", entry.perms or ""))
        for entry in normalized
    )


def _fingerprint_material(material):
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


_EMPTY_FINGERPRINT = _fingerprint_material("")


def _path_fingerprint(access, default):
    material = _entry_material(access or ())
    if default:
        material = material + "\x1e default:" + _entry_material(default or ())
    return _fingerprint_material(material)


def _entry_to_dict(entry):
    if isinstance(entry, AclEntry):
        return {"tag": entry.tag, "qualifier": entry.qualifier, "perms": entry.perms}
    if isinstance(entry, dict):
        return {
            "tag": entry.get("tag"),
            "qualifier": entry.get("qualifier", ""),
            "perms": entry.get("perms", ""),
        }
    return {
        "tag": getattr(entry, "tag"),
        "qualifier": getattr(entry, "qualifier", ""),
        "perms": getattr(entry, "perms", ""),
    }


def _path_acl_to_dict(acl):
    if acl is None:
        return None
    return {
        "dev": acl.dev,
        "inode": acl.inode,
        "is_dir": acl.is_dir,
        "access": [_entry_to_dict(entry) for entry in acl.access],
        "default": [_entry_to_dict(entry) for entry in acl.default],
        "mtime_ns": acl.mtime_ns,
    }


def _path_acl_from_dict(data):
    if data is None:
        return None
    if isinstance(data, PathAcl):
        return data
    return PathAcl(
        dev=data["dev"],
        inode=data["inode"],
        is_dir=data["is_dir"],
        access=tuple(_coerce_acl_entry(entry) for entry in data.get("access", ())),
        default=tuple(_coerce_acl_entry(entry) for entry in data.get("default", ())),
        mtime_ns=data.get("mtime_ns", 0),
    )


def _plan_from_dict(data):
    diffs = []
    for diff_data in data.get("diffs", ()):
        diffs.append(
            PathDiff(
                path=diff_data["path"],
                dev=diff_data["dev"],
                inode=diff_data["inode"],
                is_dir=diff_data["is_dir"],
                original_access=tuple(
                    AclEntry(**entry) if isinstance(entry, dict) else entry
                    for entry in diff_data.get("original_access", ())
                ),
                original_default=tuple(
                    AclEntry(**entry) if isinstance(entry, dict) else entry
                    for entry in diff_data.get("original_default", ())
                ),
                desired_access=tuple(
                    AclEntry(**entry) if isinstance(entry, dict) else entry
                    for entry in diff_data.get("desired_access", ())
                ),
                desired_default=tuple(
                    AclEntry(**entry) if isinstance(entry, dict) else entry
                    for entry in diff_data.get("desired_default", ())
                ),
                current_fingerprint=diff_data["current_fingerprint"],
                target_fingerprint=diff_data["target_fingerprint"],
            )
        )
    return RolloutPlan(plan_id=data["plan_id"], diffs=tuple(diffs))


def _jsonable(value):
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
