"""Multi-view LOD arbitration for main, shadow and reflection passes."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class LodError(ValueError):
    """Rejection raised by the arbiter; ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ViewSpec:
    """Registered view and its arbitration weight."""

    view_id: str
    priority: int


@dataclass(frozen=True)
class LodDecision:
    """Outcome of one arbitrate() call for a single object."""

    object_id: str
    demanded: int
    resident: int
    switched: str
    deferred: bool


class MultiViewLodArbiter:
    """Merge per-view LOD demands into one resident level per object."""

    _SNAPSHOT_VERSION = 1
    _SWITCH_VALUES = frozenset(("none", "upgrade", "downgrade"))

    def __init__(self, *, max_views: int = 8, max_lod: int = 3,
                 frame_upgrade_budget: int = 2, cooldown_frames: int = 2,
                 hysteresis: int = 1) -> None:
        for name, value in (
            ("max_views", max_views),
            ("max_lod", max_lod),
            ("frame_upgrade_budget", frame_upgrade_budget),
            ("cooldown_frames", cooldown_frames),
            ("hysteresis", hysteresis),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        self.max_views = max_views
        self.max_lod = max_lod
        self.frame_upgrade_budget = frame_upgrade_budget
        self.cooldown_frames = cooldown_frames
        self.hysteresis = hysteresis

        self._lock = threading.RLock()

        # frame -> object_id -> view_id -> lod
        self._pending: dict[int, dict[str, dict[str, int]]] = {}
        self._views: dict[str, ViewSpec] = {}
        self._residents: dict[str, int] = {}
        self._known_objects: set[str] = set()
        self._object_views: dict[str, set[str]] = {}

        # Accounting is keyed by frame so no budget can leak across frames.
        self._upgrade_used: dict[int, int] = {}

        # A downgrade starts an upgrade cooldown.  A removed view separately
        # holds downgrades until the same cooldown interval has elapsed.
        self._cooldown_start: dict[str, int] = {}
        self._downgrade_hold_until: dict[str, int] = {}

        # (frame, object_id) -> decision.  This is the per-frame settlement
        # guard as well as an idempotency cache.
        self._decisions: dict[tuple[int, str], LodDecision] = {}
        self._decision_priority: dict[tuple[int, str], int] = {}

        self._upgrades = 0
        self._downgrades = 0
        self._deferred = 0
        self._requests = 0
        self._log: list[dict[str, object]] = []
        self._last_frame = 0

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_int(value: object) -> bool:
        return type(value) is int

    def _check_frame(self, frame: int) -> None:
        if not self._is_int(frame) or frame < 0:
            raise LodError("LOD_BAD_FRAME", frame)

    def _check_lod(self, lod: int) -> None:
        if not self._is_int(lod) or lod < 0 or lod > self.max_lod:
            raise LodError("LOD_LOD_RANGE", lod)

    def _resident_or_default(self, object_id: str) -> int:
        return self._residents.get(object_id, self.max_lod)

    @staticmethod
    def _sort_key(value: object) -> tuple[str, str]:
        return (type(value).__name__, str(value))

    @classmethod
    def _invalid_snapshot(cls, detail: object = None) -> None:
        raise LodError("LOD_SNAPSHOT_INVALID", detail)

    # ------------------------------------------------------------------
    # View registration
    # ------------------------------------------------------------------

    def register_view(self, view_id: str, priority: int = 0) -> ViewSpec:
        with self._lock:
            # Duplicate detection deliberately precedes the hard capacity
            # check: an existing view is a duplicate even when full.
            if view_id in self._views:
                raise LodError("LOD_DUPLICATE_VIEW", view_id)
            if len(self._views) >= self.max_views:
                raise LodError("LOD_VIEW_LIMIT", len(self._views))

            spec = ViewSpec(view_id=view_id, priority=priority)
            self._views[view_id] = spec
            return spec

    def unregister_view(self, view_id: str) -> int:
        with self._lock:
            if view_id not in self._views:
                raise LodError("LOD_UNKNOWN_VIEW", view_id)

            affected = {
                object_id
                for object_id, views in self._object_views.items()
                if view_id in views
            }
            dropped = 0

            for frame in list(self._pending):
                objects = self._pending[frame]
                for object_id in list(objects):
                    demands = objects[object_id]
                    if view_id not in demands:
                        continue
                    del demands[view_id]
                    affected.add(object_id)
                    dropped += 1
                    if not demands:
                        del objects[object_id]
                if not objects:
                    del self._pending[frame]

            # Removing a view must not immediately release the precision it
            # asked for.  Use the latest frame observed by the arbiter as the
            # start of the downgrade hold.
            hold_until = self._last_frame + self.cooldown_frames
            for object_id in affected:
                previous = self._downgrade_hold_until.get(object_id, -1)
                if hold_until > previous:
                    self._downgrade_hold_until[object_id] = hold_until

            del self._views[view_id]
            for object_id in list(self._object_views):
                views = self._object_views[object_id]
                views.discard(view_id)
                if not views:
                    del self._object_views[object_id]

            return dropped

    def set_view_priority(self, view_id: str, priority: int) -> None:
        with self._lock:
            if view_id not in self._views:
                raise LodError("LOD_UNKNOWN_VIEW", view_id)
            self._views[view_id] = ViewSpec(view_id=view_id, priority=priority)

    # ------------------------------------------------------------------
    # Requests and settlement
    # ------------------------------------------------------------------

    def request(self, view_id: str, object_id: str, lod: int, frame: int) -> None:
        with self._lock:
            if view_id not in self._views:
                raise LodError("LOD_UNKNOWN_VIEW", view_id)
            self._check_lod(lod)
            self._check_frame(frame)

            self._requests += 1
            self._last_frame = max(self._last_frame, frame)
            self._known_objects.add(object_id)
            self._object_views.setdefault(object_id, set()).add(view_id)
            demands = self._pending.setdefault(frame, {}).setdefault(object_id, {})
            # A later request from the same view for the same object/frame
            # supersedes the earlier one, without creating another switch.
            demands[view_id] = lod

    def _demands_for(self, object_id: str, frame: int) -> dict[str, int]:
        raw = self._pending.get(frame, {}).get(object_id, {})
        return {
            view_id: lod
            for view_id, lod in raw.items()
            if view_id in self._views
        }

    def _drop_pending(self, object_id: str, frame: int) -> None:
        frame_pending = self._pending.get(frame)
        if frame_pending is None:
            return
        frame_pending.pop(object_id, None)
        if not frame_pending:
            del self._pending[frame]

    def _record_decision(
        self,
        object_id: str,
        frame: int,
        demanded: int,
        resident: int,
        switched: str,
        deferred: bool,
        priority: int,
    ) -> LodDecision:
        decision = LodDecision(
            object_id=object_id,
            demanded=demanded,
            resident=resident,
            switched=switched,
            deferred=deferred,
        )
        self._known_objects.add(object_id)
        key = (frame, object_id)
        self._decisions[key] = decision
        self._decision_priority[key] = priority
        self._log.append({
            "frame": frame,
            "object_id": object_id,
            "demanded": demanded,
            "resident": resident,
            "switched": switched,
            "deferred": deferred,
        })
        return decision

    def _settle_locked(
        self,
        object_id: str,
        frame: int,
        demands: dict[str, int],
        priority: int,
    ) -> LodDecision:
        current = self._resident_or_default(object_id)
        if demands:
            demanded = min(demands.values())
        else:
            demanded = current

        resident = current
        switched = "none"
        deferred = False

        if demanded < current:
            # Upgrading to a finer level.  The cost is one budget unit per
            # object and frame, regardless of the number of levels crossed.
            cooldown_start = self._cooldown_start.get(object_id)
            in_cooldown = (
                cooldown_start is not None
                and frame < cooldown_start + self.cooldown_frames
            )
            used = self._upgrade_used.get(frame, 0)
            if in_cooldown or used >= self.frame_upgrade_budget:
                deferred = True
            else:
                self._upgrade_used[frame] = used + 1
                resident = demanded
                self._residents[object_id] = resident
                switched = "upgrade"
                self._upgrades += 1

        elif demanded > current:
            # Downgrades are immediate and free, subject only to hysteresis
            # and the special hold created by unregistering a view.
            gap = demanded - current
            if gap >= self.hysteresis:
                hold_until = self._downgrade_hold_until.get(object_id, -1)
                if frame < hold_until:
                    deferred = True
                else:
                    resident = demanded
                    self._residents[object_id] = resident
                    switched = "downgrade"
                    self._downgrades += 1
                    self._cooldown_start[object_id] = frame
                    self._downgrade_hold_until.pop(object_id, None)

        if deferred:
            self._deferred += 1

        return self._record_decision(
            object_id=object_id,
            frame=frame,
            demanded=demanded,
            resident=resident,
            switched=switched,
            deferred=deferred,
            priority=priority,
        )

    def arbitrate(self, object_id: str, frame: int) -> LodDecision:
        with self._lock:
            self._check_frame(frame)
            self._last_frame = max(self._last_frame, frame)
            key = (frame, object_id)
            cached = self._decisions.get(key)
            if cached is not None:
                return cached

            demands = self._demands_for(object_id, frame)
            priority = max(
                (self._views[view_id].priority for view_id in demands),
                default=0,
            )
            decision = self._settle_locked(object_id, frame, demands, priority)
            self._drop_pending(object_id, frame)
            return decision

    def _priority_for_frame_object(self, object_id: str, frame: int) -> int:
        key = (frame, object_id)
        if key in self._decisions:
            return self._decision_priority.get(key, 0)
        demands = self._demands_for(object_id, frame)
        return max(
            (self._views[view_id].priority for view_id in demands),
            default=0,
        )

    def arbitrate_frame(self, frame: int) -> dict:
        with self._lock:
            self._check_frame(frame)
            self._last_frame = max(self._last_frame, frame)

            object_ids = set(self._pending.get(frame, {}))
            object_ids.update(
                object_id
                for (decision_frame, object_id) in self._decisions
                if decision_frame == frame
            )

            ordered = sorted(
                object_ids,
                key=lambda object_id: (
                    -self._priority_for_frame_object(object_id, frame),
                    self._sort_key(object_id),
                ),
            )

            result: dict[str, int] = {}
            for object_id in ordered:
                key = (frame, object_id)
                cached = self._decisions.get(key)
                if cached is not None:
                    # Requests arriving after settlement for this frame do
                    # not get a second switch.
                    self._drop_pending(object_id, frame)
                    result[object_id] = cached.resident
                    continue

                demands = self._demands_for(object_id, frame)
                priority = self._priority_for_frame_object(object_id, frame)
                decision = self._settle_locked(
                    object_id, frame, demands, priority
                )
                self._drop_pending(object_id, frame)
                result[object_id] = decision.resident

            return result

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def resident_lod(self, object_id: str) -> int:
        with self._lock:
            return self._resident_or_default(object_id)

    def upgrade_budget(self, frame: int) -> int:
        with self._lock:
            self._check_frame(frame)
            used = self._upgrade_used.get(frame, 0)
            return max(0, self.frame_upgrade_budget - used)

    def decision_log(self) -> tuple:
        with self._lock:
            return tuple(dict(entry) for entry in self._log)

    def stats(self) -> dict:
        with self._lock:
            return {
                "views": len(self._views),
                "objects": len(self._known_objects),
                "upgrades": self._upgrades,
                "downgrades": self._downgrades,
                "deferred": self._deferred,
                "requests": self._requests,
            }

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            views = [
                {"view_id": spec.view_id, "priority": spec.priority}
                for spec in self._views.values()
            ]
            objects = sorted(self._known_objects, key=self._sort_key)
            resident = {
                object_id: self._residents[object_id]
                for object_id in sorted(self._residents, key=self._sort_key)
            }
            pending = [
                {
                    "frame": frame,
                    "object_id": object_id,
                    "demands": {
                        view_id: lod
                        for view_id, lod in sorted(
                            self._pending[frame][object_id].items(),
                            key=lambda item: self._sort_key(item[0]),
                        )
                    },
                }
                for frame in sorted(self._pending)
                for object_id in sorted(
                    self._pending[frame], key=self._sort_key
                )
            ]
            upgrade_used = [
                {"frame": frame, "used": used}
                for frame, used in sorted(self._upgrade_used.items())
            ]
            cooldown = {
                object_id: start
                for object_id, start in sorted(
                    self._cooldown_start.items(),
                    key=lambda item: self._sort_key(item[0]),
                )
            }
            downgrade_holds = {
                object_id: until
                for object_id, until in sorted(
                    self._downgrade_hold_until.items(),
                    key=lambda item: self._sort_key(item[0]),
                )
            }
            affected_by_view: dict[str, set[str]] = {}
            for object_id, view_ids in self._object_views.items():
                for view_id in view_ids:
                    if view_id in self._views:
                        affected_by_view.setdefault(view_id, set()).add(
                            object_id
                        )
            view_objects = [
                {
                    "view_id": view_id,
                    "objects": sorted(affected, key=self._sort_key),
                }
                for view_id, affected in sorted(
                    affected_by_view.items(),
                    key=lambda item: self._sort_key(item[0]),
                )
            ]
            decisions = [
                {
                    "frame": frame,
                    "object_id": object_id,
                    "demanded": decision.demanded,
                    "resident": decision.resident,
                    "switched": decision.switched,
                    "deferred": decision.deferred,
                    "priority": self._decision_priority.get(
                        (frame, object_id), 0
                    ),
                }
                for (frame, object_id), decision in sorted(
                    self._decisions.items(),
                    key=lambda item: (
                        item[0][0],
                        self._sort_key(item[0][1]),
                    ),
                )
            ]
            counters = {
                "views": len(self._views),
                "objects": len(self._known_objects),
                "upgrades": self._upgrades,
                "downgrades": self._downgrades,
                "deferred": self._deferred,
                "requests": self._requests,
            }

            return {
                "version": self._SNAPSHOT_VERSION,
                "config": {
                    "max_views": self.max_views,
                    "max_lod": self.max_lod,
                    "frame_upgrade_budget": self.frame_upgrade_budget,
                    "cooldown_frames": self.cooldown_frames,
                    "hysteresis": self.hysteresis,
                },
                "views": views,
                "objects": objects,
                "resident": resident,
                "pending": pending,
                "upgrade_used": upgrade_used,
                "cooldown": cooldown,
                "downgrade_holds": downgrade_holds,
                "view_objects": view_objects,
                "last_frame": self._last_frame,
                "decisions": decisions,
                "counters": counters,
                "log": [dict(entry) for entry in self._log],
            }

    def _parse_snapshot(self, state: dict) -> dict:
        if not isinstance(state, dict):
            self._invalid_snapshot("state must be a mapping")

        required = (
            "views",
            "objects",
            "resident",
            "pending",
            "upgrade_used",
            "cooldown",
            "downgrade_holds",
            "view_objects",
            "last_frame",
            "decisions",
            "counters",
            "log",
        )
        if any(key not in state for key in required):
            self._invalid_snapshot("missing state field")

        version = state.get("version", self._SNAPSHOT_VERSION)
        if not self._is_int(version) or version != self._SNAPSHOT_VERSION:
            self._invalid_snapshot("unsupported snapshot version")

        config = state.get("config")
        if config is not None:
            if not isinstance(config, dict):
                self._invalid_snapshot("config must be a mapping")
            for name in (
                "max_views",
                "max_lod",
                "frame_upgrade_budget",
                "cooldown_frames",
                "hysteresis",
            ):
                value = config.get(name)
                if not self._is_int(value) or value < 0:
                    self._invalid_snapshot(f"invalid config field: {name}")

        views: dict[str, ViewSpec] = {}
        raw_views = state["views"]
        if not isinstance(raw_views, list):
            self._invalid_snapshot("views must be a list")
        if len(raw_views) > self.max_views:
            self._invalid_snapshot("view limit exceeded")
        for raw in raw_views:
            if not isinstance(raw, dict):
                self._invalid_snapshot("invalid view entry")
            view_id = raw.get("view_id")
            priority = raw.get("priority")
            if not isinstance(view_id, str) or type(priority) is not int:
                self._invalid_snapshot("invalid view entry")
            if view_id in views:
                self._invalid_snapshot("duplicate view")
            views[view_id] = ViewSpec(view_id=view_id, priority=priority)

        raw_objects = state["objects"]
        if not isinstance(raw_objects, list):
            self._invalid_snapshot("objects must be a list")
        known_objects: set[str] = set()
        for object_id in raw_objects:
            if not isinstance(object_id, str):
                self._invalid_snapshot("invalid object id")
            if object_id in known_objects:
                self._invalid_snapshot("duplicate object")
            known_objects.add(object_id)

        raw_resident = state["resident"]
        if not isinstance(raw_resident, dict):
            self._invalid_snapshot("resident must be a mapping")
        residents: dict[str, int] = {}
        for object_id, lod in raw_resident.items():
            if not isinstance(object_id, str):
                self._invalid_snapshot("invalid resident object")
            self._check_snapshot_lod(lod)
            if object_id not in known_objects:
                self._invalid_snapshot("resident object is not tracked")
            residents[object_id] = lod

        pending: dict[int, dict[str, dict[str, int]]] = {}
        raw_pending = state["pending"]
        if not isinstance(raw_pending, list):
            self._invalid_snapshot("pending must be a list")
        for raw in raw_pending:
            if not isinstance(raw, dict):
                self._invalid_snapshot("invalid pending entry")
            frame = raw.get("frame")
            object_id = raw.get("object_id")
            demands = raw.get("demands")
            if not self._is_int(frame) or frame < 0:
                self._invalid_snapshot("invalid pending frame")
            if not isinstance(object_id, str):
                self._invalid_snapshot("invalid pending object")
            if not isinstance(demands, dict) or not demands:
                self._invalid_snapshot("invalid pending demands")
            object_pending = pending.setdefault(frame, {})
            if object_id in object_pending:
                self._invalid_snapshot("duplicate pending object")
            parsed_demands: dict[str, int] = {}
            for view_id, lod in demands.items():
                if not isinstance(view_id, str) or view_id not in views:
                    self._invalid_snapshot("pending view is not registered")
                self._check_snapshot_lod(lod)
                parsed_demands[view_id] = lod
            object_pending[object_id] = parsed_demands
            known_objects.add(object_id)

        raw_used = state["upgrade_used"]
        if not isinstance(raw_used, list):
            self._invalid_snapshot("upgrade_used must be a list")
        upgrade_used: dict[int, int] = {}
        for raw in raw_used:
            if not isinstance(raw, dict):
                self._invalid_snapshot("invalid budget entry")
            frame = raw.get("frame")
            used = raw.get("used")
            if not self._is_int(frame) or frame < 0:
                self._invalid_snapshot("invalid budget frame")
            if (
                not self._is_int(used)
                or used < 0
                or used > self.frame_upgrade_budget
            ):
                self._invalid_snapshot("invalid budget use")
            if frame in upgrade_used:
                self._invalid_snapshot("duplicate budget frame")
            upgrade_used[frame] = used

        cooldown = self._parse_frame_mapping(
            state["cooldown"], "cooldown", known_objects
        )
        downgrade_holds = self._parse_frame_mapping(
            state["downgrade_holds"], "downgrade_holds", known_objects
        )

        view_objects: dict[str, set[str]] = {}
        raw_view_objects = state["view_objects"]
        if not isinstance(raw_view_objects, list):
            self._invalid_snapshot("view_objects must be a list")
        seen_view_objects: set[str] = set()
        for raw in raw_view_objects:
            if not isinstance(raw, dict):
                self._invalid_snapshot("invalid view object entry")
            view_id = raw.get("view_id")
            objects = raw.get("objects")
            if not isinstance(view_id, str) or view_id not in views:
                self._invalid_snapshot("view object references unknown view")
            if view_id in seen_view_objects:
                self._invalid_snapshot("duplicate view object entry")
            seen_view_objects.add(view_id)
            if not isinstance(objects, list):
                self._invalid_snapshot("invalid view object list")
            parsed_objects: set[str] = set()
            for object_id in objects:
                if not isinstance(object_id, str) or object_id not in known_objects:
                    self._invalid_snapshot("view object is not tracked")
                if object_id in parsed_objects:
                    self._invalid_snapshot("duplicate view object")
                parsed_objects.add(object_id)
            for object_id in parsed_objects:
                view_objects.setdefault(object_id, set()).add(view_id)

        last_frame = state["last_frame"]
        if not self._is_int(last_frame) or last_frame < 0:
            self._invalid_snapshot("invalid last frame")

        decisions: dict[tuple[int, str], LodDecision] = {}
        decision_priority: dict[tuple[int, str], int] = {}
        raw_decisions = state["decisions"]
        if not isinstance(raw_decisions, list):
            self._invalid_snapshot("decisions must be a list")
        for raw in raw_decisions:
            if not isinstance(raw, dict):
                self._invalid_snapshot("invalid decision entry")
            frame = raw.get("frame")
            object_id = raw.get("object_id")
            demanded = raw.get("demanded")
            resident = raw.get("resident")
            switched = raw.get("switched")
            deferred = raw.get("deferred")
            priority = raw.get("priority", 0)
            if not self._is_int(frame) or frame < 0:
                self._invalid_snapshot("invalid decision frame")
            if not isinstance(object_id, str) or object_id not in known_objects:
                self._invalid_snapshot("invalid decision object")
            self._check_snapshot_lod(demanded)
            self._check_snapshot_lod(resident)
            if (
                not isinstance(switched, str)
                or switched not in self._SWITCH_VALUES
            ):
                self._invalid_snapshot("invalid decision switch")
            if type(deferred) is not bool:
                self._invalid_snapshot("invalid decision deferred flag")
            if type(priority) is not int:
                self._invalid_snapshot("invalid decision priority")
            key = (frame, object_id)
            if key in decisions:
                self._invalid_snapshot("duplicate decision")
            decisions[key] = LodDecision(
                object_id=object_id,
                demanded=demanded,
                resident=resident,
                switched=switched,
                deferred=deferred,
            )
            decision_priority[key] = priority

        log: list[dict[str, object]] = []
        raw_log = state["log"]
        if not isinstance(raw_log, list):
            self._invalid_snapshot("log must be a list")
        for raw in raw_log:
            if not isinstance(raw, dict):
                self._invalid_snapshot("invalid log entry")
            frame = raw.get("frame")
            object_id = raw.get("object_id")
            demanded = raw.get("demanded")
            resident = raw.get("resident")
            switched = raw.get("switched")
            deferred = raw.get("deferred")
            if not self._is_int(frame) or frame < 0:
                self._invalid_snapshot("invalid log frame")
            if not isinstance(object_id, str) or object_id not in known_objects:
                self._invalid_snapshot("invalid log object")
            self._check_snapshot_lod(demanded)
            self._check_snapshot_lod(resident)
            if (
                not isinstance(switched, str)
                or switched not in self._SWITCH_VALUES
            ):
                self._invalid_snapshot("invalid log switch")
            if type(deferred) is not bool:
                self._invalid_snapshot("invalid log deferred flag")
            log.append({
                "frame": frame,
                "object_id": object_id,
                "demanded": demanded,
                "resident": resident,
                "switched": switched,
                "deferred": deferred,
            })

        counters = state["counters"]
        if not isinstance(counters, dict):
            self._invalid_snapshot("counters must be a mapping")
        counter_names = (
            "views",
            "objects",
            "upgrades",
            "downgrades",
            "deferred",
            "requests",
        )
        for name in counter_names:
            value = counters.get(name)
            if not self._is_int(value) or value < 0:
                self._invalid_snapshot(f"invalid counter: {name}")
        if counters["views"] != len(views):
            self._invalid_snapshot("view counter mismatch")
        if counters["objects"] != len(known_objects):
            self._invalid_snapshot("object counter mismatch")
        if counters["upgrades"] != sum(
            1 for entry in log if entry["switched"] == "upgrade"
        ):
            self._invalid_snapshot("upgrade counter mismatch")
        if counters["downgrades"] != sum(
            1 for entry in log if entry["switched"] == "downgrade"
        ):
            self._invalid_snapshot("downgrade counter mismatch")
        if counters["deferred"] != sum(
            1 for entry in log if entry["deferred"]
        ):
            self._invalid_snapshot("deferred counter mismatch")

        return {
            "views": views,
            "known_objects": known_objects,
            "residents": residents,
            "pending": pending,
            "upgrade_used": upgrade_used,
            "cooldown": cooldown,
            "downgrade_holds": downgrade_holds,
            "view_objects": view_objects,
            "last_frame": last_frame,
            "decisions": decisions,
            "decision_priority": decision_priority,
            "log": log,
            "upgrades": counters["upgrades"],
            "downgrades": counters["downgrades"],
            "deferred": counters["deferred"],
            "requests": counters["requests"],
        }

    def _check_snapshot_lod(self, lod: object) -> None:
        if not self._is_int(lod) or lod < 0 or lod > self.max_lod:
            self._invalid_snapshot("invalid lod in snapshot")

    def _parse_frame_mapping(
        self,
        raw: object,
        name: str,
        known_objects: set[str],
    ) -> dict[str, int]:
        if not isinstance(raw, dict):
            self._invalid_snapshot(f"{name} must be a mapping")
        parsed: dict[str, int] = {}
        for object_id, frame in raw.items():
            if not isinstance(object_id, str) or object_id not in known_objects:
                self._invalid_snapshot(f"invalid {name} object")
            if not self._is_int(frame) or frame < 0:
                self._invalid_snapshot(f"invalid {name} frame")
            parsed[object_id] = frame
        return parsed

    def restore(self, state: dict) -> None:
        with self._lock:
            # Parse and validate everything into fresh containers first.  Only
            # after that succeeds do we replace any live state, making restore
            # failure atomic.
            parsed = self._parse_snapshot(state)

            self._views = parsed["views"]
            self._known_objects = parsed["known_objects"]
            self._residents = parsed["residents"]
            self._pending = parsed["pending"]
            self._upgrade_used = parsed["upgrade_used"]
            self._cooldown_start = parsed["cooldown"]
            self._downgrade_hold_until = parsed["downgrade_holds"]
            self._object_views = parsed["view_objects"]
            self._last_frame = parsed["last_frame"]
            self._decisions = parsed["decisions"]
            self._decision_priority = parsed["decision_priority"]
            self._log = parsed["log"]
            self._upgrades = parsed["upgrades"]
            self._downgrades = parsed["downgrades"]
            self._deferred = parsed["deferred"]
            self._requests = parsed["requests"]
