"""Global lock-ordering guard: ranked resources, all-or-nothing acquisition, upgrades."""

from __future__ import annotations

import threading


class LockOrderError(ValueError):
    """Refused lock-table operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LockOrderDeadlockGuard:
    """Serialize multi-resource work behind one global resource order."""

    def __init__(self, *, max_locks_per_request: int = 8, max_locks: int = 64,
                 clock=None) -> None:
        self.max_locks_per_request = max_locks_per_request
        self.max_locks = max_locks
        self._clock = clock
        self._lock = threading.RLock()

    def register_resource(self, name: str, rank: int | None = None) -> int:
        raise NotImplementedError

    def order_version(self) -> int:
        raise NotImplementedError

    def acquire_many(self, resources, *, owner=None, timeout: float = 0.0,
                     mode: str = "shared") -> tuple[str, ...]:
        raise NotImplementedError

    def release(self, resource: str, *, owner=None, count: int = 1) -> int:
        raise NotImplementedError

    def release_all(self, *, owner=None) -> int:
        raise NotImplementedError

    def try_upgrade(self, resource: str, *, owner=None) -> bool:
        raise NotImplementedError

    def held(self, *, owner=None) -> dict[str, int]:
        raise NotImplementedError

    def wait_graph(self) -> dict[str, list[str]]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
