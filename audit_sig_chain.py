"""Append-only audit log with chained HMAC signatures, truncation checks and archives."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading

_GENESIS = "0" * 64


class AuditChainError(ValueError):
    """Raised when an audit chain operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _digest(payload) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _sign(seq, key_id, actor, action, payload_digest, now, prev_sig,
          material) -> str:
    message = f"{seq}\n{key_id}\n{actor}\n{action}\n{payload_digest}\n{now}\n{prev_sig}"
    return hmac.new(material.encode(), message.encode(),
                    hashlib.sha256).hexdigest()


class AuditSignatureChain:
    """Sign each audit entry against the previous signature and guard archives."""

    def __init__(self, *, store=None, keys=None, active_key: str = "k1",
                 max_entries: int = 4096, segment_size: int = 8) -> None:
        self._store = store if store is not None else []
        self._keys = dict(keys) if keys else {"k1": "audit-default-key"}
        self.active_key = active_key
        self.max_entries = max_entries
        self.segment_size = segment_size
        self._lock = threading.RLock()

    def append(self, actor: str, action: str, payload, *, now: int = 0) -> dict:
        raise NotImplementedError

    def entry(self, seq: int) -> dict:
        raise NotImplementedError

    def head(self) -> dict:
        raise NotImplementedError

    def verify(self, *, start: int = 1, end: int | None = None,
               expected_entries: int | None = None) -> dict:
        raise NotImplementedError

    def rotate_key(self, key_id: str, material: str, *,
                   make_active: bool = True) -> int:
        raise NotImplementedError

    def seal_archive(self, name: str, *, upto: int | None = None) -> dict:
        raise NotImplementedError

    def verify_archive(self, name: str, *, live=None) -> dict:
        raise NotImplementedError

    def detect_truncation(self, *, expected_entries: int | None = None) -> dict:
        raise NotImplementedError

    def confirm_head(self, count: int, sig: str | None = None) -> None:
        raise NotImplementedError

    def archives(self) -> list:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
