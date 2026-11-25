"""Damage reflection and redirection routing with caps and original-source attribution."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ReflectRoutingError(ValueError):
    """Raised when a reflection or redirection operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ReflectSpec:
    owner_id: str
    ratio: float
    single_cap: int
    frame_cap: int


@dataclass(frozen=True)
class RedirectSpec:
    owner_id: str
    target_id: str
    priority: int


@dataclass(frozen=True)
class RoutedHit:
    hit_id: str
    source_id: str
    original_target: str
    final_target: str
    amount: int
    reflected_to: str | None
    reflected: int
    dropped: int
    chain: tuple
    frame: int


class DamageReflectRouter:
    """Route incoming hits through redirect chains and reflection caps."""

    def __init__(self, *, default_frame_cap: int = 1000000) -> None:
        self.default_frame_cap = default_frame_cap
        self._lock = threading.RLock()

    def register_target(self, target_id: str) -> None:
        raise NotImplementedError

    def register_reflect(self, owner_id: str, ratio: float, *,
                         single_cap: int, frame_cap: int | None = None) -> None:
        raise NotImplementedError

    def unregister_reflect(self, owner_id: str) -> bool:
        raise NotImplementedError

    def register_redirect(self, owner_id: str, target_id: str, *,
                          priority: int = 0) -> None:
        raise NotImplementedError

    def unregister_redirect(self, owner_id: str, target_id: str | None = None) -> int:
        raise NotImplementedError

    def route(self, hit_id: str, source_id: str, target_id: str, amount: int, *,
              frame: int = 0) -> RoutedHit:
        raise NotImplementedError

    def settle(self, hit_id: str) -> RoutedHit:
        raise NotImplementedError

    def attribution(self, hit_id: str) -> dict:
        raise NotImplementedError

    def ledger(self, *, frame: int | None = None) -> list:
        raise NotImplementedError

    def frame_total(self, owner_id: str, frame: int) -> int:
        raise NotImplementedError

    def reset_frame(self, frame: int) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
