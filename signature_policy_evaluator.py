"""Signature policy tree: layered tightening, key state, versioned conclusion cache."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PolicyError(ValueError):
    """Raised when a policy or key operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CompiledPolicy:
    threshold: int
    roles: tuple[str, ...]
    usages: tuple[str, ...]
    version: int
    levels: tuple[str, ...]


@dataclass(frozen=True)
class KeyInfo:
    key_id: str
    roles: tuple[str, ...]
    usages: tuple[str, ...]
    not_before: int
    not_after: int
    revoked_at: int | None


@dataclass(frozen=True)
class Signature:
    key_id: str
    role: str
    usage: str
    signed_at: int


@dataclass(frozen=True)
class Evaluation:
    satisfied: bool
    effective: int
    threshold: int
    policy_version: int
    key_status_version: int
    unmet: tuple[str, ...]
    rejected: tuple[str, ...]


class SignaturePolicyEvaluator:
    """Compile layered signature policies and judge signature sets against them."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._version = 0
        self._key_status_version = 0
        self._global: dict | None = None
        self._orgs: dict[str, dict] = {}
        self._namespaces: dict[tuple[str, str], dict] = {}
        self._keys: dict[str, KeyInfo] = {}
        self._cache: dict[object, Evaluation] = {}
        self._stats = {"policy_sets": 0, "keys_registered": 0,
                       "evaluations": 0, "cache_hits": 0}

    def set_global_policy(self, policy: dict) -> CompiledPolicy:
        raise NotImplementedError

    def set_org_policy(self, org: str, policy: dict) -> CompiledPolicy:
        raise NotImplementedError

    def set_namespace_policy(self, org: str, namespace: str,
                             policy: dict) -> CompiledPolicy:
        raise NotImplementedError

    def compile(self, org: str, namespace: str) -> CompiledPolicy:
        raise NotImplementedError

    def policy_version(self, org: str, namespace: str) -> int:
        raise NotImplementedError

    def register_key(self, key_id: str, *, roles: tuple[str, ...] = (),
                     usages: tuple[str, ...] = (), not_before: int = 0,
                     not_after: int = 9999999999) -> None:
        raise NotImplementedError

    def revoke_key(self, key_id: str, *, at: int) -> None:
        raise NotImplementedError

    def key_status_version(self) -> int:
        raise NotImplementedError

    def evaluate(self, org: str, namespace: str, signatures: list[Signature],
                 *, at: int) -> Evaluation:
        raise NotImplementedError

    def cache_size(self) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
