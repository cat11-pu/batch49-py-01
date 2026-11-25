"""Password reset procedure: proof levels, cooldown, case merge and revocation."""

from __future__ import annotations

import threading
from dataclasses import dataclass

EVIDENCE_KINDS = ("email", "phone", "manual")
ACTIVE_STATUSES = ("collecting", "downgraded", "eligible", "approved")


class ResetError(ValueError):
    """Raised when a password reset operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ResetCase:
    """One password reset case for one account."""

    case_id: str
    account: str
    status: str
    level: int
    required_level: int
    evidence: tuple
    opened_at: int
    expires_at: int
    cooldown_until: int
    approved_until: int | None
    reviewer: str | None


@dataclass(frozen=True)
class ReviewResult:
    """Outcome of reviewing the evidence of one case."""

    status: str
    granted_level: int
    required_level: int
    missing: tuple


@dataclass(frozen=True)
class CompleteResult:
    """Outcome of completing a password reset."""

    status: str
    case_id: str
    sessions_invalidated: int
    notified_via: str


class PasswordResetProcedure:
    """Run password reset cases with proof levels and session revocation."""

    def __init__(self, *, required_level: int = 2, validity: int = 10,
                 cooldown: int = 6, approval_ttl: int = 4,
                 max_cases: int = 32, notification: str = "email") -> None:
        self.required_level = required_level
        self.validity = validity
        self.cooldown = cooldown
        self.approval_ttl = approval_ttl
        self.max_cases = max_cases
        self.notification = notification
        self._lock = threading.RLock()
        self._cases = {}
        self._sessions = {}
        self._sequence = 0
        self._counters = {
            "opened": 0,
            "evidence": 0,
            "reviews": 0,
            "approvals": 0,
            "completed": 0,
            "expired": 0,
            "sessions_invalidated": 0,
        }

    def open_case(self, account: str, *, now: int = 0) -> ResetCase:
        raise NotImplementedError

    def submit_evidence(self, account: str, kind: str, *,
                        now: int = 0) -> ResetCase:
        raise NotImplementedError

    def review(self, account: str, *, now: int = 0) -> ReviewResult:
        raise NotImplementedError

    def approve(self, account: str, reviewer: str, *,
                now: int = 0) -> ResetCase:
        raise NotImplementedError

    def complete(self, account: str, *, now: int = 0) -> CompleteResult:
        raise NotImplementedError

    def expire(self, now: int = 0) -> int:
        raise NotImplementedError

    def register_session(self, account: str, session_id: str) -> None:
        raise NotImplementedError

    def active_sessions(self, account: str) -> list[str]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
