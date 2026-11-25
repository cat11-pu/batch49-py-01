"""Signed WAF rule releases with gray shards and atomic rollback."""

from __future__ import annotations

GENESIS_DIGEST = "0" * 64
SHARD_COUNT = 20


class WafRuleReleaseError(ValueError):
    """Raised when a WAF rule release operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class WafRuleReleaseGuard:
    """Coordinate signed rule releases across fixed shards."""

    SHARD_COUNT = SHARD_COUNT

    def __init__(self, *, max_rules: int = 64, max_batches: int = 6,
                 max_pins: int = 8) -> None:
        self.max_rules = max_rules
        self.max_batches = max_batches
        self.max_pins = max_pins

    def trust_key(self, key_id: str, material: str) -> str:
        raise NotImplementedError

    def untrust_key(self, key_id: str) -> bool:
        raise NotImplementedError

    def verify_release(self, release: dict) -> dict:
        raise NotImplementedError

    def submit(self, release: dict, *, request_id: str, now: int = 0) -> dict:
        raise NotImplementedError

    def promote(self, request_id: str, *, percent: int, now: int = 0) -> dict:
        raise NotImplementedError

    def commit(self, request_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def rollback(self, request_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def shard_ruleset(self, shard_id: int) -> dict:
        raise NotImplementedError

    def active_ruleset(self) -> dict:
        raise NotImplementedError

    def release_status(self, request_id: str) -> dict:
        raise NotImplementedError

    def pin(self, *, now: int = 0) -> dict:
        raise NotImplementedError

    def unpin(self, token: str) -> bool:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
