"""2D contact detection and impulse resolution for circles and axis-aligned boxes."""

from __future__ import annotations

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


class ContactImpulseResolver:
    """Fixed-step resolver with contact dedup, clamping and explicit sleep control."""

    def __init__(self, *, position_correction: float = 0.8,
                 max_correction: float = 0.25, sleep_speed: float = 0.02,
                 iterations: int = 4) -> None:
        self.position_correction = position_correction
        self.max_correction = max_correction
        self.sleep_speed = sleep_speed
        self.iterations = iterations
        self._lock = threading.RLock()

    def add_body(self, body: RigidBody) -> None:
        raise NotImplementedError

    def remove_body(self, body_id: str) -> bool:
        raise NotImplementedError

    def set_material(self, body_id: str, restitution: float,
                     friction: float) -> None:
        raise NotImplementedError

    def step(self, dt: float, iterations: int | None = None) -> None:
        raise NotImplementedError

    def contacts(self) -> list:
        raise NotImplementedError

    def body(self, body_id: str) -> RigidBody:
        raise NotImplementedError

    def wake(self, body_id: str) -> None:
        raise NotImplementedError

    def sleep_body(self, body_id: str) -> None:
        raise NotImplementedError

    def sleeping(self, body_id: str) -> bool:
        raise NotImplementedError

    def apply_impulse(self, body_id: str, ix: float, iy: float) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
