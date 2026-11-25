"""SCIM group PATCH member semantics with paged reconciliation."""

from __future__ import annotations

from dataclasses import dataclass


class ScimPatchError(ValueError):
    """Raised when a SCIM group operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GroupView:
    group_id: str
    version: int
    members: tuple[str, ...]
    nested: tuple[str, ...]


@dataclass(frozen=True)
class PatchResult:
    group_id: str
    version: int
    changed: bool
    added: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True)
class ReconcileSession:
    token: str
    group_id: str
    start_version: int
    pages: tuple[int, ...]


@dataclass(frozen=True)
class ReconcileResult:
    group_id: str
    version: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    pages: int


class ScimGroupPatchReconciler:
    """Apply SCIM member patches and reconcile paged source snapshots."""

    def __init__(self, *, max_members: int = 100, max_depth: int = 3,
                 max_pages: int = 50) -> None:
        self.max_members = max_members
        self.max_depth = max_depth
        self.max_pages = max_pages

    def create_group(self, group_id: str) -> GroupView:
        raise NotImplementedError

    def apply_patch(self, *, group_id: str,
                    operations) -> PatchResult:
        raise NotImplementedError

    def get_group(self, group_id: str) -> GroupView | None:
        raise NotImplementedError

    def list_members(self, group_id: str, *,
                     expand: bool = False) -> tuple[str, ...]:
        raise NotImplementedError

    def begin_reconcile(self, group_id: str) -> ReconcileSession:
        raise NotImplementedError

    def reconcile_page(self, *, token: str, page: int,
                       members) -> int:
        raise NotImplementedError

    def finish_reconcile(self, token: str) -> ReconcileResult:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
