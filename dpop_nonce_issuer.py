"""DPoP server nonce issuance with generations, jkt binding and key migration."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class DpopError(ValueError):
    """Raised when a DPoP operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Nonce:
    value: str
    client: str
    generation: int
    expires_at: int
    consumed: bool


@dataclass(frozen=True)
class Binding:
    token_id: str
    client: str
    current_jkt: str
    previous_jkt: str | None
    grace_until: int | None
    bound_at: int


@dataclass(frozen=True)
class ProofResult:
    status: str
    nonce: str | None
    generation: int | None


class DpopNonceIssuer:
    """Per-client nonce table plus token-to-key bindings."""

    def __init__(self, *, nonce_capacity: int = 256, nonce_ttl_ms: int = 60000,
                 migration_grace_ms: int = 300000, clock=None) -> None:
        self.nonce_capacity = nonce_capacity
        self.nonce_ttl_ms = nonce_ttl_ms
        self.migration_grace_ms = migration_grace_ms
        self._clock = clock
        self._lock = threading.RLock()

    def register_client(self, client, *, now=None) -> int:
        raise NotImplementedError

    def rotate_generation(self, client, *, now) -> int:
        raise NotImplementedError

    def issue_nonce(self, client, *, now) -> Nonce:
        raise NotImplementedError

    def verify_proof(self, client, token_id, *, nonce, jkt, now) -> ProofResult:
        raise NotImplementedError

    def bind_token(self, client, token_id, *, jkt, now) -> Binding:
        raise NotImplementedError

    def migrate_key(self, client, token_id, *, new_jkt, now) -> Binding:
        raise NotImplementedError

    def expire(self, now) -> int:
        raise NotImplementedError

    def binding_of(self, token_id):
        raise NotImplementedError

    def nonce_of(self, value):
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
