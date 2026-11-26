"""Bind-mount preflight, execution bookkeeping, unmount and stack accounting."""

from __future__ import annotations

import os
import re
import stat as stat_module
import threading
import time
from copy import deepcopy
from typing import Any


_STATE_VERSION = 1
_STAT_KEYS = ("validated", "applied", "unmounted", "rejected")


class MountCheckError(ValueError):
    """Refused bind-mount operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail

    def __repr__(self) -> str:
        return self.code


class MountTargetValidator:
    """Validate and track bind mounts without leaving half-applied state."""

    def __init__(self, allowed_source_roots=("/srv",), max_mounts: int = 8,
                 max_depth: int = 4, bind_supported: bool = True,
                 clock=None) -> None:
        self.allowed_source_roots = tuple(allowed_source_roots)
        self.max_mounts = max_mounts
        self.max_depth = max_depth
        self.bind_supported = bind_supported
        self._clock = clock

        self._lock = threading.RLock()
        self._busy_targets: set[str] = set()
        self._pending_capacity = 0
        self._mounts: list[dict[str, Any]] = []
        self._stats = {key: 0 for key in _STAT_KEYS}
        self._mountinfo_path = "/proc/self/mountinfo"

    def resolve(self, path: str) -> str:
        """Return canonical path, resolving intermediate/final symlinks."""
        return os.path.realpath(os.path.abspath(os.fspath(path)))

    def validate(self, source: str, target: str, recursive: bool = False,
                 read_only: bool = False, overmount: bool = False,
                 allow_create: bool = False) -> dict:
        try:
            result = self._evaluate(
                source,
                target,
                recursive=recursive,
                read_only=read_only,
                overmount=overmount,
                allow_create=allow_create,
            )
        except MountCheckError:
            self._increment_stat("rejected")
            raise
        self._increment_stat("validated")
        return result

    def apply(self, source: str, target: str, recursive: bool = False,
              read_only: bool = False, overmount: bool = False,
              allow_create: bool = False,
              receipt: dict | None = None) -> dict:
        target_key = self.resolve(target)
        acquired = False
        created_path: str | None = None
        reserved = False
        backend_completed = False
        committed = False

        try:
            with self._lock:
                if target_key in self._busy_targets:
                    raise MountCheckError(
                        "TARGET_BUSY",
                        {"target": target_key, "reason": "apply_in_progress"},
                    )
                self._busy_targets.add(target_key)
                acquired = True

            evaluation = self._evaluate(
                source,
                target,
                recursive=recursive,
                read_only=read_only,
                overmount=overmount,
                allow_create=allow_create,
            )

            source_changed = False
            if receipt is not None:
                if not isinstance(receipt, dict):
                    raise MountCheckError(
                        "RECEIPT_INVALID",
                        {"receipt_type": type(receipt).__name__},
                    )
                current_receipt = evaluation["receipt"]
                self._check_target_receipt(receipt, current_receipt)
                source_changed = self._source_identity_changed(
                    receipt, current_receipt
                )

            with self._lock:
                top = self._top_record_locked(target_key)
                if top is not None and not overmount:
                    raise MountCheckError("OVERMOUNT_REQUIRED",
                                          {"target": target_key})
                if len(self._mounts) + self._pending_capacity >= self.max_mounts:
                    raise MountCheckError("CAPACITY_EXCEEDED", {
                        "target": target_key,
                        "mount_count": len(self._mounts),
                        "pending": self._pending_capacity,
                        "max_mounts": self.max_mounts,
                    })
                self._pending_capacity += 1
                reserved = True
                covered_source = top.get("source") if top is not None else None
                actual_overmount = top is not None

            if evaluation["would_create"]:
                try:
                    os.mkdir(target_key)
                except FileExistsError as exc:
                    raise MountCheckError("TARGET_CHANGED", {
                        "target": target_key,
                        "reason": "created_by_another_operation",
                    }) from exc
                except OSError as exc:
                    raise MountCheckError("TARGET_CREATE_FAILED", {
                        "target": target_key,
                        "error": repr(exc),
                    }) from exc
                created_path = target_key

            final_evaluation = self._evaluate(
                source,
                target,
                recursive=recursive,
                read_only=read_only,
                overmount=overmount,
                allow_create=allow_create,
            )
            if final_evaluation["target"] != target_key:
                raise MountCheckError("TARGET_CHANGED", {
                    "target": target_key,
                    "current_target": final_evaluation["target"],
                    "reason": "resolved_path_changed",
                })
            if receipt is not None:
                if not evaluation["would_create"]:
                    self._check_target_receipt(
                        receipt, final_evaluation["receipt"]
                    )
                source_changed = source_changed or self._source_identity_changed(
                    receipt, final_evaluation["receipt"]
                )

            options = {
                "recursive": recursive,
                "read_only": read_only,
                "overmount": actual_overmount,
            }
            backend_info = self._perform_bind(
                final_evaluation["source"], target_key, options
            )
            backend_completed = True

            record = self._build_record(
                final_evaluation,
                target_key=target_key,
                created_target=created_path is not None,
                actual_overmount=actual_overmount,
                covered_source=covered_source,
                source_revalidated=source_changed,
                backend_info=backend_info,
            )
            response = deepcopy(record)

            with self._lock:
                self._mounts.append(record)
                self._stats["applied"] += 1
                self._pending_capacity -= 1
                reserved = False
                committed = True

            return response

        except MountCheckError:
            self._increment_stat("rejected")
            raise
        except Exception as exc:
            self._increment_stat("rejected")
            raise MountCheckError("APPLY_FAILED",
                                  {"error": repr(exc)}) from exc
        finally:
            if not committed:
                self._rollback_failed_apply(
                    created_path, reserved, backend_completed
                )
            if acquired:
                with self._lock:
                    self._busy_targets.discard(target_key)

    def unmount(self, target: str) -> dict:
        target_key = self.resolve(target)
        acquired = False
        record: dict[str, Any] | None = None

        try:
            with self._lock:
                if target_key in self._busy_targets:
                    raise MountCheckError("TARGET_BUSY",
                                          {"target": target_key,
                                           "reason": "operation_in_progress"})
                index = self._last_index_locked(target_key)
                if index is None:
                    raise MountCheckError("NOT_MOUNTED",
                                          {"target": target_key})
                record = self._mounts[index]
                self._busy_targets.add(target_key)
                acquired = True

            shared = self._is_shared_mount(target_key)
            if shared:
                shared_info = self._shared_mount_info(target_key)
                raise MountCheckError("SHARED_MOUNT", {
                    "target": target_key,
                    "mountinfo": shared_info,
                })

            self._perform_unbind(record)

            unmounted_at = self._now()
            with self._lock:
                try:
                    self._mounts.remove(record)
                except ValueError as exc:
                    raise MountCheckError("NOT_MOUNTED",
                                          {"target": target_key}) from exc
                self._stats["unmounted"] += 1

            result = deepcopy(record)
            result["status"] = "unmounted"
            result["unmounted"] = True
            result["unmounted_at"] = unmounted_at
            return result

        except MountCheckError:
            self._increment_stat("rejected")
            raise
        except Exception as exc:
            self._increment_stat("rejected")
            raise MountCheckError("UNMOUNT_FAILED", {
                "target": target_key,
                "error": repr(exc),
            }) from exc
        finally:
            if acquired:
                with self._lock:
                    self._busy_targets.discard(target_key)

    def is_mounted(self, target: str) -> bool:
        target_key = self.resolve(target)
        with self._lock:
            return any(item.get("target") == target_key
                       for item in self._mounts)

    def mounts(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._mounts)

    def report(self) -> dict:
        with self._lock:
            records = deepcopy(self._mounts)
            stats = deepcopy(self._stats)

        mountpoints = [record.get("target") for record in records]
        active_mountpoints: list[str] = []
        for mountpoint in mountpoints:
            if mountpoint not in active_mountpoints:
                active_mountpoints.append(mountpoint)

        overmounts = sum(
            1 for record in records
            if record.get("covered_source") is not None
            or record.get("overmount") is True
        )

        resolved_paths = []
        path_map: dict[str, str] = {}
        for item in records:
            source_literal = item.get("source_literal", item.get("source"))
            target_literal = item.get("target_literal", item.get("target"))
            entry = {
                "source_literal": source_literal,
                "target_literal": target_literal,
                "source": item.get("source"),
                "target": item.get("target"),
                "resolved_source": item.get("resolved_source",
                                            item.get("source")),
                "resolved_target": item.get("resolved_target",
                                            item.get("target")),
            }
            resolved_paths.append(entry)
            if source_literal is not None:
                path_map[f"source:{source_literal}"] = entry["resolved_source"]
            if target_literal is not None:
                path_map[f"target:{target_literal}"] = entry["resolved_target"]
                path_map[target_literal] = entry["resolved_target"]

        return {
            "ok": True,
            "mounts": records,
            "mountpoints": mountpoints,
            "mount_points": mountpoints,
            "resolved_mountpoints": mountpoints,
            "resolved_targets": mountpoints,
            "resolved_sources": [item.get("source") for item in records],
            "mount_entries": mountpoints,
            "active_mountpoints": active_mountpoints,
            "mountpoint_count": len(active_mountpoints),
            "count": len(records),
            "mount_count": len(records),
            "overmounts": overmounts,
            "overmount_count": overmounts,
            "paths": resolved_paths,
            "resolved_paths": resolved_paths,
            "resolved_path_strings": mountpoints,
            "resolved_path_map": path_map,
            "path_map": path_map,
            "stats": stats,
            "max_mounts": self.max_mounts,
            "max_depth": self.max_depth,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy({
                "version": _STATE_VERSION,
                "mounts": self._mounts,
                "stats": self._stats,
            })

    def restore(self, state: dict) -> None:
        try:
            mounts, stats = self._parse_state(state)
        except MountCheckError:
            raise
        except Exception as exc:
            raise MountCheckError(
                "STATE_INVALID", {"reason": "unparseable_state"}
            ) from exc
        with self._lock:
            self._mounts = mounts
            self._stats = stats
            self._pending_capacity = 0

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    # ----- internal helpers -------------------------------------------------

    def _increment_stat(self, key: str) -> None:
        with self._lock:
            self._stats[key] += 1

    def _now(self):
        if self._clock is None:
            return time.monotonic()
        if callable(self._clock):
            return self._clock()
        return self._clock

    def _allowed_root_and_depth(self, source_real: str) -> tuple[str, int]:
        candidates: list[tuple[str, int]] = []
        for raw_root in self.allowed_source_roots:
            root_real = self.resolve(raw_root)
            try:
                common = os.path.commonpath((root_real, source_real))
            except ValueError:
                continue
            if common != root_real:
                continue

            relative = os.path.relpath(source_real, root_real)
            if relative == ".":
                depth = 0
            else:
                depth = len([part for part in relative.split(os.sep)
                             if part not in ("", ".")])
            candidates.append((root_real, depth))

        if not candidates:
            raise MountCheckError("SOURCE_OUTSIDE_ROOTS", {
                "source": source_real,
                "allowed_source_roots": [
                    self.resolve(root) for root in self.allowed_source_roots
                ],
            })

        candidates.sort(key=lambda item: len(item[0]), reverse=True)
        return candidates[0]

    def _evaluate(self, source: str, target: str, *, recursive: bool,
                  read_only: bool, overmount: bool,
                  allow_create: bool) -> dict:
        if not self.bind_supported:
            raise MountCheckError("BIND_UNSUPPORTED",
                                  {"bind_supported": False})

        source_literal = os.fspath(source)
        target_literal = os.fspath(target)
        source_real = self.resolve(source_literal)
        target_real = self.resolve(target_literal)

        try:
            source_stat = os.stat(source_real)
        except OSError as exc:
            raise MountCheckError("SOURCE_MISSING",
                                  {"source": source_real}) from exc
        if not stat_module.S_ISDIR(source_stat.st_mode):
            raise MountCheckError("SOURCE_MISSING", {
                "source": source_real,
                "reason": "not_a_directory",
            })

        allowed_root, depth = self._allowed_root_and_depth(source_real)

        try:
            target_stat = os.stat(target_real)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise MountCheckError("TARGET_INVALID", {
                "target": target_real,
                "error": repr(exc),
            }) from exc

        target_exists = target_stat is not None
        would_create = not target_exists

        if target_stat is not None and not stat_module.S_ISDIR(
                target_stat.st_mode):
            raise MountCheckError("TARGET_NOT_DIRECTORY",
                                  {"target": target_real})

        if would_create and not allow_create:
            raise MountCheckError("TARGET_MISSING",
                                  {"target": target_real})

        if target_stat is not None and (
                source_stat.st_dev, source_stat.st_ino
        ) == (target_stat.st_dev, target_stat.st_ino):
            raise MountCheckError("SELF_BIND", {
                "source": source_real,
                "target": target_real,
                "dev": source_stat.st_dev,
                "ino": source_stat.st_ino,
            })

        with self._lock:
            top = self._top_record_locked(target_real)

        mounted_here = top is not None
        if mounted_here and not overmount:
            raise MountCheckError("OVERMOUNT_REQUIRED", {
                "target": target_real,
                "covered_source": top.get("source"),
            })

        if recursive and depth > self.max_depth:
            raise MountCheckError("DEPTH_EXCEEDED", {
                "source": source_real,
                "allowed_root": allowed_root,
                "depth": depth,
                "max_depth": self.max_depth,
            })

        shared_info = self._shared_mount_info(target_real)
        receipt = {
            "source": source_real,
            "target": target_real,
            "source_dev": source_stat.st_dev,
            "source_ino": source_stat.st_ino,
            "target_dev": target_stat.st_dev if target_stat else None,
            "target_ino": target_stat.st_ino if target_stat else None,
            "target_exists": target_exists,
            "checked_at": self._now(),
        }

        return {
            "ok": True,
            "code": "OK",
            "source_literal": source_literal,
            "target_literal": target_literal,
            "source": source_real,
            "target": target_real,
            "resolved_source": source_real,
            "resolved_target": target_real,
            "allowed_root": allowed_root,
            "depth": depth,
            "recursive": bool(recursive),
            "read_only": bool(read_only),
            "read_write": not read_only,
            "overmount": mounted_here and bool(overmount),
            "overmount_requested": bool(overmount),
            "mounted": mounted_here,
            "allow_create": bool(allow_create),
            "would_create": would_create,
            "created_target": False,
            "covered_source": top.get("source")
            if mounted_here and overmount else None,
            "shared_mount": shared_info is not None,
            "shared_mount_info": shared_info,
            "receipt": receipt,
        }

    @staticmethod
    def _require_receipt_fields(receipt: dict) -> None:
        required = ("source_dev", "source_ino", "target_dev", "target_ino")
        missing = [key for key in required if key not in receipt]
        if missing:
            raise MountCheckError("RECEIPT_INVALID", {"missing": missing})

    @classmethod
    def _check_target_receipt(cls, receipt: dict,
                              current_receipt: dict) -> None:
        cls._require_receipt_fields(receipt)
        target_fields = ("target_dev", "target_ino")
        identity_changed = tuple(
            receipt.get(key) for key in target_fields
        ) != tuple(current_receipt.get(key) for key in target_fields)
        path_changed = (
            "target" in receipt
            and receipt.get("target") != current_receipt.get("target")
        )
        if identity_changed or path_changed:
            raise MountCheckError("TARGET_CHANGED", {
                "target": current_receipt.get("target"),
                "receipt": {
                    "target": receipt.get("target"),
                    **{key: receipt.get(key) for key in target_fields},
                },
                "current": {
                    "target": current_receipt.get("target"),
                    **{key: current_receipt.get(key) for key in target_fields},
                },
            })

    @staticmethod
    def _source_identity_changed(receipt: dict,
                                 current_receipt: dict) -> bool:
        source_fields = ("source_dev", "source_ino")
        return tuple(receipt.get(key) for key in source_fields) != tuple(
            current_receipt.get(key) for key in source_fields
        )

    def _top_record_locked(self, target_key: str):
        index = self._last_index_locked(target_key)
        if index is None:
            return None
        return self._mounts[index]

    def _last_index_locked(self, target_key: str) -> int | None:
        for index in range(len(self._mounts) - 1, -1, -1):
            if self._mounts[index].get("target") == target_key:
                return index
        return None

    def _build_record(self, evaluation: dict, *, target_key: str,
                      created_target: bool, actual_overmount: bool,
                      covered_source: str | None,
                      source_revalidated: bool,
                      backend_info: dict | None) -> dict[str, Any]:
        receipt = evaluation["receipt"]
        now = self._now()
        return {
            "source_literal": evaluation["source_literal"],
            "target_literal": evaluation["target_literal"],
            "source": evaluation["source"],
            "target": target_key,
            "resolved_source": evaluation["resolved_source"],
            "resolved_target": target_key,
            "source_dev": receipt["source_dev"],
            "source_ino": receipt["source_ino"],
            "target_dev": receipt["target_dev"],
            "target_ino": receipt["target_ino"],
            "recursive": evaluation["recursive"],
            "read_only": evaluation["read_only"],
            "read_write": evaluation["read_write"],
            "overmount": actual_overmount,
            "covered_source": covered_source,
            "created_target": created_target,
            "source_revalidated": source_revalidated,
            "mounted": True,
            "bind": True,
            "status": "mounted",
            "shared_mount": evaluation["shared_mount"],
            "backend": backend_info or {"backend": "in-memory"},
            "applied_at": now,
            "timestamp": now,
            "mounted_at": now,
            "created": created_target,
            "receipt": deepcopy(receipt),
        }

    def _perform_bind(self, source: str, target: str,
                      options: dict) -> dict[str, Any]:
        return {
            "backend": "bookkeeping",
            "source": source,
            "target": target,
            "options": dict(options),
        }

    def _perform_unbind(self, record: dict) -> dict[str, Any]:
        return {"backend": "bookkeeping", "target": record.get("target")}

    def _remove_created_target(self, target_key: str) -> None:
        try:
            os.rmdir(target_key)
        except FileNotFoundError:
            return
        except OSError:
            try:
                if os.path.isdir(target_key) and not os.listdir(target_key):
                    os.rmdir(target_key)
            except OSError:
                pass

    def _rollback_failed_apply(self, created_path: str | None,
                               reserved: bool,
                               backend_completed: bool) -> None:
        if backend_completed:
            try:
                self._rollback_bind()
            except Exception:
                pass
        if created_path is not None:
            self._remove_created_target(created_path)
        if reserved:
            with self._lock:
                self._pending_capacity = max(
                    0, self._pending_capacity - 1
                )

    def _rollback_bind(self) -> None:
        return None

    @staticmethod
    def _unescape_mountinfo_path(value: str) -> str:
        return re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            value,
        )

    def _read_mountinfo(self) -> list[dict[str, Any]]:
        try:
            with open(self._mountinfo_path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return []

        entries = []
        for line in lines:
            fields = line.split()
            if len(fields) < 7:
                continue
            optional = []
            index = 6
            while index < len(fields) and fields[index] != "-":
                optional.append(fields[index])
                index += 1
            entries.append({
                "mount_id": fields[0],
                "parent_id": fields[1],
                "device": fields[2],
                "root": self._unescape_mountinfo_path(fields[3]),
                "mountpoint": self.resolve(
                    self._unescape_mountinfo_path(fields[4])
                ),
                "options": fields[5],
                "optional_fields": optional,
                "shared": next((item for item in optional
                                if item.startswith("shared:")), None),
            })
        return entries

    def _shared_mount_info(self, target_real: str):
        target_real = self.resolve(target_real)
        for entry in self._read_mountinfo():
            if entry.get("mountpoint") == target_real and entry.get("shared"):
                return deepcopy(entry)
        return None

    def _is_shared_mount(self, target: str) -> bool:
        return self._shared_mount_info(target) is not None

    def _parse_state(self, state: object):
        if not isinstance(state, dict):
            raise MountCheckError("STATE_INVALID",
                                  {"reason": "state_not_object"})
        version = state.get("version")
        if isinstance(version, bool) or version != _STATE_VERSION:
            raise MountCheckError("STATE_INVALID", {"reason": "bad_version"})

        mounts = state.get("mounts")
        stats = state.get("stats")
        if not isinstance(mounts, list):
            raise MountCheckError("STATE_INVALID", {"field": "mounts"})
        if not isinstance(stats, dict):
            raise MountCheckError("STATE_INVALID", {"field": "stats"})

        normalized_stats: dict[str, int] = {}
        for key in _STAT_KEYS:
            value = stats.get(key)
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise MountCheckError(
                    "STATE_INVALID", {"field": f"stats.{key}"}
                )
            normalized_stats[key] = value

        normalized_mounts = []
        for index, record in enumerate(mounts):
            if not isinstance(record, dict):
                raise MountCheckError("STATE_INVALID",
                                      {"field": f"mounts[{index}]"})
            source = record.get("source")
            target = record.get("target")
            if not isinstance(source, str) or not isinstance(target, str):
                raise MountCheckError("STATE_INVALID", {
                    "field": f"mounts[{index}].source_or_target",
                })
            normalized_mounts.append(deepcopy(record))

        if len(normalized_mounts) > self.max_mounts:
            raise MountCheckError("STATE_INVALID", {
                "reason": "mounts_exceed_capacity",
                "mount_count": len(normalized_mounts),
                "max_mounts": self.max_mounts,
            })

        return normalized_mounts, normalized_stats
