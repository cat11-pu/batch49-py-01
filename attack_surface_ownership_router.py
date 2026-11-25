"""Route attack-surface asset ownership through tag, domain and network rules."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class RoutingError(ValueError):
    """Raised when a routing rule or ownership operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OwnershipRule:
    rule_id: str
    owner: str
    priority: int
    tag: str | None
    domain_suffix: str | None
    cidr: str | None
    specificity: int


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    tags: tuple[str, ...]
    domain: str | None
    ip: str | None


@dataclass(frozen=True)
class Assignment:
    asset_id: str
    owner: str | None
    rule_id: str | None
    locked: bool
    source: str
    assigned_at: str


@dataclass(frozen=True)
class UnownedEntry:
    asset_id: str
    since: str
    last_seen: str


@dataclass(frozen=True)
class Conflict:
    asset_id: str
    locked_owner: str
    rule_id: str
    rule_owner: str
    at: str


class AttackSurfaceOwnershipRouter:
    """Match assets to owners, keep an unowned queue and protect manual locks."""

    def __init__(self, *, max_rules: int = 200, max_assets: int = 5000,
                 unowned_retention_seconds: int = 2592000, clock=None) -> None:
        self.max_rules = max_rules
        self.max_assets = max_assets
        self.unowned_retention_seconds = unowned_retention_seconds
        self._clock = clock
        self._lock = threading.RLock()

    def add_rule(self, rule_id: str, owner: str, *, priority: int = 0,
                 tag: str | None = None, domain_suffix: str | None = None,
                 cidr: str | None = None,
                 now: str | None = None) -> OwnershipRule:
        raise NotImplementedError

    def remove_rule(self, rule_id: str) -> bool:
        raise NotImplementedError

    def register_asset(self, asset_id: str, *, tags=(), domain: str | None = None,
                       ip: str | None = None,
                       now: str | None = None) -> Assignment:
        raise NotImplementedError

    def update_asset(self, asset_id: str, *, tags=None, domain: str | None = None,
                     ip: str | None = None,
                     now: str | None = None) -> Assignment:
        raise NotImplementedError

    def resolve(self, asset_id: str) -> Assignment:
        raise NotImplementedError

    def claim_unowned(self, asset_id: str, owner: str,
                      now: str | None = None) -> Assignment:
        raise NotImplementedError

    def release_lock(self, asset_id: str, now: str | None = None) -> Assignment:
        raise NotImplementedError

    def recompute(self, now: str | None = None) -> tuple[Conflict, ...]:
        raise NotImplementedError

    def unowned(self, now: str | None = None) -> tuple[UnownedEntry, ...]:
        raise NotImplementedError

    def history(self, asset_id: str) -> list[Assignment]:
        raise NotImplementedError

    def conflicts(self) -> tuple[Conflict, ...]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
