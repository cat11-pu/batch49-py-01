"""Batch token introspection with versioned caller projections."""

from __future__ import annotations

import threading
from dataclasses import dataclass


TYPE_FIELDS = {
    "access": ("token_type", "client_id", "sub", "scope", "exp", "iat", "ttl"),
    "refresh": ("token_type", "client_id", "sub", "scope", "exp", "iat", "ttl",
                "family_id"),
    "id": ("token_type", "client_id", "sub", "exp", "iat", "ttl", "amr"),
}

DEFAULT_FIELDS = ("sub", "scope")


class IntrospectionError(ValueError):
    """Raised when a batch introspection call is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TokenRecord:
    token: str
    token_type: str
    client_id: str
    subject: str
    scope: str
    issued_at: int
    expires_at: int
    grace_until: int
    family_id: str | None
    amr: tuple[str, ...]


@dataclass(frozen=True)
class ProjectionPolicy:
    caller: str
    version: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class BatchResult:
    results: tuple
    count: int
    failed: int
    bytes_used: int


class IntrospectionBatchProjector:
    """Introspect token batches and project caller-authorised fields."""

    def __init__(self, *, max_items: int = 20, max_total_bytes: int = 4096,
                 max_token_length: int = 512, clock=None) -> None:
        self.max_items = max_items
        self.max_total_bytes = max_total_bytes
        self.max_token_length = max_token_length
        self._clock = clock
        self._lock = threading.RLock()

    def register_caller(self, caller, *, version=1) -> ProjectionPolicy:
        raise NotImplementedError

    def set_projection(self, caller, fields, *, version) -> ProjectionPolicy:
        raise NotImplementedError

    def projection_of(self, caller):
        raise NotImplementedError

    def register_token(self, token, *, token_type, client_id, subject, scope,
                       issued_at, expires_at, grace_until=0, family_id=None,
                       amr=()) -> TokenRecord:
        raise NotImplementedError

    def token_of(self, token):
        raise NotImplementedError

    def remove_token(self, token) -> bool:
        raise NotImplementedError

    def introspect_batch(self, caller, requests, *, now) -> BatchResult:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
