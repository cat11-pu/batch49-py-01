"""Signed rollback bundles: per-batch verification, isolation and idempotent replay."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading

_STAT_KEYS = {"requests", "batches", "executed", "undone", "retries",
              "quarantined"}


class RollbackError(ValueError):
    """Raised when a rollback submission or execution is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _signature_text(batch_id: str, assets: list) -> str:
    body = {"batch_id": batch_id, "assets": assets}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


class RollbackBundleGuard:
    """Execute signed rollback batches in order, isolating untrusted ones."""

    def __init__(self, undo, *, max_batches: int = 8,
                 max_assets_per_batch: int = 32,
                 max_attempts: int = 2) -> None:
        self._undo = undo
        self.max_batches = max_batches
        self.max_assets_per_batch = max_assets_per_batch
        self.max_attempts = max_attempts
        self._lock = threading.RLock()

    def trust_key(self, key_id: str, material: str) -> None:
        raise NotImplementedError

    def untrust_key(self, key_id: str) -> int:
        raise NotImplementedError

    def verify_batch(self, batch) -> dict:
        raise NotImplementedError

    def submit(self, bundle, *, request_id: str, now: int = 0) -> dict:
        raise NotImplementedError

    def execute(self, request_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def retry_batch(self, request_id: str, batch_id: str, *,
                    now: int = 0) -> dict:
        raise NotImplementedError

    def bundle_status(self, request_id: str) -> dict:
        raise NotImplementedError

    def history(self, request_id: str | None = None) -> tuple:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
