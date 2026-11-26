"""Deterministic 2D contact detection and impulse resolution.

The resolver works on circles and axis-aligned boxes only. All public
operations are serialised with a re-entrant lock, and every traversal that can
affect simulation state uses body-id ordering. Contacts are rebuilt from
scratch on each step; one record is kept per body pair.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass


class ContactError(ValueError):
    """Raised when a body or step operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class RigidBody:
    body_id: str
    shape: str = "circle"
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 1.0
    half_w: float = 1.0
    half_h: float = 1.0
    mass: float = 1.0
    restitution: float = 0.5
    friction: float = 0.3
    static: bool = False
    sleeping: bool = False


@dataclass(frozen=True)
class Contact:
    a: str
    b: str
    nx: float
    ny: float
    penetration: float
    impulse: float


_EPSILON = 1e-12
_MAX_STEP = 0.25


def _finite_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


class ContactImpulseResolver:
    """Fixed-step resolver with contact dedup, clamping and explicit sleep control."""

    def __init__(self, *, position_correction: float = 0.8,
                 max_correction: float = 0.25, sleep_speed: float = 0.02,
                 iterations: int = 4) -> None:
        if not _finite_number(position_correction):
            raise ContactError("INVALID_CONFIG", "position_correction")
        if not _finite_number(max_correction) or float(max_correction) < 0.0:
            raise ContactError("INVALID_CONFIG", "max_correction")
        if not _finite_number(sleep_speed) or float(sleep_speed) < 0.0:
            raise ContactError("INVALID_CONFIG", "sleep_speed")
        if (not isinstance(iterations, int) or isinstance(iterations, bool)
                or iterations < 1):
            raise ContactError("INVALID_CONFIG", "iterations")

        self.position_correction = min(1.0, max(0.0, float(position_correction)))
        self.max_correction = float(max_correction)
        self.sleep_speed = float(sleep_speed)
        self.iterations = iterations
        self._lock = threading.RLock()
        self._bodies: dict[str, RigidBody] = {}
        self._contacts: list[Contact] = []
        self._previous_pairs: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Body management
    # ------------------------------------------------------------------
    def add_body(self, body: RigidBody) -> None:
        with self._lock:
            if not isinstance(body, RigidBody):
                raise ContactError("INVALID_BODY", "body")
            if not isinstance(body.body_id, str) or not body.body_id:
                raise ContactError("INVALID_BODY", "body_id")
            if body.body_id in self._bodies:
                raise ContactError("DUPLICATE_BODY", body.body_id)
            self._validate_body(body)
            self._bodies[body.body_id] = body

    def remove_body(self, body_id: str) -> bool:
        with self._lock:
            if body_id not in self._bodies:
                return False
            del self._bodies[body_id]
            self._contacts = [
                contact for contact in self._contacts
                if contact.a != body_id and contact.b != body_id
            ]
            self._previous_pairs = {
                pair for pair in self._previous_pairs
                if pair[0] != body_id and pair[1] != body_id
            }
            return True

    def set_material(self, body_id: str, restitution: float,
                     friction: float) -> None:
        with self._lock:
            body = self._require(body_id)
            if not _finite_number(restitution) or not _finite_number(friction):
                raise ContactError("INVALID_BODY", body_id)
            body.restitution = self._clamp_restitution(restitution)
            body.friction = float(friction)

    def body(self, body_id: str) -> RigidBody:
        with self._lock:
            return self._require(body_id)

    def wake(self, body_id: str) -> None:
        with self._lock:
            body = self._require(body_id)
            if not body.static:
                body.sleeping = False

    def sleep_body(self, body_id: str) -> None:
        with self._lock:
            body = self._require(body_id)
            if not body.static:
                body.sleeping = True

    def sleeping(self, body_id: str) -> bool:
        with self._lock:
            body = self._require(body_id)
            return bool(body.sleeping and not body.static)

    def apply_impulse(self, body_id: str, ix: float, iy: float) -> None:
        with self._lock:
            body = self._require(body_id)
            if not _finite_number(ix) or not _finite_number(iy):
                raise ContactError("INVALID_BODY", body_id)
            if not body.static:
                body.vx += float(ix) / body.mass
                body.vy += float(iy) / body.mass
                body.sleeping = False

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------
    def step(self, dt: float, iterations: int | None = None) -> None:
        with self._lock:
            if (not _finite_number(dt) or float(dt) <= 0.0
                    or float(dt) > _MAX_STEP):
                raise ContactError("INVALID_STEP", dt)
            if iterations is None:
                iteration_count = self.iterations
            elif (not isinstance(iterations, int)
                  or isinstance(iterations, bool) or iterations < 1):
                raise ContactError("INVALID_STEP", iterations)
            else:
                iteration_count = iterations

            ordered = [self._bodies[key] for key in sorted(self._bodies)]

            for body in ordered:
                if body.static or body.sleeping:
                    continue
                body.x += body.vx * dt
                body.y += body.vy * dt

            detected = self._detect_all(ordered)
            pair_set = {(body_a.body_id, body_b.body_id)
                        for body_a, body_b, _nx, _ny, _pen in detected}
            new_pairs = pair_set - self._previous_pairs
            sleeping_at_start = {
                body.body_id for body in ordered
                if body.sleeping and not body.static
            }

            solvers = []
            frozen_ids: set[str] = set()
            for body_a, body_b, nx, ny, penetration in detected:
                pair = (body_a.body_id, body_b.body_id)
                inactive = (body_a.body_id in sleeping_at_start
                            or body_b.body_id in sleeping_at_start)

                if pair in new_pairs:
                    for body in (body_a, body_b):
                        if body.body_id in sleeping_at_start:
                            body.sleeping = False
                            frozen_ids.add(body.body_id)
                            inactive = True

                if inactive:
                    frozen_ids.add(body_a.body_id)
                    frozen_ids.add(body_b.body_id)

                restitution = max(
                    self._clamp_restitution(body_a.restitution),
                    self._clamp_restitution(body_b.restitution),
                )
                friction = max(0.0, body_a.friction, body_b.friction)
                solvers.append([
                    body_a, body_b, nx, ny, penetration,
                    0.0, not inactive, restitution, friction,
                ])

            for _ in range(iteration_count):
                for solver in solvers:
                    if not solver[6]:
                        continue
                    self._solve_velocity(solver)

            for solver in solvers:
                if solver[6]:
                    self._correct_position(solver)

            for body in ordered:
                if body.static or body.sleeping:
                    continue
                if body.body_id in frozen_ids:
                    continue
                if math.hypot(body.vx, body.vy) <= self.sleep_speed:
                    body.sleeping = True

            self._contacts = [
                Contact(
                    a=solver[0].body_id,
                    b=solver[1].body_id,
                    nx=float(solver[2]),
                    ny=float(solver[3]),
                    penetration=float(solver[4]),
                    impulse=float(solver[5]),
                )
                for solver in solvers
            ]
            self._previous_pairs = pair_set

    def contacts(self) -> list:
        with self._lock:
            return list(self._contacts)

    def stats(self) -> dict:
        with self._lock:
            return {
                "bodies": len(self._bodies),
                "contacts": len(self._contacts),
                "sleeping": sum(
                    1 for body in self._bodies.values()
                    if body.sleeping and not body.static
                ),
            }

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            bodies = []
            for body_id in sorted(self._bodies):
                body = self._bodies[body_id]
                bodies.append({
                    "body_id": body.body_id,
                    "shape": body.shape,
                    "x": float(body.x),
                    "y": float(body.y),
                    "vx": float(body.vx),
                    "vy": float(body.vy),
                    "radius": float(body.radius),
                    "half_w": float(body.half_w),
                    "half_h": float(body.half_h),
                    "mass": float(body.mass),
                    "restitution": float(body.restitution),
                    "friction": float(body.friction),
                    "static": bool(body.static),
                    "sleeping": bool(body.sleeping),
                })
            return {"version": 1, "bodies": bodies}

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                if not isinstance(state, dict):
                    raise ValueError("state")
                if state.get("version", 1) != 1:
                    raise ValueError("version")
                raw_bodies = state["bodies"]
                if not isinstance(raw_bodies, list):
                    raise ValueError("bodies")

                restored: dict[str, RigidBody] = {}
                required = (
                    "body_id", "shape", "x", "y", "vx", "vy", "radius",
                    "half_w", "half_h", "mass", "restitution", "friction",
                    "static", "sleeping",
                )
                for raw in raw_bodies:
                    if not isinstance(raw, dict):
                        raise ValueError("body")
                    if any(key not in raw for key in required):
                        raise ValueError("body fields")
                    if (not isinstance(raw["static"], bool)
                            or not isinstance(raw["sleeping"], bool)):
                        raise ValueError("body flags")
                    body = RigidBody(
                        body_id=raw["body_id"],
                        shape=raw["shape"],
                        x=raw["x"],
                        y=raw["y"],
                        vx=raw["vx"],
                        vy=raw["vy"],
                        radius=raw["radius"],
                        half_w=raw["half_w"],
                        half_h=raw["half_h"],
                        mass=raw["mass"],
                        restitution=raw["restitution"],
                        friction=raw["friction"],
                        static=raw["static"],
                        sleeping=raw["sleeping"],
                    )
                    if body.body_id in restored:
                        raise ValueError("duplicate body")
                    self._validate_body(body)
                    restored[body.body_id] = body
            except Exception as exc:
                detail = exc.detail if isinstance(exc, ContactError) else exc
                raise ContactError("INVALID_SNAPSHOT", detail) from None

            self._bodies = restored
            self._contacts = []
            self._previous_pairs = set()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _require(self, body_id: str) -> RigidBody:
        body = self._bodies.get(body_id)
        if body is None:
            raise ContactError("UNKNOWN_BODY", body_id)
        return body

    @staticmethod
    def _clamp_restitution(value: object) -> float:
        number = float(value)
        return min(1.0, max(0.0, number))

    def _validate_body(self, body: RigidBody) -> None:
        if not isinstance(body.body_id, str) or not body.body_id:
            raise ContactError("INVALID_BODY", "body_id")
        if body.shape not in ("circle", "box"):
            raise ContactError("INVALID_BODY", "shape")
        for name in ("x", "y", "vx", "vy", "mass", "restitution", "friction"):
            if not _finite_number(getattr(body, name)):
                raise ContactError("INVALID_BODY", name)
        if body.shape == "circle":
            if not _finite_number(body.radius) or float(body.radius) <= 0.0:
                raise ContactError("INVALID_BODY", "radius")
        else:
            if not _finite_number(body.half_w) or float(body.half_w) <= 0.0:
                raise ContactError("INVALID_BODY", "half_w")
            if not _finite_number(body.half_h) or float(body.half_h) <= 0.0:
                raise ContactError("INVALID_BODY", "half_h")

        body.static = bool(body.static)
        if body.static:
            if float(body.mass) < 0.0:
                raise ContactError("INVALID_BODY", "mass")
            body.sleeping = False
        else:
            if float(body.mass) <= 0.0:
                raise ContactError("INVALID_BODY", "mass")
            body.sleeping = bool(body.sleeping)
        body.restitution = self._clamp_restitution(body.restitution)
        body.friction = float(body.friction)

    def _detect_all(self, ordered: list[RigidBody]) -> list[tuple]:
        found = []
        count = len(ordered)
        for index_a in range(count):
            body_a = ordered[index_a]
            for index_b in range(index_a + 1, count):
                body_b = ordered[index_b]
                geometry = self._detect_pair(body_a, body_b)
                if geometry is None:
                    continue
                nx, ny, penetration = geometry
                if penetration <= _EPSILON:
                    continue
                found.append((body_a, body_b, nx, ny, penetration))
        return found

    def _detect_pair(self, body_a: RigidBody,
                     body_b: RigidBody) -> tuple[float, float, float] | None:
        if body_a.shape == "circle" and body_b.shape == "circle":
            return self._circle_circle(body_a, body_b)
        if body_a.shape == "circle" and body_b.shape == "box":
            return self._circle_box(body_a, body_b)
        if body_a.shape == "box" and body_b.shape == "circle":
            normal = self._circle_box(body_b, body_a)
            if normal is None:
                return None
            return (-normal[0], -normal[1], normal[2])
        return self._box_box(body_a, body_b)

    @staticmethod
    def _circle_circle(body_a: RigidBody,
                       body_b: RigidBody) -> tuple[float, float, float] | None:
        dx = body_b.x - body_a.x
        dy = body_b.y - body_a.y
        radius_sum = body_a.radius + body_b.radius
        distance_squared = dx * dx + dy * dy
        if distance_squared >= radius_sum * radius_sum:
            return None
        distance = math.sqrt(distance_squared)
        if distance <= _EPSILON:
            return (1.0, 0.0, float(radius_sum))
        return (dx / distance, dy / distance, radius_sum - distance)

    @staticmethod
    def _circle_box(circle: RigidBody,
                    box: RigidBody) -> tuple[float, float, float] | None:
        left = box.x - box.half_w
        right = box.x + box.half_w
        bottom = box.y - box.half_h
        top = box.y + box.half_h
        closest_x = min(max(circle.x, left), right)
        closest_y = min(max(circle.y, bottom), top)
        dx = circle.x - closest_x
        dy = circle.y - closest_y
        distance_squared = dx * dx + dy * dy
        radius = circle.radius
        if distance_squared >= radius * radius:
            return None

        if distance_squared > _EPSILON * _EPSILON:
            distance = math.sqrt(distance_squared)
            return (-dx / distance, -dy / distance, radius - distance)

        distances = (
            (circle.x - left, 1.0, 0.0),
            (right - circle.x, -1.0, 0.0),
            (circle.y - bottom, 0.0, 1.0),
            (top - circle.y, 0.0, -1.0),
        )
        face_distance, nx, ny = min(distances, key=lambda item: item[0])
        return (nx, ny, radius + face_distance)

    @staticmethod
    def _box_box(body_a: RigidBody,
                 body_b: RigidBody) -> tuple[float, float, float] | None:
        a_left = body_a.x - body_a.half_w
        a_right = body_a.x + body_a.half_w
        b_left = body_b.x - body_b.half_w
        b_right = body_b.x + body_b.half_w
        overlap_x = min(a_right - b_left, b_right - a_left)
        if overlap_x <= 0.0:
            return None
        a_bottom = body_a.y - body_a.half_h
        a_top = body_a.y + body_a.half_h
        b_bottom = body_b.y - body_b.half_h
        b_top = body_b.y + body_b.half_h
        overlap_y = min(a_top - b_bottom, b_top - a_bottom)
        if overlap_y <= 0.0:
            return None

        if overlap_x <= overlap_y:
            if body_a.x <= body_b.x:
                return (1.0, 0.0, overlap_x)
            return (-1.0, 0.0, overlap_x)
        if body_a.y <= body_b.y:
            return (0.0, 1.0, overlap_y)
        return (0.0, -1.0, overlap_y)

    @staticmethod
    def _inverse_mass(body: RigidBody) -> float:
        if body.static:
            return 0.0
        return 1.0 / body.mass

    def _solve_velocity(self, solver: list) -> None:
        body_a, body_b, nx, ny, _penetration, _impulse, _active, restitution, friction = solver
        inverse_a = self._inverse_mass(body_a)
        inverse_b = self._inverse_mass(body_b)
        inverse_sum = inverse_a + inverse_b
        if inverse_sum <= 0.0:
            return

        velocity_ax = 0.0 if body_a.static else body_a.vx
        velocity_ay = 0.0 if body_a.static else body_a.vy
        velocity_bx = 0.0 if body_b.static else body_b.vx
        velocity_by = 0.0 if body_b.static else body_b.vy
        relative_x = velocity_ax - velocity_bx
        relative_y = velocity_ay - velocity_by
        normal_velocity = relative_x * nx + relative_y * ny
        if normal_velocity > 0.0:
            impulse = (1.0 + restitution) * normal_velocity / inverse_sum
            body_a.vx -= impulse * inverse_a * nx
            body_a.vy -= impulse * inverse_a * ny
            body_b.vx += impulse * inverse_b * nx
            body_b.vy += impulse * inverse_b * ny
            solver[5] += impulse
        else:
            impulse = 0.0

        tangent_x = -ny
        tangent_y = nx
        velocity_ax = 0.0 if body_a.static else body_a.vx
        velocity_ay = 0.0 if body_a.static else body_a.vy
        velocity_bx = 0.0 if body_b.static else body_b.vx
        velocity_by = 0.0 if body_b.static else body_b.vy
        relative_x = velocity_ax - velocity_bx
        relative_y = velocity_ay - velocity_by
        tangent_velocity = relative_x * tangent_x + relative_y * tangent_y
        tangent_impulse = -tangent_velocity / inverse_sum
        limit = max(0.0, friction) * impulse
        tangent_impulse = min(limit, max(-limit, tangent_impulse))
        body_a.vx += tangent_impulse * inverse_a * tangent_x
        body_a.vy += tangent_impulse * inverse_a * tangent_y
        body_b.vx -= tangent_impulse * inverse_b * tangent_x
        body_b.vy -= tangent_impulse * inverse_b * tangent_y

    def _correct_position(self, solver: list) -> None:
        body_a, body_b, nx, ny, penetration = solver[:5]
        inverse_a = self._inverse_mass(body_a)
        inverse_b = self._inverse_mass(body_b)
        inverse_sum = inverse_a + inverse_b
        if inverse_sum <= 0.0:
            return

        correction = min(
            self.max_correction,
            self.position_correction * penetration,
        )
        if correction <= 0.0:
            return
        share_a = correction * inverse_a / inverse_sum
        share_b = correction * inverse_b / inverse_sum
        body_a.x -= share_a * nx
        body_a.y -= share_a * ny
        body_b.x += share_b * nx
        body_b.y += share_b * ny
