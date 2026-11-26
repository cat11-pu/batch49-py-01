"""Generation-scoped temp directory sweeper: freeze, lease, delete, quarantine."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class SweepError(ValueError):
    """Refusal raised by the sweeper."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Generation:
    gen: int
    label: object
    state: str
    created_at: int


@dataclass(frozen=True)
class DirEntry:
    path: str
    gen: int
    inode: int
    bytes: int


class TempGenerationSweeper:
    """Create temp dirs per job generation and delete frozen generations."""

    def __init__(self, *, root, max_dirs_per_gen: int = 64,
                 max_bytes: int = 1 << 20, quarantine_limit: int = 8,
                 clock=None) -> None:
        self.root = root
        self.max_dirs_per_gen = max_dirs_per_gen
        self.max_bytes = max_bytes
        self.quarantine_limit = quarantine_limit
        self._clock = clock
        self._lock = threading.RLock()
        self._generations: dict = {}   # gen -> {"gen", "label", "state", "created_at"}
        self._entries: dict = {}       # path -> DirEntry (live, registered dirs)
        self._leases: dict = {}        # path -> set of lease tokens
        self._quarantine: dict = {}    # path -> {"path", "gen", "inode", "code", "attempts"}
        self._faults: dict = {}        # path -> {"code", "times"}
        self._swept: dict = {}         # gen -> set of retired paths (deleted/quarantined)
        self._next_gen = 0
        self._next_inode = 0
        self._counters = {"deleted": 0, "skipped": 0, "quarantined": 0,
                          "failed": 0, "delayed": 0}

    def _now(self, now):
        if now is None:
            return self._clock() if self._clock is not None else 0
        return now

    def _gen_root(self, gen):
        return f"{self.root}/g{gen}"

    def _active_gen(self):
        actives = [gen for gen, rec in self._generations.items()
                   if rec["state"] == "active"]
        return max(actives) if actives else 0

    def begin_generation(self, *, label=None, now=0):
        with self._lock:
            now = self._now(now)
            for rec in self._generations.values():
                if rec["state"] == "active":
                    rec["state"] = "frozen"
            self._next_gen += 1
            rec = {"gen": self._next_gen, "label": label,
                   "state": "active", "created_at": now}
            self._generations[self._next_gen] = rec
            return Generation(**rec)

    def current_generation(self):
        with self._lock:
            return self._active_gen()

    def freeze(self, gen):
        with self._lock:
            rec = self._generations.get(gen)
            if rec is None:
                raise SweepError("GEN_UNKNOWN", gen)
            rec["state"] = "frozen"
            return Generation(**rec)

    def create(self, path, *, bytes=0, inode=None):
        with self._lock:
            if bytes < 0:
                raise SweepError("INVALID_REQUEST",
                                 {"path": path, "bytes": bytes})
            active = self._active_gen()
            root = self._gen_root(active) if active else None
            if root is None or (path != root
                                and not path.startswith(root + "/")):
                raise SweepError("PATH_OUTSIDE", path)
            if path in self._entries or path in self._quarantine:
                raise SweepError("DIR_EXISTS", path)
            gen_entries = [entry for entry in self._entries.values()
                           if entry.gen == active]
            if len(gen_entries) >= self.max_dirs_per_gen:
                raise SweepError("DIR_LIMIT",
                                 {"gen": active, "limit": self.max_dirs_per_gen})
            used = sum(entry.bytes for entry in gen_entries)
            if used + bytes > self.max_bytes:
                raise SweepError("BYTE_LIMIT",
                                 {"gen": active, "limit": self.max_bytes})
            if inode is None:
                self._next_inode += 1
                inode = self._next_inode
            else:
                self._next_inode = max(self._next_inode, inode)
            entry = DirEntry(path=path, gen=active, inode=inode, bytes=bytes)
            self._entries[path] = entry
            return entry

    def acquire(self, path, *, lease):
        with self._lock:
            if not self._is_known(path):
                raise SweepError("PATH_UNKNOWN", path)
            held = self._leases.setdefault(path, set())
            if lease in held:
                raise SweepError("LEASE_EXISTS",
                                 {"path": path, "lease": lease})
            if not held and path not in self._entries:
                self._counters["delayed"] += 1
            held.add(lease)

    def release(self, path, *, lease):
        with self._lock:
            held = self._leases.get(path)
            if not held or lease not in held:
                raise SweepError("LEASE_UNKNOWN",
                                 {"path": path, "lease": lease})
            held.discard(lease)
            if not held:
                del self._leases[path]
                if path not in self._entries:
                    self._counters["delayed"] -= 1

    def _is_known(self, path):
        if path in self._entries or path in self._quarantine:
            return True
        return any(path in paths for paths in self._swept.values())

    def rename(self, path, new_path):
        with self._lock:
            entry = self._entries.get(path)
            if entry is None:
                raise SweepError("PATH_UNKNOWN", path)
            if new_path in self._entries or new_path in self._quarantine:
                raise SweepError("DIR_EXISTS", new_path)
            moved = DirEntry(path=new_path, gen=entry.gen,
                             inode=entry.inode, bytes=entry.bytes)
            del self._entries[path]
            self._entries[new_path] = moved
            if path in self._leases:
                self._leases[new_path] = self._leases.pop(path)
            if path in self._faults:
                self._faults[new_path] = self._faults.pop(path)
            return moved

    def _has_descendants(self, path):
        prefix = path + "/"
        for other in self._entries:
            if other.startswith(prefix):
                return True
        for other in self._quarantine:
            if other.startswith(prefix):
                return True
        return False

    def _consume_fault(self, path):
        fault = self._faults.get(path)
        if not fault or fault["times"] <= 0:
            return None
        fault["times"] -= 1
        if fault["times"] <= 0:
            del self._faults[path]
        return fault["code"]

    def _sweep_generation(self, gen):
        retired = self._swept.setdefault(gen, set())
        skipped = sorted(retired)
        deleted, quarantined, failed = [], [], []
        entries = [entry for entry in self._entries.values()
                   if entry.gen == gen]
        entries.sort(key=lambda entry: (-entry.path.count("/"), entry.path))
        for entry in entries:
            path = entry.path
            if path not in self._entries:
                continue
            if self._leases.get(path):
                skipped.append(path)
                continue
            if self._has_descendants(path):
                skipped.append(path)
                continue
            code = self._consume_fault(path)
            if code is None:
                del self._entries[path]
                retired.add(path)
                deleted.append(path)
            elif len(self._quarantine) >= self.quarantine_limit:
                failed.append(path)
            else:
                self._quarantine[path] = {"path": path, "gen": entry.gen,
                                          "inode": entry.inode, "code": code,
                                          "attempts": 1}
                del self._entries[path]
                retired.add(path)
                quarantined.append(path)
        skipped.sort()
        return deleted, skipped, quarantined, failed

    def sweep(self, *, gen=None, now=0):
        with self._lock:
            if gen is not None:
                rec = self._generations.get(gen)
                if rec is None:
                    raise SweepError("GEN_UNKNOWN", gen)
                if rec["state"] != "frozen":
                    raise SweepError("GEN_ACTIVE", gen)
                gens = [gen]
            else:
                gens = sorted(g for g, rec in self._generations.items()
                              if rec["state"] == "frozen")
            deleted, skipped, quarantined, failed = [], [], [], []
            for target in gens:
                batch = self._sweep_generation(target)
                deleted += batch[0]
                skipped += batch[1]
                quarantined += batch[2]
                failed += batch[3]
            self._counters["deleted"] += len(deleted)
            self._counters["skipped"] += len(skipped)
            self._counters["quarantined"] += len(quarantined)
            self._counters["failed"] += len(failed)
            if failed:
                raise SweepError("QUARANTINE_LIMIT", tuple(failed))
            return {"deleted": tuple(deleted), "skipped": tuple(skipped),
                    "quarantined": tuple(quarantined),
                    "failed": tuple(failed)}

    def dirs(self, *, gen=None):
        with self._lock:
            rows = [entry for entry in self._entries.values()
                    if gen is None or entry.gen == gen]
            rows.sort(key=lambda entry: entry.path)
            return rows

    def quarantine_report(self):
        with self._lock:
            return [dict(self._quarantine[path])
                    for path in sorted(self._quarantine)]

    def retry_quarantine(self, *, now=0):
        with self._lock:
            deleted = 0
            retained = 0
            for path in sorted(list(self._quarantine)):
                record = self._quarantine.get(path)
                if record is None:
                    continue
                code = self._consume_fault(path)
                if code is None:
                    del self._quarantine[path]
                    deleted += 1
                    self._counters["deleted"] += 1
                else:
                    record["attempts"] += 1
                    record["code"] = code
                    retained += 1
            return {"deleted": deleted, "retained": retained}

    def inject_fault(self, path, *, code="EACCES", times=1):
        with self._lock:
            self._faults[path] = {"code": code, "times": times}

    def snapshot(self):
        with self._lock:
            return {
                "root": self.root,
                "max_dirs_per_gen": self.max_dirs_per_gen,
                "max_bytes": self.max_bytes,
                "quarantine_limit": self.quarantine_limit,
                "generations": [dict(rec) for _, rec in
                                sorted(self._generations.items())],
                "entries": [{"path": entry.path, "gen": entry.gen,
                             "inode": entry.inode, "bytes": entry.bytes}
                            for entry in sorted(self._entries.values(),
                                                key=lambda entry: entry.path)],
                "leases": {path: sorted(leases) for path, leases in
                           sorted(self._leases.items())},
                "quarantine": [dict(self._quarantine[path])
                               for path in sorted(self._quarantine)],
                "faults": {path: dict(fault) for path, fault in
                           sorted(self._faults.items())},
                "swept": {str(gen): sorted(paths) for gen, paths in
                          sorted(self._swept.items())},
                "counters": dict(self._counters),
                "next_gen": self._next_gen,
                "next_inode": self._next_inode,
            }

    def restore(self, state):
        with self._lock:
            self._generations = {rec["gen"]: dict(rec)
                                 for rec in state.get("generations", [])}
            self._entries = {}
            for rec in state.get("entries", []):
                entry = DirEntry(path=rec["path"], gen=rec["gen"],
                                 inode=rec["inode"], bytes=rec["bytes"])
                self._entries[entry.path] = entry
            self._leases = {path: set(leases) for path, leases in
                            state.get("leases", {}).items()}
            self._quarantine = {rec["path"]: dict(rec)
                                for rec in state.get("quarantine", [])}
            self._faults = {path: dict(fault) for path, fault in
                            state.get("faults", {}).items()}
            self._swept = {int(gen): set(paths) for gen, paths in
                           state.get("swept", {}).items()}
            self._counters = {"deleted": 0, "skipped": 0, "quarantined": 0,
                              "failed": 0, "delayed": 0}
            self._counters.update(state.get("counters", {}))
            self._counters["delayed"] = sum(
                1 for path in self._leases if path not in self._entries)
            self._next_gen = state.get("next_gen",
                                       max(self._generations, default=0))
            self._next_inode = state.get(
                "next_inode",
                max((entry.inode for entry in self._entries.values()),
                    default=0))

    def stats(self):
        with self._lock:
            return dict(self._counters)
