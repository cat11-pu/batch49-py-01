"""Container creation admission: dangerous mounts, capability inflation and
host namespace exposure."""

from __future__ import annotations

import threading


class ContainerEscapeError(ValueError):
    """Raised when a spec is malformed or a hard capacity limit is hit."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ContainerEscapeGuard:
    """Score container specs and quarantine repeat offenders by container key."""

    def __init__(self, *, max_mounts: int = 16, max_findings: int = 64,
                 quarantine_after: int = 3, quarantine_ttl: int = 300,
                 max_profiles: int = 8, max_path_len: int = 256) -> None:
        self.max_mounts = max_mounts
        self.max_findings = max_findings
        self.quarantine_after = quarantine_after
        self.quarantine_ttl = quarantine_ttl
        self.max_profiles = max_profiles
        self.max_path_len = max_path_len
        self._lock = threading.RLock()

    def normalize_path(self, path: str) -> str:
        raise NotImplementedError

    def classify_mount(self, source: str, target: str, *,
                       propagation: str = "rprivate",
                       read_only: bool = False) -> str:
        raise NotImplementedError

    def check_capabilities(self, add=(), drop=()) -> dict:
        raise NotImplementedError

    def namespace_plan(self, spec: dict) -> dict:
        raise NotImplementedError

    def evaluate(self, spec: dict, *, now: int = 0) -> dict:
        raise NotImplementedError

    def register_profile(self, name: str, spec: dict) -> dict:
        raise NotImplementedError

    def profiles(self) -> list:
        raise NotImplementedError

    def quarantine_state(self, key: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def release(self, key: str, *, now: int = 0) -> bool:
        raise NotImplementedError

    def findings(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
