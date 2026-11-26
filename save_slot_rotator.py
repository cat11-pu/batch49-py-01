"""Three-generation save slots: staged promotion, rotation, crash recovery."""

from __future__ import annotations

import copy
import json
import threading
import uuid
import zlib
from dataclasses import dataclass, field


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
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return text.encode("utf-8")


def slot_crc(payload: dict) -> str:
    """Return CRC32 of slot_bytes(payload) as 8 lowercase hex characters."""
    return format(zlib.crc32(slot_bytes(payload)) & 0xFFFFFFFF, "08x")


@dataclass
class _Slot:
    tier: str
    generation: int
    checksum: str
    size: int
    status: str
    payload: dict


@dataclass
class _Temp:
    account: str
    temp_id: str
    checksum: str
    size: int
    complete: bool
    promotion_marked: bool
    payload: dict


@dataclass
class _Account:
    generation: int = 0
    slots: dict = field(default_factory=dict)
    temps: list = field(default_factory=list)


class SaveSlotRotator:
    """Keep active, previous and backup slots per account with staged writes."""

    def __init__(self, *, slot_limit: int = 3, max_slot_bytes: int = 65536) -> None:
        self.slot_limit = slot_limit
        self.max_slot_bytes = max_slot_bytes
        self._lock = threading.RLock()
        self._accounts: dict[str, _Account] = {}
        self._stats = {
            "saves": 0,
            "promotions": 0,
            "recoveries": 0,
            "pruned": 0,
            "corrupt_loads": 0,
        }

    def _ensure_account(self, account: str) -> _Account:
        state = self._accounts.get(account)
        if state is None:
            state = _Account()
            self._accounts[account] = state
        return state

    def _find_temp(self, state: _Account, temp_id: str) -> _Temp:
        for temp in state.temps:
            if temp.temp_id == temp_id:
                return temp
        raise SlotFault("SLOT_UNAVAILABLE",
                        {"temp_id": temp_id})

    def _meta(self, account: str, slot: _Slot) -> SlotMeta:
        return SlotMeta(
            account=account,
            tier=slot.tier,
            generation=slot.generation,
            checksum=slot.checksum,
            size=slot.size,
            status=slot.status,
        )

    def stage(self, account: str, payload: dict, *, complete: bool = True,
              checksum: str | None = None) -> str:
        data = slot_bytes(payload)
        size = len(data)
        if size > self.max_slot_bytes:
            raise SlotFault("SLOT_TOO_LARGE",
                            {"size": size, "limit": self.max_slot_bytes})
        with self._lock:
            state = self._ensure_account(account)
            if len(state.temps) >= self.slot_limit:
                raise SlotFault("SLOT_CAPACITY",
                                {"limit": self.slot_limit})
            actual = slot_crc(payload)
            temp_id = uuid.uuid4().hex
            state.temps.append(_Temp(
                account=account,
                temp_id=temp_id,
                checksum=checksum if checksum is not None else actual,
                size=size,
                complete=complete,
                promotion_marked=False,
                payload=copy.deepcopy(payload),
            ))
            return temp_id

    def mark_promotion(self, account: str, temp_id: str) -> None:
        with self._lock:
            state = self._ensure_account(account)
            temp = self._find_temp(state, temp_id)
            temp.promotion_marked = True

    def _apply_promotion(self, state: _Account, temp: _Temp) -> _Slot:
        state.generation += 1
        backup = state.slots.pop("previous", None)
        if backup is not None:
            backup.tier = "backup"
            state.slots["backup"] = backup
        previous = state.slots.pop("active", None)
        if previous is not None:
            previous.tier = "previous"
            state.slots["previous"] = previous
        slot = _Slot(
            tier="active",
            generation=state.generation,
            checksum=temp.checksum,
            size=temp.size,
            status="ok",
            payload=copy.deepcopy(temp.payload),
        )
        state.slots["active"] = slot
        state.temps.remove(temp)
        self._stats["promotions"] += 1
        return slot

    def promote(self, account: str, temp_id: str) -> SlotMeta:
        with self._lock:
            state = self._ensure_account(account)
            temp = self._find_temp(state, temp_id)
            if not temp.complete or not temp.promotion_marked:
                raise SlotFault("SLOT_NOT_READY",
                                {"temp_id": temp_id,
                                 "complete": temp.complete,
                                 "promotion_marked": temp.promotion_marked})
            if slot_crc(temp.payload) != temp.checksum:
                raise SlotFault("SLOT_CHECKSUM_MISMATCH",
                                {"temp_id": temp_id,
                                 "expected": slot_crc(temp.payload),
                                 "recorded": temp.checksum})
            slot = self._apply_promotion(state, temp)
            return self._meta(account, slot)

    def save(self, account: str, payload: dict) -> SlotMeta:
        data = slot_bytes(payload)
        size = len(data)
        if size > self.max_slot_bytes:
            raise SlotFault("SLOT_TOO_LARGE",
                            {"size": size, "limit": self.max_slot_bytes})
        with self._lock:
            state = self._ensure_account(account)
            if len(state.temps) >= self.slot_limit:
                raise SlotFault("SLOT_CAPACITY",
                                {"limit": self.slot_limit})
            checksum = slot_crc(payload)
            temp = _Temp(
                account=account,
                temp_id=uuid.uuid4().hex,
                checksum=checksum,
                size=size,
                complete=True,
                promotion_marked=False,
                payload=copy.deepcopy(payload),
            )
            state.temps.append(temp)
            temp.promotion_marked = True
            slot = self._apply_promotion(state, temp)
            self._stats["saves"] += 1
            return self._meta(account, slot)

    def load(self, account: str, *, tier: str = "active") -> dict:
        if tier not in TIERS:
            raise SlotFault("SLOT_INVALID_TIER", {"tier": tier})
        with self._lock:
            state = self._accounts.get(account)
            if state is None or tier not in state.slots:
                raise SlotFault("SLOT_UNAVAILABLE",
                                {"account": account, "tier": tier})
            slot = state.slots[tier]
            if slot_crc(slot.payload) != slot.checksum:
                slot.status = "corrupt"
                self._stats["corrupt_loads"] += 1
                raise SlotFault("SLOT_CORRUPT",
                                {"account": account, "tier": tier,
                                 "generation": slot.generation})
            if slot.status == "unverified":
                slot.status = "ok"
            return copy.deepcopy(slot.payload)

    def rotate(self, account: str) -> dict:
        with self._lock:
            state = self._ensure_account(account)
            backup = state.slots.pop("previous", None)
            if backup is not None:
                backup.tier = "backup"
                state.slots["backup"] = backup
            previous = state.slots.pop("active", None)
            if previous is not None:
                previous.tier = "previous"
                state.slots["previous"] = previous
            state.generation += 1
            return {tier: slot.generation
                    for tier, slot in state.slots.items()}

    def recover(self, account: str) -> dict:
        outcome = {"promoted": 0, "discarded": 0, "candidates": 0}
        with self._lock:
            state = self._ensure_account(account)
            for temp in list(state.temps):
                if not temp.complete:
                    state.temps.remove(temp)
                    outcome["discarded"] += 1
                    continue
                if not temp.promotion_marked:
                    outcome["candidates"] += 1
                    continue
                if slot_crc(temp.payload) != temp.checksum:
                    state.temps.remove(temp)
                    outcome["discarded"] += 1
                    continue
                self._apply_promotion(state, temp)
                outcome["promoted"] += 1
            self._stats["recoveries"] += 1
            return outcome

    def prune(self, account: str, *, keep_candidates: int = 0) -> int:
        with self._lock:
            state = self._ensure_account(account)
            keep = max(keep_candidates, 0)
            if len(state.temps) <= keep:
                return 0
            removed = state.temps[:len(state.temps) - keep]
            state.temps = state.temps[len(state.temps) - keep:]
            count = len(removed)
            self._stats["pruned"] += count
            return count

    def pending(self, account: str) -> list[TempRecord]:
        with self._lock:
            state = self._accounts.get(account)
            if state is None:
                return []
            return [
                TempRecord(
                    account=temp.account,
                    temp_id=temp.temp_id,
                    checksum=temp.checksum,
                    size=temp.size,
                    complete=temp.complete,
                    promotion_marked=temp.promotion_marked,
                )
                for temp in state.temps
            ]

    def slot_meta(self, account: str) -> dict[str, SlotMeta]:
        with self._lock:
            state = self._accounts.get(account)
            if state is None:
                return {}
            return {tier: self._meta(account, slot)
                    for tier, slot in state.slots.items()}

    def generation(self, account: str) -> int:
        with self._lock:
            state = self._accounts.get(account)
            return state.generation if state is not None else 0

    def adopt_slot(self, account: str, tier: str, payload: dict,
                   checksum: str, *, generation: int = 0) -> SlotMeta:
        if tier not in TIERS:
            raise SlotFault("SLOT_INVALID_TIER", {"tier": tier})
        data = slot_bytes(payload)
        with self._lock:
            state = self._ensure_account(account)
            slot = _Slot(
                tier=tier,
                generation=generation,
                checksum=checksum,
                size=len(data),
                status="unverified",
                payload=copy.deepcopy(payload),
            )
            state.slots[tier] = slot
            return self._meta(account, slot)

    def _dump_account(self, state: _Account) -> dict:
        return {
            "generation": state.generation,
            "slots": {
                tier: {
                    "tier": slot.tier,
                    "generation": slot.generation,
                    "checksum": slot.checksum,
                    "size": slot.size,
                    "status": slot.status,
                    "payload": copy.deepcopy(slot.payload),
                }
                for tier, slot in state.slots.items()
            },
            "temps": [
                {
                    "account": temp.account,
                    "temp_id": temp.temp_id,
                    "checksum": temp.checksum,
                    "size": temp.size,
                    "complete": temp.complete,
                    "promotion_marked": temp.promotion_marked,
                    "payload": copy.deepcopy(temp.payload),
                }
                for temp in state.temps
            ],
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "accounts": {
                    account: self._dump_account(state)
                    for account, state in self._accounts.items()
                },
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            accounts = self._validate_restore_state(state)
            rebuilt: dict[str, _Account] = {}
            for account, record in accounts.items():
                account_state = _Account(generation=record["generation"])
                for tier, slot_record in record["slots"].items():
                    account_state.slots[tier] = _Slot(
                        tier=slot_record["tier"],
                        generation=slot_record["generation"],
                        checksum=slot_record["checksum"],
                        size=slot_record["size"],
                        status=slot_record["status"],
                        payload=copy.deepcopy(slot_record["payload"]),
                    )
                for temp_record in record["temps"]:
                    account_state.temps.append(_Temp(
                        account=temp_record["account"],
                        temp_id=temp_record["temp_id"],
                        checksum=temp_record["checksum"],
                        size=temp_record["size"],
                        complete=temp_record["complete"],
                        promotion_marked=temp_record["promotion_marked"],
                        payload=copy.deepcopy(temp_record["payload"]),
                    ))
                rebuilt[account] = account_state
            self._accounts = rebuilt

    def _validate_restore_state(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise SlotFault("SLOT_INVALID_STATE", {"reason": "not a mapping"})
        accounts = state.get("accounts")
        if not isinstance(accounts, dict):
            raise SlotFault("SLOT_INVALID_STATE",
                            {"reason": "missing accounts"})
        for account, record in accounts.items():
            if not isinstance(account, str) or not isinstance(record, dict):
                raise SlotFault("SLOT_INVALID_STATE",
                                {"reason": "bad account record"})
            if not isinstance(record.get("generation"), int):
                raise SlotFault("SLOT_INVALID_STATE",
                                {"reason": "bad generation"})
            slots = record.get("slots")
            temps = record.get("temps")
            if not isinstance(slots, dict) or not isinstance(temps, list):
                raise SlotFault("SLOT_INVALID_STATE",
                                {"reason": "bad slots or temps"})
            for tier, slot_record in slots.items():
                if tier not in TIERS or not isinstance(slot_record, dict):
                    raise SlotFault("SLOT_INVALID_STATE",
                                    {"reason": "bad slot tier"})
                if not isinstance(slot_record.get("payload"), dict):
                    raise SlotFault("SLOT_INVALID_STATE",
                                    {"reason": "bad slot payload"})
                for key in ("tier", "generation", "checksum", "size",
                            "status"):
                    if key not in slot_record:
                        raise SlotFault("SLOT_INVALID_STATE",
                                        {"reason": f"missing slot {key}"})
            for temp_record in temps:
                if not isinstance(temp_record, dict):
                    raise SlotFault("SLOT_INVALID_STATE",
                                    {"reason": "bad temp record"})
                for key in ("account", "temp_id", "checksum", "size",
                            "complete", "promotion_marked", "payload"):
                    if key not in temp_record:
                        raise SlotFault("SLOT_INVALID_STATE",
                                        {"reason": f"missing temp {key}"})
                if not isinstance(temp_record["payload"], dict):
                    raise SlotFault("SLOT_INVALID_STATE",
                                    {"reason": "bad temp payload"})
        return accounts

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)
