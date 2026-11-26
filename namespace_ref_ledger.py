"""Namespace fd ledger: per-process refs, auditing, leak detection, reclaim."""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone


class NsLedgerError(ValueError):
    """Raised when a ledger operation is refused."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _sort_key(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, type(value).__name__, repr(value))


def _holder_key(key):
    pid, starttime = key
    return (_sort_key(pid), _sort_key(starttime))


def _inode_key(value):
    return _sort_key(value)


class NamespaceRefLedger:
    """Track namespace holders by inode, audit against observations, reclaim."""

    _SNAPSHOT_VERSION = 1
    _UNREADABLE_STATES = {
        "d",
        "uninterruptible",
        "unreadable",
        "unknown",
        "missing",
        "lost",
    }

    def __init__(self, *, max_namespaces=32, max_holders=8, clock=None):
        self.max_namespaces = max_namespaces
        self.max_holders = max_holders
        self._clock = clock
        self._lock = threading.RLock()
        self._records = {}
        self._reclaimed = {}
        self._audit_serial = 0
        self._audits = 0
        self._reclaimed_total = 0

    def _now(self):
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _new_record(self, inode, generation):
        return {
            "inode": inode,
            "generation": generation,
            "generation_floor": None,
            "max_starttime": None,
            "holders": {},
            "stale": {},
            "paths": set(),
            "mounts": set(),
            "absent_audits": 0,
            "last_pid": None,
        }

    def _generation_for(self, inode):
        tombstone = self._reclaimed.get(inode)
        if tombstone is None:
            return 1
        return int(tombstone.get("generation") or 0) + 1

    @staticmethod
    def _remember_starttime(record, starttime):
        current = record.get("max_starttime")
        if current is None or _sort_key(starttime) > _sort_key(current):
            record["max_starttime"] = starttime

    @staticmethod
    def _after_generation_floor(starttime, floor):
        if floor is None:
            return True
        return _sort_key(starttime) > _sort_key(floor)

    def _reject(self, code, detail=None):
        raise NsLedgerError(code, detail)

    def _check_namespace_capacity(self, inode):
        if inode not in self._records and len(self._records) >= self.max_namespaces:
            self._reject("NS_LIMIT", {
                "inode": inode,
                "namespaces": len(self._records),
                "max_namespaces": self.max_namespaces,
            })

    def _check_holder_capacity(self, record, key):
        if key in record["holders"]:
            return
        if len(record["holders"]) >= self.max_holders:
            self._reject("HOLDER_LIMIT", {
                "inode": record["inode"],
                "pid": key[0],
                "starttime": key[1],
                "holders": len(record["holders"]),
                "max_holders": self.max_holders,
            })

    def _holder_list(self, record):
        return [
            {
                "pid": holder["pid"],
                "starttime": holder["starttime"],
                "mode": holder["mode"],
            }
            for _, holder in sorted(record["holders"].items(),
                                    key=lambda item: _holder_key(item[0]))
        ]

    def track(self, inode, pid, *, starttime, mode="fd", path=None):
        with self._lock:
            record = self._records.get(inode)
            key = (pid, starttime)
            if record is None:
                self._check_namespace_capacity(inode)
                if self.max_holders <= 0:
                    self._reject("HOLDER_LIMIT", {
                        "inode": inode,
                        "pid": pid,
                        "starttime": starttime,
                        "holders": 0,
                        "max_holders": self.max_holders,
                    })
                generation = self._generation_for(inode)
                tombstone = self._reclaimed.pop(inode, None)
                record = self._new_record(inode, generation)
                if tombstone is not None:
                    floor = tombstone.get("last_starttime")
                    record["generation_floor"] = floor
                    record["max_starttime"] = floor
                self._records[inode] = record
            else:
                self._check_holder_capacity(record, key)

            record["stale"].pop(key, None)
            record["holders"][key] = {
                "pid": pid,
                "starttime": starttime,
                "mode": mode,
            }
            self._remember_starttime(record, starttime)
            record["last_pid"] = pid
            record["absent_audits"] = 0
            if path is not None:
                record["paths"].add(path)

            return {
                "inode": inode,
                "pid": pid,
                "starttime": starttime,
                "holders": len(record["holders"]),
                "paths": sorted(record["paths"], key=_sort_key),
            }

    def release(self, inode, pid, *, starttime=None):
        with self._lock:
            record = self._records.get(inode)
            if record is None:
                return 0

            removed = False
            for key in list(record["holders"]):
                if key[0] != pid:
                    continue
                if starttime is not None and key[1] != starttime:
                    continue
                record["holders"].pop(key, None)
                record["last_pid"] = pid
                removed = True

            for key in list(record["stale"]):
                if key[0] != pid:
                    continue
                if starttime is not None and key[1] != starttime:
                    continue
                record["stale"].pop(key, None)

            if removed:
                record["absent_audits"] = 0
            return len(record["holders"])

    def _normalize_observed(self, observed):
        if observed is None:
            return []

        entries = []

        def add_entry(entry, inode=None):
            if not isinstance(entry, dict):
                entry = {"pid": entry}
            else:
                entry = dict(entry)
            if inode is not None:
                entry.setdefault("inode", inode)

            nested = entry.get("holders")
            if isinstance(nested, (list, tuple, set)):
                base_inode = entry.get("inode", inode)
                for item in nested:
                    child = dict(item) if isinstance(item, dict) else {"pid": item}
                    if base_inode is not None:
                        child.setdefault("inode", base_inode)
                    if "readable" in entry:
                        child.setdefault("readable", entry["readable"])
                    entries.append(child)
                return
            entries.append(entry)

        if isinstance(observed, dict):
            if "inode" in observed or "pid" in observed or "holders" in observed:
                add_entry(observed)
            else:
                for inode, value in observed.items():
                    if isinstance(value, dict):
                        add_entry(value, inode)
                    elif isinstance(value, (list, tuple, set)):
                        for item in value:
                            add_entry(item, inode)
                    else:
                        add_entry({"inode": inode, "pid": value})
        else:
            for item in observed:
                add_entry(item)
        return entries

    @staticmethod
    def _observation_pid(entry):
        if "pid" in entry:
            return entry.get("pid")
        process = entry.get("process")
        if isinstance(process, dict):
            return process.get("pid")
        return None

    @staticmethod
    def _observation_starttime(entry):
        for key in ("starttime", "start_time", "proc_starttime"):
            if key in entry:
                return entry.get(key)
        process = entry.get("process")
        if isinstance(process, dict):
            for key in ("starttime", "start_time", "proc_starttime"):
                if key in process:
                    return process.get(key)
        return None

    @staticmethod
    def _observation_generation(entry):
        if "generation" in entry:
            return entry.get("generation")
        process = entry.get("process")
        if isinstance(process, dict):
            return process.get("generation")
        return None

    def _observation_readable(self, entry):
        for key in ("readable", "process_readable", "proc_readable",
                    "accessible"):
            if key in entry and entry.get(key) is False:
                return False
        if entry.get("unreadable") or entry.get("unknown"):
            return False
        if entry.get("error") is not None:
            return False

        status = entry.get("status", entry.get("proc_state"))
        if status is None:
            status = entry.get("state")
        if isinstance(status, str):
            normalized = status.strip().lower()
            if normalized in self._UNREADABLE_STATES:
                return False

        if self._observation_pid(entry) is None:
            return False
        if self._observation_starttime(entry) is None:
            return False
        return True

    @staticmethod
    def _source_values(value):
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    def _transfer_sources(self, entry):
        sources = []
        for field in ("from_pid", "source_pid", "old_pid", "previous_pid",
                      "transferred_from", "parent_pid", "origin_pid"):
            sources.extend(self._source_values(entry.get(field)))

        for field in ("chain", "transfer_chain", "pids", "pid_chain",
                      "path_chain"):
            sources.extend(self._source_values(entry.get(field)))

        own_pid = self._observation_pid(entry)
        own_starttime = self._observation_starttime(entry)
        result = []
        for source in sources:
            if isinstance(source, dict):
                pid = self._observation_pid(source)
                starttime = self._observation_starttime(source)
            else:
                pid = source
                starttime = None
            if pid is None:
                continue
            if pid == own_pid and (starttime is None or starttime == own_starttime):
                continue
            result.append((pid, starttime))
        return result

    @staticmethod
    def _match_transfer_source(candidates, pid, starttime):
        exact = None
        same_pid = []
        for key in candidates:
            if key[0] != pid:
                continue
            if starttime is not None and key[1] == starttime:
                exact = key
                break
            same_pid.append(key)
        if exact is not None:
            return exact
        if len(same_pid) == 1:
            return same_pid[0]
        return None

    def _audit_record(self, record, observations, observed_paths, serial):
        active = record["holders"]
        stale = record["stale"]
        previous_stale = set(stale)
        observed_keys = set(observations)
        stale_out = []
        unmanaged_out = []

        for key in list(stale):
            if key in observed_keys and key not in active:
                holder = stale.pop(key)
                active[key] = {
                    "pid": holder["pid"],
                    "starttime": holder["starttime"],
                    "mode": holder["mode"],
                }
                self._remember_starttime(record, holder["starttime"])
                record["last_pid"] = holder["pid"]

        missing = [key for key in active if key not in observed_keys]
        new = [key for key in observed_keys if key not in active]
        available_stale = [key for key in previous_stale if key in stale]

        transfers = {}
        used_sources = set()
        for new_key in new:
            for pid, starttime in self._transfer_sources(observations[new_key]):
                source = self._match_transfer_source(
                    [key for key in missing if key not in used_sources],
                    pid, starttime)
                if source is None:
                    source = self._match_transfer_source(
                        [key for key in available_stale
                         if key not in used_sources],
                        pid, starttime)
                if source is not None:
                    transfers[new_key] = source
                    used_sources.add(source)
                    break

        if not transfers:
            if len(missing) == 1 and len(new) == 1:
                transfers[new[0]] = missing[0]
                used_sources.add(missing[0])
            elif (not missing and len(new) == 1
                  and len(available_stale) == 1):
                transfers[new[0]] = available_stale[0]
                used_sources.add(available_stale[0])

        for new_key, old_key in transfers.items():
            old_holder = active.get(old_key)
            if old_holder is None:
                old_holder = stale.pop(old_key, None)
            else:
                active.pop(old_key, None)
            if old_holder is None:
                continue
            if old_key in missing:
                stale[old_key] = {
                    "pid": old_holder["pid"],
                    "starttime": old_holder["starttime"],
                    "mode": old_holder["mode"],
                    "stale_audit": serial,
                }
                stale_out.append({
                    "inode": record["inode"],
                    "pid": old_holder["pid"],
                })
            active[new_key] = {
                "pid": new_key[0],
                "starttime": new_key[1],
                "mode": old_holder["mode"],
            }
            self._remember_starttime(record, new_key[1])
            record["last_pid"] = new_key[0]

        if not transfers and not new and len(missing) == 1:
            observed_unmanaged = [
                key for key in observed_keys
                if key in active and active[key]["mode"] == "unmanaged"
            ]
            if len(observed_unmanaged) == 1:
                old_key = missing[0]
                old_holder = active.pop(old_key)
                inherit_key = observed_unmanaged[0]
                active[inherit_key]["mode"] = old_holder["mode"]
                stale[old_key] = {
                    "pid": old_holder["pid"],
                    "starttime": old_holder["starttime"],
                    "mode": old_holder["mode"],
                    "stale_audit": serial,
                }
                used_sources.add(old_key)
                record["last_pid"] = inherit_key[0]
                stale_out.append({
                    "inode": record["inode"],
                    "pid": old_holder["pid"],
                })

        for key in missing:
            if key in used_sources or key not in active:
                continue
            holder = active.pop(key)
            stale[key] = {
                "pid": holder["pid"],
                "starttime": holder["starttime"],
                "mode": holder["mode"],
                "stale_audit": serial,
            }
            record["last_pid"] = holder["pid"]
            stale_out.append({"inode": record["inode"], "pid": holder["pid"]})

        for key in new:
            if key in transfers or key in active:
                continue
            active[key] = {
                "pid": key[0],
                "starttime": key[1],
                "mode": "unmanaged",
            }
            self._remember_starttime(record, key[1])
            record["last_pid"] = key[0]
            unmanaged_out.append({"inode": record["inode"], "pid": key[0]})

        for key in previous_stale:
            stale.pop(key, None)

        record["paths"].update(observed_paths)
        if not active and not record["mounts"] and not observed_keys:
            record["absent_audits"] = int(record.get("absent_audits") or 0) + 1
        else:
            record["absent_audits"] = 0

        return stale_out, unmanaged_out

    def audit(self, observed):
        entries = self._normalize_observed(observed)
        with self._lock:
            checked = len(entries)
            grouped = {}
            uncertain = set()
            global_uncertain = False

            for entry in entries:
                inode = entry.get("inode")
                if not self._observation_readable(entry):
                    if inode is None:
                        global_uncertain = True
                    else:
                        uncertain.add(inode)
                    continue
                if inode is None:
                    continue
                key = (self._observation_pid(entry),
                       self._observation_starttime(entry))
                grouped.setdefault(inode, {})[key] = entry

            reclaimable = sum(
                1 for record in self._records.values()
                if not record["holders"] and not record["mounts"]
            )
            if global_uncertain:
                self._audits += 1
                return {
                    "checked": checked,
                    "stale": [],
                    "unmanaged": [],
                    "reclaimable": reclaimable,
                }

            working = copy.deepcopy(self._records)
            working_reclaimed = copy.deepcopy(self._reclaimed)
            filtered = {}
            filtered_paths = {}
            new_inodes = []

            for inode, observations in grouped.items():
                if inode in uncertain:
                    continue
                record = working.get(inode)
                if record is not None:
                    generation = int(record.get("generation") or 1)
                    floor = record.get("generation_floor")
                    holders = record["holders"]
                    stale = record["stale"]
                elif inode in working_reclaimed:
                    tombstone = working_reclaimed[inode]
                    generation = int(tombstone.get("generation") or 1) + 1
                    floor = tombstone.get("last_starttime")
                    holders = {}
                    stale = {}
                    record = None
                else:
                    generation = None
                    floor = None
                    holders = {}
                    stale = {}
                    record = None

                current = {}
                for key, entry in observations.items():
                    observed_generation = self._observation_generation(entry)
                    if (generation is not None
                            and observed_generation is not None):
                        try:
                            if int(observed_generation) < generation:
                                continue
                        except (TypeError, ValueError):
                            pass
                    if (floor is not None and key not in holders
                            and key not in stale
                            and not self._after_generation_floor(
                                key[1], floor)):
                        continue
                    current[key] = entry
                if current:
                    filtered[inode] = current
                    if record is None:
                        record = self._new_record(
                            inode, 1 if generation is None else generation)
                        record["generation_floor"] = floor
                        record["max_starttime"] = floor
                        working[inode] = record
                        working_reclaimed.pop(inode, None)
                        new_inodes.append(inode)
                    for entry in current.values():
                        path = entry.get("path")
                        if path is not None:
                            filtered_paths.setdefault(inode, set()).add(path)

            if len(working) > self.max_namespaces:
                self._reject("NS_LIMIT", {
                    "namespaces": len(working),
                    "incoming": len(new_inodes),
                    "max_namespaces": self.max_namespaces,
                })

            serial = self._audit_serial + 1
            stale_out = []
            unmanaged_out = []
            for inode, record in working.items():
                if inode in uncertain:
                    continue
                observations = filtered.get(inode)
                if observations is None and inode in grouped:
                    continue
                observations = observations or {}
                paths = filtered_paths.get(inode, set())
                record_stale, record_unmanaged = self._audit_record(
                    record, observations, paths, serial)
                stale_out.extend(record_stale)
                unmanaged_out.extend(record_unmanaged)

            for record in working.values():
                if len(record["holders"]) > self.max_holders:
                    self._reject("HOLDER_LIMIT", {
                        "inode": record["inode"],
                        "holders": len(record["holders"]),
                        "max_holders": self.max_holders,
                    })

            self._records = working
            self._reclaimed = working_reclaimed
            self._audit_serial = serial
            self._audits += 1

            stale_out.sort(key=lambda item: (
                _inode_key(item["inode"]), _sort_key(item["pid"])))
            unmanaged_out.sort(key=lambda item: (
                _inode_key(item["inode"]), _sort_key(item["pid"])))
            reclaimable = sum(
                1 for record in self._records.values()
                if not record["holders"] and not record["mounts"]
            )
            return {
                "checked": checked,
                "stale": stale_out,
                "unmanaged": unmanaged_out,
                "reclaimable": reclaimable,
            }

    def leaks(self):
        with self._lock:
            result = [
                inode for inode, record in self._records.items()
                if int(record.get("absent_audits") or 0) >= 2
                and not record["holders"]
                and not record["mounts"]
            ]
            return sorted(result, key=_inode_key)

    def reclaim(self, *, now=None):
        with self._lock:
            timestamp = self._now() if now is None else now
            eligible = [
                (inode, record) for inode, record in self._records.items()
                if int(record.get("absent_audits") or 0) >= 2
                and not record["holders"]
                and not record["mounts"]
            ]
            eligible.sort(key=lambda item: _inode_key(item[0]))

            output = []
            for inode, record in eligible:
                self._reclaimed[inode] = {
                    "inode": inode,
                    "generation": int(record.get("generation") or 1),
                    "last_pid": record.get("last_pid"),
                    "last_starttime": record.get("max_starttime"),
                    "at": timestamp,
                }
                self._records.pop(inode, None)
                self._reclaimed_total += 1
                output.append({
                    "inode": inode,
                    "last_pid": record.get("last_pid"),
                    "at": timestamp,
                })
            return output

    def holders(self, inode):
        with self._lock:
            record = self._records.get(inode)
            if record is None:
                return []
            return self._holder_list(record)

    def refcount(self, inode):
        with self._lock:
            record = self._records.get(inode)
            return 0 if record is None else len(record["holders"])

    def state(self, inode):
        with self._lock:
            record = self._records.get(inode)
            if record is None:
                return "reclaimed" if inode in self._reclaimed else "unknown"
            if record["mounts"]:
                return "mounted"
            if record["holders"]:
                return "active"
            return "reclaimable"

    def bind_mount(self, inode, mountpoint):
        with self._lock:
            record = self._records.get(inode)
            if record is None:
                self._check_namespace_capacity(inode)
                generation = self._generation_for(inode)
                tombstone = self._reclaimed.pop(inode, None)
                record = self._new_record(inode, generation)
                if tombstone is not None:
                    floor = tombstone.get("last_starttime")
                    record["generation_floor"] = floor
                    record["max_starttime"] = floor
                self._records[inode] = record

            for other_inode, other_record in self._records.items():
                if other_inode == inode:
                    continue
                other_record["mounts"].discard(mountpoint)

            record["mounts"].add(mountpoint)
            record["absent_audits"] = 0
            return len(record["mounts"])

    def unmount(self, mountpoint):
        with self._lock:
            remaining = 0
            for record in self._records.values():
                if mountpoint in record["mounts"]:
                    record["mounts"].discard(mountpoint)
                    remaining = len(record["mounts"])
            return remaining

    def paths(self, inode):
        with self._lock:
            record = self._records.get(inode)
            if record is None:
                return []
            return sorted(record["paths"], key=_sort_key)

    def _serialize_record(self, record):
        return {
            "inode": record["inode"],
            "generation": int(record.get("generation") or 1),
            "generation_floor": record.get("generation_floor"),
            "max_starttime": record.get("max_starttime"),
            "holders": [
                {
                    "pid": holder["pid"],
                    "starttime": holder["starttime"],
                    "mode": holder["mode"],
                }
                for _, holder in sorted(record["holders"].items(),
                                        key=lambda item: _holder_key(item[0]))
            ],
            "stale": [
                {
                    "pid": holder["pid"],
                    "starttime": holder["starttime"],
                    "mode": holder["mode"],
                }
                for _, holder in sorted(record["stale"].items(),
                                        key=lambda item: _holder_key(item[0]))
            ],
            "paths": sorted(record["paths"], key=_sort_key),
            "mounts": sorted(record["mounts"], key=_sort_key),
            "absent_audits": int(record.get("absent_audits") or 0),
            "last_pid": record.get("last_pid"),
        }

    def snapshot(self):
        with self._lock:
            namespaces = [
                self._serialize_record(record)
                for _, record in sorted(self._records.items(),
                                        key=lambda item: _inode_key(item[0]))
            ]
            reclaimed = [
                copy.deepcopy(self._reclaimed[inode])
                for inode in sorted(self._reclaimed, key=_inode_key)
            ]
            return {
                "version": self._SNAPSHOT_VERSION,
                "namespaces": namespaces,
                "reclaimed": reclaimed,
                "audit_serial": self._audit_serial,
                "audits": self._audits,
                "reclaimed_total": self._reclaimed_total,
            }

    def _restore_holder(self, value):
        if not isinstance(value, dict):
            self._reject("SNAPSHOT_INVALID", {"reason": "holder"})
        if "pid" not in value or "starttime" not in value:
            self._reject("SNAPSHOT_INVALID", {"reason": "holder_fields"})
        return {
            "pid": value["pid"],
            "starttime": value["starttime"],
            "mode": value.get("mode", "fd"),
        }

    def restore(self, snapshot):
        with self._lock:
            if not isinstance(snapshot, dict):
                self._reject("SNAPSHOT_INVALID", {"reason": "snapshot"})
            version = snapshot.get("version", self._SNAPSHOT_VERSION)
            if version != self._SNAPSHOT_VERSION:
                self._reject("SNAPSHOT_INVALID", {"reason": "version"})
            namespaces = snapshot.get("namespaces")
            if not isinstance(namespaces, (list, tuple)):
                self._reject("SNAPSHOT_INVALID", {"reason": "namespaces"})

            records = {}
            try:
                for item in namespaces:
                    if not isinstance(item, dict) or "inode" not in item:
                        self._reject("SNAPSHOT_INVALID", {"reason": "record"})
                    inode = item["inode"]
                    if inode in records:
                        self._reject("SNAPSHOT_INVALID",
                                     {"reason": "duplicate_inode"})
                    record = self._new_record(
                        inode, int(item.get("generation") or 1))
                    record["generation_floor"] = item.get("generation_floor")
                    record["max_starttime"] = item.get("max_starttime")
                    for holder_value in item.get("holders", []):
                        holder = self._restore_holder(holder_value)
                        record["holders"][(holder["pid"],
                                           holder["starttime"])] = holder
                        self._remember_starttime(record, holder["starttime"])
                    for holder_value in item.get("stale", []):
                        holder = self._restore_holder(holder_value)
                        holder["stale_audit"] = self._audit_serial
                        record["stale"][(holder["pid"],
                                         holder["starttime"])] = holder
                        self._remember_starttime(record, holder["starttime"])
                    record["paths"] = set(item.get("paths", []))
                    record["mounts"] = set(item.get("mounts", []))
                    record["absent_audits"] = int(
                        item.get("absent_audits") or 0)
                    record["last_pid"] = item.get("last_pid")
                    if len(record["holders"]) > self.max_holders:
                        self._reject("HOLDER_LIMIT", {
                            "inode": inode,
                            "holders": len(record["holders"]),
                            "max_holders": self.max_holders,
                        })
                    records[inode] = record
            except NsLedgerError:
                raise
            except (TypeError, ValueError):
                self._reject("SNAPSHOT_INVALID", {"reason": "record_fields"})

            if len(records) > self.max_namespaces:
                self._reject("NS_LIMIT", {
                    "namespaces": len(records),
                    "max_namespaces": self.max_namespaces,
                })

            reclaimed_values = snapshot.get("reclaimed", [])
            if not isinstance(reclaimed_values, (list, tuple)):
                self._reject("SNAPSHOT_INVALID", {"reason": "reclaimed"})
            reclaimed = {}
            try:
                for value in reclaimed_values:
                    if not isinstance(value, dict) or "inode" not in value:
                        self._reject("SNAPSHOT_INVALID",
                                     {"reason": "reclaimed"})
                    inode = value["inode"]
                    reclaimed[inode] = copy.deepcopy(value)
                    reclaimed[inode]["generation"] = int(
                        value.get("generation") or 1)

                audit_serial = int(snapshot.get("audit_serial") or 0)
                audits = int(snapshot.get("audits") or 0)
                reclaimed_total = int(
                    snapshot.get("reclaimed_total") or 0)
            except NsLedgerError:
                raise
            except (TypeError, ValueError):
                self._reject("SNAPSHOT_INVALID",
                             {"reason": "snapshot_fields"})

            self._records = records
            self._reclaimed = reclaimed
            self._audit_serial = audit_serial
            self._audits = audits
            self._reclaimed_total = reclaimed_total

    def stats(self):
        with self._lock:
            records = list(self._records.values())
            holders = sum(len(record["holders"]) for record in records)
            stale = sum(len(record["stale"]) for record in records)
            unmanaged = sum(
                1 for record in records
                for holder in record["holders"].values()
                if holder["mode"] == "unmanaged"
            )
            reclaimable = sum(
                1 for record in records
                if not record["holders"] and not record["mounts"]
            )
            mounts = sum(len(record["mounts"]) for record in records)
            return {
                "namespaces": len(records),
                "holders": holders,
                "stale": stale,
                "unmanaged": unmanaged,
                "reclaimable": reclaimable,
                "reclaimed": self._reclaimed_total,
                "mounts": mounts,
                "audits": self._audits,
            }
