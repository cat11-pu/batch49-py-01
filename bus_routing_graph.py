"""Audio bus routing graph: topology validation, gain, delay, mute, and solo."""

from __future__ import annotations

import threading


class BusError(ValueError):
    """Raised when a bus, connection, or query is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class BusRoutingGraph:
    """Own buses, directed sends, and the derived routing properties."""

    def __init__(self, *, master_id: str = "master", max_buses: int = 16,
                 max_connections: int = 32, max_depth: int = 4) -> None:
        self.master_id = master_id
        self.max_buses = max_buses
        self.max_connections = max_connections
        self.max_depth = max_depth
        self._lock = threading.RLock()

    def add_bus(self, bus_id: str, *, gain: float = 1.0,
                delay_frames: int = 0) -> None:
        raise NotImplementedError

    def connect(self, src: str, dst: str, *, gain: float = 1.0,
                delay_frames: int = 0) -> None:
        raise NotImplementedError

    def disconnect(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def effective_gain(self, bus_id: str) -> float:
        raise NotImplementedError

    def effective_delay(self, bus_id: str) -> int:
        raise NotImplementedError

    def mute(self, bus_id: str, muted: bool = True) -> None:
        raise NotImplementedError

    def solo(self, bus_id: str, enabled: bool = True) -> None:
        raise NotImplementedError

    def topology_order(self) -> list[str]:
        raise NotImplementedError

    def buses(self) -> list[str]:
        raise NotImplementedError

    def connections(self) -> list[tuple[str, str, float, int]]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
