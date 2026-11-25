"""Kernel module admission: whitelist rules, signature gate, version floor, measurement."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class KernelModuleError(ValueError):
    """Raised when a module admission operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class VersionRule:
    name: str
    min_version: str
    max_version: str
    allowed_keys: tuple[str, ...]


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    material: str
    not_before: int
    not_after: int
    revoked_at: int | None


@dataclass(frozen=True)
class ModuleRecord:
    name: str
    version: str
    digest: str
    key_id: str
    state: str
    admitted_at: int
    measured_at: int | None
    measures: int


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    version: str
    state: str
    stale: bool


@dataclass(frozen=True)
class Admission:
    accepted: bool
    record: ModuleRecord


class KernelModuleAdmission:
    """Admit, load, measure and block kernel modules from a local whitelist."""

    def __init__(self, *, max_modules: int = 32) -> None:
        self.max_modules = max_modules
        self._lock = threading.RLock()

    def add_rule(self, name: str, *, min_version: str, max_version: str,
                 allowed_keys: tuple[str, ...]) -> VersionRule:
        raise NotImplementedError

    def remove_rule(self, name: str) -> bool:
        raise NotImplementedError

    def register_key(self, key_id: str, material: str, *,
                     not_before: int = 0, not_after: int = 4102444800) -> bool:
        raise NotImplementedError

    def revoke_key(self, key_id: str, *, now: int) -> bool:
        raise NotImplementedError

    def expected_signature(self, key_id: str, name: str, version: str,
                           digest: str) -> str:
        raise NotImplementedError

    def admit(self, name: str, version: str, digest: str, signature: str, *,
              key_id: str, now: int, allow_rollback: bool = False) -> Admission:
        raise NotImplementedError

    def load(self, name: str, *, now: int) -> bool:
        raise NotImplementedError

    def unload(self, name: str, *, now: int) -> bool:
        raise NotImplementedError

    def measure(self, name: str, digest: str, *, now: int) -> bool:
        raise NotImplementedError

    def block(self, name: str, reason: str, *, now: int) -> bool:
        raise NotImplementedError

    def unblock(self, name: str) -> bool:
        raise NotImplementedError

    def retire(self, name: str) -> bool:
        raise NotImplementedError

    def module_of(self, name: str) -> ModuleRecord:
        raise NotImplementedError

    def loaded_modules(self) -> tuple[str, ...]:
        raise NotImplementedError

    def effective_manifest(self) -> tuple[ManifestEntry, ...]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
