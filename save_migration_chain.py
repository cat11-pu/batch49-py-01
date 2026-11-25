"""Save schema migration chain: staged slots, per-step checksums, rollback."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass


class SaveError(ValueError):
    """Raised when a slot or migration operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SlotMeta:
    account: str
    slot_id: str
    version: int
    checksum: str
    status: str
    size: int


@dataclass(frozen=True)
class MigrationSummary:
    account: str
    slot_id: str
    from_version: int
    to_version: int
    steps: int


def canonical_bytes(payload: dict) -> bytes:
    """Encode the payload as sorted-key, whitespace-free UTF-8 JSON."""
    raise NotImplementedError


def payload_checksum(payload: dict) -> str:
    """Return the first 16 lowercase hex characters of SHA-256(canonical_bytes)."""
    raise NotImplementedError


class SaveMigrationChain:
    """Own per-account save slots plus the single-step migration registry."""

    def __init__(self, *, current_version: int, slots_per_account: int = 3) -> None:
        self.current_version = current_version
        self.slots_per_account = slots_per_account
        self._lock = threading.RLock()

    def register_migration(self, from_version: int, to_version: int, migrator) -> None:
        raise NotImplementedError

    def create_slot(self, account: str, slot_id: str, payload: dict, *,
                    version: int = 1, checksum: str | None = None) -> SlotMeta:
        raise NotImplementedError

    def plan_upgrade(self, account: str, slot_id: str) -> list[tuple[int, int]]:
        raise NotImplementedError

    def migrate_slot(self, account: str, slot_id: str, *,
                     target_version: int | None = None) -> SlotMeta:
        raise NotImplementedError

    def last_migration(self, account: str, slot_id: str) -> MigrationSummary | None:
        raise NotImplementedError

    def rollback_slot(self, account: str, slot_id: str) -> SlotMeta:
        raise NotImplementedError

    def verify_slot(self, account: str, slot_id: str) -> bool:
        raise NotImplementedError

    def list_slots(self, account: str) -> list[SlotMeta]:
        raise NotImplementedError

    def slot_payload(self, account: str, slot_id: str) -> dict:
        raise NotImplementedError

    def set_active_slot(self, account: str, slot_id: str) -> None:
        raise NotImplementedError

    def active_slot(self, account: str) -> str | None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
