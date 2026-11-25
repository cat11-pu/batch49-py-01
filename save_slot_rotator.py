"""Three-generation save slots: staged promotion, rotation, crash recovery."""

from __future__ import annotations

import copy
import json
import threading
import zlib
from dataclasses import dataclass


class SlotFault(ValueError):
    """Raised when a slot operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SlotMeta:
    account: str
    tier: str
    generation: int
    checksum: str
    size: int
    status: str


@dataclass(frozen=True)
class TempRecord:
    account: str
    temp_id: str
    checksum: str
    size: int
    complete: bool
    promotion_marked: bool


TIERS = ("active", "previous", "backup")


def slot_bytes(payload: dict) -> bytes:
    """Encode payload as sorted-key, whitespace-free UTF-8 JSON."""
    raise NotImplementedError


def slot_crc(payload: dict) -> str:
    """Return CRC32 of slot_bytes(payload) as 8 lowercase hex characters."""
    raise NotImplementedError


class SaveSlotRotator:
    """Keep active, previous and backup slots per account with staged writes."""

    def __init__(self, *, slot_limit: int = 3, max_slot_bytes: int = 65536) -> None:
        self.slot_limit = slot_limit
        self.max_slot_bytes = max_slot_bytes
        self._lock = threading.RLock()

    def stage(self, account: str, payload: dict, *, complete: bool = True,
              checksum: str | None = None) -> str:
        raise NotImplementedError

    def mark_promotion(self, account: str, temp_id: str) -> None:
        raise NotImplementedError

    def promote(self, account: str, temp_id: str) -> SlotMeta:
        raise NotImplementedError

    def save(self, account: str, payload: dict) -> SlotMeta:
        raise NotImplementedError

    def load(self, account: str, *, tier: str = "active") -> dict:
        raise NotImplementedError

    def rotate(self, account: str) -> dict:
        raise NotImplementedError

    def recover(self, account: str) -> dict:
        raise NotImplementedError

    def prune(self, account: str, *, keep_candidates: int = 0) -> int:
        raise NotImplementedError

    def pending(self, account: str) -> list[TempRecord]:
        raise NotImplementedError

    def slot_meta(self, account: str) -> dict[str, SlotMeta]:
        raise NotImplementedError

    def generation(self, account: str) -> int:
        raise NotImplementedError

    def adopt_slot(self, account: str, tier: str, payload: dict,
                   checksum: str, *, generation: int = 0) -> SlotMeta:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
