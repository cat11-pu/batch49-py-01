"""SSO global logout ticket broker: per-RP delivery, retry ledger and expiry."""

from __future__ import annotations

from dataclasses import dataclass


class SsoLogoutError(ValueError):
    """Raised when a logout ticket operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LogoutTicket:
    ticket_id: str
    user: str
    session_id: str
    rps: tuple[str, ...]
    status: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class DeliveryResult:
    ticket_id: str
    rp: str
    status: str
    attempts: int
    next_attempt_at: int | None
    reason: str | None


@dataclass(frozen=True)
class PendingDelivery:
    ticket_id: str
    rp: str
    attempts: int
    next_attempt_at: int
    last_reason: str | None


class SsoLogoutTicketBroker:
    """Fan out one-time logout tickets to relying parties with retries."""

    def __init__(self, *, max_attempts: int = 3, base_backoff: int = 30,
                 max_queue_per_rp: int = 8, default_ttl: int = 600) -> None:
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_queue_per_rp = max_queue_per_rp
        self.default_ttl = default_ttl

    def issue_ticket(self, *, user: str, session_id: str, rps,
                     now: int, ttl: int | None = None) -> LogoutTicket:
        raise NotImplementedError

    def deliver(self, *, ticket_id: str, rp: str, now: int, success: bool,
                reason: str | None = None) -> DeliveryResult:
        raise NotImplementedError

    def retry_pending(self, now: int) -> tuple[PendingDelivery, ...]:
        raise NotImplementedError

    def pending(self, rp: str | None = None) -> tuple[PendingDelivery, ...]:
        raise NotImplementedError

    def ack(self, *, ticket_id: str, rp: str, now: int) -> bool:
        raise NotImplementedError

    def expire(self, now: int) -> tuple[str, ...]:
        raise NotImplementedError

    def get_ticket(self, ticket_id: str) -> LogoutTicket | None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
