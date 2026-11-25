"""MFA 因子丢失恢复工单：等待期、双人复核与恢复期因子冻结。"""

from __future__ import annotations

from dataclasses import dataclass


class MfaRecoveryError(ValueError):
    """Raised when an MFA recovery operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class FactorCase:
    case_id: str
    account: str
    risk: str
    status: str
    required_reviews: int
    wait_until: int
    expires_at: int
    reviewers: tuple[str, ...]
    approvals: tuple[str, ...]
    rejections: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryReport:
    case_id: str
    account: str
    revoked_factors: tuple[str, ...]
    added_factors: tuple[str, ...]
    audit_from: int
    audit_to: int


class MfaRecoveryCaseflow:
    """Recovery cases with waiting period, reviewers and factor freezing."""

    def __init__(self, *, wait_low: int = 0, wait_high: int = 3600,
                 grace: int = 1800, max_new_factors: int = 5) -> None:
        self.wait_low = wait_low
        self.wait_high = wait_high
        self.grace = grace
        self.max_new_factors = max_new_factors

    def register_account(self, *, account: str, factors=(), now: int = 0) -> None:
        raise NotImplementedError

    def open_case(self, *, account: str, risk: str, now: int) -> FactorCase:
        raise NotImplementedError

    def assign_reviewers(self, *, case_id: str, reviewers, now: int) -> FactorCase:
        raise NotImplementedError

    def review(self, *, case_id: str, reviewer: str, approve: bool = True,
               now: int) -> FactorCase:
        raise NotImplementedError

    def freeze_factors(self, *, account: str, frozen: bool, now: int) -> bool:
        raise NotImplementedError

    def add_factor(self, *, account: str, factor: str, now: int) -> tuple[str, ...]:
        raise NotImplementedError

    def password_login(self, *, account: str) -> bool:
        raise NotImplementedError

    def record_self_recovery(self, *, account: str, factors, now: int) -> int:
        raise NotImplementedError

    def complete(self, *, case_id: str, new_factors, now: int) -> RecoveryReport:
        raise NotImplementedError

    def expire(self, now: int) -> tuple[str, ...]:
        raise NotImplementedError

    def get_case(self, case_id: str) -> FactorCase | None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
