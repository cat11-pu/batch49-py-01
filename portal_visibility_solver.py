"""Portal visibility solver: portal clipping, door fractions, cell propagation."""

from __future__ import annotations

import threading


class PortalError(ValueError):
    """Raised when a portal visibility request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class PortalVisibilitySolver:
    """Propagate a visibility volume from the camera cell through open portals."""

    def __init__(self, *, max_depth: int = 4, max_portals: int = 64,
                 clip_budget: int = 256) -> None:
        self.max_depth = max_depth
        self.max_portals = max_portals
        self.clip_budget = clip_budget
        self._lock = threading.RLock()

    def add_cell(self, cell_id: str, x0: float, y0: float,
                 x1: float, y1: float) -> None:
        """Register one axis-aligned rectangular cell."""
        raise NotImplementedError

    def add_portal(self, portal_id: str, cell_a: str, cell_b: str,
                   axis: str, position: float, lo: float, hi: float) -> None:
        """Register one door on a segment between two cells."""
        raise NotImplementedError

    def set_camera_cell(self, cell_id: str) -> None:
        """Place the camera in one cell."""
        raise NotImplementedError

    def set_eye(self, x: float, y: float) -> None:
        """Set the camera position in world coordinates."""
        raise NotImplementedError

    def open_portal(self, portal_id: str) -> None:
        """Mark one portal fully open."""
        raise NotImplementedError

    def close_portal(self, portal_id: str) -> None:
        """Mark one portal fully closed."""
        raise NotImplementedError

    def set_door_fraction(self, portal_id: str, fraction: float) -> None:
        """Narrow one portal opening to the given fraction."""
        raise NotImplementedError

    def solve(self) -> list:
        """Propagate visibility and return visible cell ids in discovery order."""
        raise NotImplementedError

    def visible_cells(self) -> list:
        """Return the cells found by the last solve call."""
        raise NotImplementedError

    def visible_region(self, cell_id: str) -> tuple | None:
        """Return the visible rectangle for one cell, or None."""
        raise NotImplementedError

    def visible_area(self, cell_id: str) -> float:
        """Return the visible area for one cell."""
        raise NotImplementedError

    def visible_objects(self, objects) -> list:
        """Filter (object_id, cell_id) rows by visible cells, sorted by id."""
        raise NotImplementedError

    def budget_used(self) -> int:
        """Return how many portal clip operations the last solve consumed."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Return a detached snapshot of cells, portals, camera and results."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Restore state produced by snapshot, rejecting malformed input."""
        raise NotImplementedError
