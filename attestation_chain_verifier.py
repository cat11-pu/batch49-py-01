"""Attestation envelope chains: ring anchoring, key rotation handover, break localization."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class AttestationError(ValueError):
    """Raised when a chain or trust configuration is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    owner: str
    secret: str
    not_before: int
    not_after: int
    rotated_from: str | None


@dataclass(frozen=True)
class Envelope:
    envelope_id: str
    seq: int
    issuer: str
    subject: str
    key_id: str
    signed_at: int
    payload_sha: str
    sig: str


@dataclass(frozen=True)
class RingResult:
    index: int
    envelope_id: str
    key_id: str
    issuer: str
    subject: str
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class BreakReport:
    index: int
    envelope_id: str
    reason: str


@dataclass(frozen=True)
class ChainVerdict:
    ok: bool
    rings: tuple[RingResult, ...]
    break_report: BreakReport | None
    root_version: int
    verified_at: int


def sign_envelope(payload_sha: str, key_id: str, secret: str) -> str:
    raise NotImplementedError


class AttestationChainVerifier:
    """Verify multi-ring attestation chains against a versioned trust root set."""

    def __init__(self, *, overlap_seconds: int = 300, max_chain: int = 32) -> None:
        self.overlap_seconds = overlap_seconds
        self.max_chain = max_chain
        self._lock = threading.RLock()
        self._root_version = 0
        self._keys: dict[str, KeyRecord] = {}
        self._history: list[ChainVerdict] = []
        self._stats = {"verifications": 0, "verified": 0, "rejected": 0,
                       "rings": 0}

    def add_root(self, key_id: str, *, owner: str, secret: str,
                 not_before: int = 0, not_after: int = 9999999999) -> None:
        raise NotImplementedError

    def rotate_key(self, old_key_id: str, new_key_id: str, *, at: int,
                   secret: str) -> None:
        raise NotImplementedError

    def active_keys(self, at: int) -> list[str]:
        raise NotImplementedError

    def verify_envelope(self, envelope: Envelope, *, at: int) -> RingResult:
        raise NotImplementedError

    def verify(self, chain: list[Envelope], *, at: int) -> ChainVerdict:
        raise NotImplementedError

    def locate_break(self, chain: list[Envelope], *, at: int) -> BreakReport | None:
        raise NotImplementedError

    def root_version(self) -> int:
        raise NotImplementedError

    def history(self) -> tuple[ChainVerdict, ...]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
