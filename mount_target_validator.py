"""Bind-mount preflight: source/target identity, inode checks, mount bookkeeping."""

from __future__ import annotations

import threading


class MountCheckError(ValueError):
    """Refused bind-mount operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class MountTargetValidator:
    """Validate and track bind mounts without leaving half-applied state."""

    def __init__(self, *, allowed_source_roots=("/srv",), max_mounts: int = 8,
                 max_depth: int = 4, bind_supported: bool = True,
                 clock=None) -> None:
        self.allowed_source_roots = tuple(allowed_source_roots)
        self.max_mounts = max_mounts
        self.max_depth = max_depth
        self.bind_supported = bind_supported
        self._clock = clock
        self._lock = threading.RLock()

    def resolve(self, path: str) -> str:
        raise NotImplementedError

    def validate(self, source: str, target: str, *, recursive: bool = False,
                 read_only: bool = False, overmount: bool = False,
                 allow_create: bool = False) -> dict:
        raise NotImplementedError

    def apply(self, source: str, target: str, *, recursive: bool = False,
              read_only: bool = False, overmount: bool = False,
              allow_create: bool = False, receipt: dict | None = None) -> dict:
        raise NotImplementedError

    def unmount(self, target: str) -> dict:
        raise NotImplementedError

    def is_mounted(self, target: str) -> bool:
        raise NotImplementedError

    def mounts(self) -> list[dict]:
        raise NotImplementedError

    def report(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
