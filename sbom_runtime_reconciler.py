"""Reconcile SBOM declarations against libraries loaded by running processes."""

from __future__ import annotations

from dataclasses import dataclass


class ReconcilerError(ValueError):
    """Raised when reconciliation input or state is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    linkage: str = "dynamic"


@dataclass(frozen=True)
class LoadedLibrary:
    soname: str
    version: str | None
    path: str
    pid: int
    service: str = "unknown"


@dataclass(frozen=True)
class Difference:
    kind: str
    component: str
    detail: str
    evidence: tuple
    pids: tuple
    services: tuple


class SbomRuntimeReconciler:
    """Compare declared components with runtime loaded libraries."""

    def __init__(self, *, max_libraries: int = 5000, equivalence_groups=None) -> None:
        self.max_libraries = max_libraries
        self._groups = equivalence_groups

    def load_sbom(self, components) -> int:
        raise NotImplementedError

    def load_runtime(self, libraries) -> int:
        raise NotImplementedError

    def add_equivalence_group(self, names) -> None:
        raise NotImplementedError

    def normalize_key(self, raw) -> str:
        raise NotImplementedError

    def normalize_version(self, raw) -> str:
        raise NotImplementedError

    def reconcile(self) -> list:
        raise NotImplementedError

    def findings(self, kind=None) -> list:
        raise NotImplementedError

    def summary(self) -> dict:
        raise NotImplementedError

    def evidence_for(self, component) -> list:
        raise NotImplementedError

    def service_index(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
