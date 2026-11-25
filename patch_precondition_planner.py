"""Patch precondition planning: base version gates, file conflicts, minimal blocking chains."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PatchPlanError(ValueError):
    """Raised when a patch plan request is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Patch:
    """A candidate patch: touched files, prerequisites, base version and hunks."""

    patch_id: str
    files: tuple = ()
    requires: tuple = ()
    base_version: str = ""
    hunks: tuple = ()


@dataclass(frozen=True)
class PlanResult:
    """Batches that can run in order, plus every patch that could not be scheduled."""

    asset_id: str
    batches: tuple = ()
    blocked: tuple = ()
    reasons: tuple = ()


class PatchPreconditionPlanner:
    """Turn candidate patches for one asset into ordered installation batches."""

    def __init__(self, *, max_batch_size: int = 8, max_patches: int = 512) -> None:
        self.max_batch_size = max_batch_size
        self.max_patches = max_patches
        self._lock = threading.RLock()

    def add_asset(self, asset_id: str, version: str) -> None:
        raise NotImplementedError

    def asset_version(self, asset_id: str) -> str:
        raise NotImplementedError

    def add_patch(self, patch: Patch) -> None:
        raise NotImplementedError

    def base_version_ok(self, asset_id: str, patch_id: str) -> bool:
        raise NotImplementedError

    def dependency_closure(self, patch_id: str) -> tuple:
        raise NotImplementedError

    def conflicts(self, left_id: str, right_id: str) -> tuple:
        raise NotImplementedError

    def plan(self, asset_id: str, patch_ids) -> PlanResult:
        raise NotImplementedError

    def minimum_blocking_set(self, asset_id: str, patch_ids) -> tuple:
        raise NotImplementedError

    def cycle_members(self, patch_ids=None) -> tuple:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
