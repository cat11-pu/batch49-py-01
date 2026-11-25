"""Select a syscall filter profile from executable path, inode, digest and argv."""

from __future__ import annotations

import threading


class ProfileSelectError(ValueError):
    """Refused profile-table operation."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SyscallProfileSelector:
    """Bind an executable image to a filter profile generation."""

    def __init__(self, *, cache_size: int = 16, max_rules: int = 32,
                 default_profile: str = "deny-all", clock=None) -> None:
        self.cache_size = cache_size
        self.max_rules = max_rules
        self.default_profile = default_profile
        self._clock = clock
        self._lock = threading.RLock()

    def register(self, name: str, *, path: str | None = None,
                 inode: str | None = None, digest: str | None = None,
                 argv_prefix=None, policy=None) -> int:
        raise NotImplementedError

    def unregister(self, name: str) -> int:
        raise NotImplementedError

    def generation(self) -> int:
        raise NotImplementedError

    def profiles(self) -> list[str]:
        raise NotImplementedError

    def select(self, exe_path: str, argv=(), *, now=None) -> dict:
        raise NotImplementedError

    def invalidate(self, *, path: str | None = None, inode: str | None = None,
                   digest: str | None = None) -> int:
        raise NotImplementedError

    def binding_current(self, binding: dict) -> bool:
        raise NotImplementedError

    def cache_stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
