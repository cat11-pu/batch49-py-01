"""Vulnerability exemption approval workflow with scoped applicability and versions."""

from __future__ import annotations

import threading
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 604800


class ExemptionError(ValueError):
    """Raised when an exemption operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    tags: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True)
class ExemptionRecord:
    exemption_id: str
    version: int
    rule_id: str
    asset_tags: tuple[str, ...]
    state: str
    requested_by: str
    approved_by: str | None
    created_at: str
    activated_at: str | None
    expires_at: str | None
    reason: str | None
    superseded_by: int | None


class VulnExemptionWorkflow:
    """Run the request, approval, activation, expiry and renewal state machine."""

    def __init__(self, *, default_ttl: int = DEFAULT_TTL_SECONDS,
                 max_live_exemptions: int = 100, clock=None) -> None:
        self.default_ttl = default_ttl
        self.max_live_exemptions = max_live_exemptions
        self._clock = clock
        self._lock = threading.RLock()

    def register_asset(self, asset_id: str, tags,
                       now: str | None = None) -> AssetRecord:
        raise NotImplementedError

    def update_asset_tags(self, asset_id: str, tags,
                          now: str | None = None) -> AssetRecord:
        raise NotImplementedError

    def request_exemption(self, exemption_id: str, rule_id: str, asset_tags,
                          requested_by: str, ttl_seconds: int | None = None,
                          now: str | None = None) -> ExemptionRecord:
        raise NotImplementedError

    def approve_exemption(self, exemption_id: str, approver: str,
                          now: str | None = None) -> ExemptionRecord:
        raise NotImplementedError

    def reject_exemption(self, exemption_id: str, approver: str, reason: str,
                         now: str | None = None) -> ExemptionRecord:
        raise NotImplementedError

    def activate_exemption(self, exemption_id: str,
                           now: str | None = None) -> ExemptionRecord:
        raise NotImplementedError

    def renew_exemption(self, exemption_id: str, ttl_seconds: int, approver: str,
                        now: str | None = None) -> ExemptionRecord:
        raise NotImplementedError

    def revoke_exemption(self, exemption_id: str, reason: str,
                         now: str | None = None) -> ExemptionRecord:
        raise NotImplementedError

    def expire_due(self, now: str | None = None) -> int:
        raise NotImplementedError

    def is_exempted(self, rule_id: str, asset_id: str,
                    now: str | None = None) -> bool:
        raise NotImplementedError

    def get_exemption(self, exemption_id: str) -> ExemptionRecord:
        raise NotImplementedError

    def versions(self, exemption_id: str) -> list[ExemptionRecord]:
        raise NotImplementedError

    def history(self, exemption_id: str) -> list[dict]:
        raise NotImplementedError

    def list_by_state(self, state: str) -> list[ExemptionRecord]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
