"""Vulnerability remediation SLA clock with freeze windows, exemptions and escalation."""

from __future__ import annotations

import threading
from dataclasses import dataclass

DEFAULT_SLA_SECONDS = {
    "critical": 86400,
    "high": 259200,
    "medium": 604800,
    "low": 1209600,
}


class SlaError(ValueError):
    """Raised when an SLA operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class FreezeWindow:
    asset_id: str
    start: str
    end: str
    seconds: int


@dataclass(frozen=True)
class Exemption:
    vuln_id: str
    granted_at: str
    until: str
    revoked_at: str | None


@dataclass(frozen=True)
class VulnRecord:
    vuln_id: str
    asset_id: str
    severity: str
    status: str
    confirmed_at: str
    due_at: str
    escalation_level: int
    exemption_until: str | None
    freeze_seconds: int


@dataclass(frozen=True)
class EscalationEvent:
    vuln_id: str
    level: int
    at: str
    due_at: str
    reason: str


class VulnSlaEscalator:
    """Track per-vulnerability remediation deadlines and escalation history."""

    def __init__(self, *, sla_seconds=None, escalate_step: int = 86400,
                 max_level: int = 3, max_vulnerabilities: int = 200,
                 max_freeze_windows_per_asset: int = 64, clock=None) -> None:
        self.sla_seconds = dict(DEFAULT_SLA_SECONDS)
        if sla_seconds:
            self.sla_seconds.update(sla_seconds)
        self.escalate_step = escalate_step
        self.max_level = max_level
        self.max_vulnerabilities = max_vulnerabilities
        self.max_freeze_windows_per_asset = max_freeze_windows_per_asset
        self._clock = clock
        self._lock = threading.RLock()

    def open_vulnerability(self, vuln_id: str, asset_id: str, severity: str,
                           confirmed_at: str, now: str | None = None) -> VulnRecord:
        raise NotImplementedError

    def add_freeze_window(self, asset_id: str, start: str, end: str) -> int:
        raise NotImplementedError

    def grant_exemption(self, vuln_id: str, until: str,
                        now: str | None = None) -> Exemption:
        raise NotImplementedError

    def revoke_exemption(self, vuln_id: str, now: str | None = None) -> bool:
        raise NotImplementedError

    def close_vulnerability(self, vuln_id: str, resolution: str,
                            now: str | None = None) -> VulnRecord:
        raise NotImplementedError

    def reopen_vulnerability(self, vuln_id: str,
                             now: str | None = None) -> VulnRecord:
        raise NotImplementedError

    def evaluate(self, now: str | None = None) -> list[EscalationEvent]:
        raise NotImplementedError

    def get_vulnerability(self, vuln_id: str) -> VulnRecord:
        raise NotImplementedError

    def escalation_history(self, vuln_id: str | None = None) -> list[EscalationEvent]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
