"""Inbound gRPC metadata normalization, authorization and outbound filtering."""

from __future__ import annotations

import base64
import copy
import re
import threading


class GrpcMetadataError(ValueError):
    """Raised when metadata or a call is refused by the gate."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class GrpcMetadataGate:
    """Sanitize metadata, enforce per-method policy and build outbound headers."""

    def __init__(self, token_verifier=None, *, max_pairs: int = 32,
                 max_key_bytes: int = 64, max_value_bytes: int = 1024,
                 max_methods: int = 64, trusted_peers=(),
                 propagate_keys=("x-request-id", "traceparent", "x-tenant")) -> None:
        self.token_verifier = token_verifier
        self.max_pairs = max_pairs
        self.max_key_bytes = max_key_bytes
        self.max_value_bytes = max_value_bytes
        self.max_methods = max_methods
        self.trusted_peers = frozenset(trusted_peers)
        self.propagate_keys = tuple(propagate_keys)

    def register_method(self, method: str, *, audiences, roles=("user",),
                        max_deadline_ms: int = 30000, required_headers=()) -> dict:
        raise NotImplementedError

    def set_trusted_peers(self, peers) -> int:
        raise NotImplementedError

    def methods(self) -> list:
        raise NotImplementedError

    def revoke_method(self, method: str) -> bool:
        raise NotImplementedError

    def policy(self, method: str) -> dict:
        raise NotImplementedError

    def sanitize(self, metadata) -> dict:
        raise NotImplementedError

    def authorize(self, method: str, metadata, *, peer: str, now: int = 0) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
