"""OTP delivery broker: channel routing, quota windows, batches and verification."""

from __future__ import annotations

import threading
from dataclasses import dataclass

CHANNELS = ("email", "sms")
PURPOSES = ("login", "register", "reset")


class OtpError(ValueError):
    """Raised when an OTP delivery operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DeliveryBatch:
    """One delivered verification code batch."""

    batch_id: str
    target: str
    tenant: str
    purpose: str
    channel: str
    code: str
    created_at: int
    expires_at: int
    attempts_left: int
    status: str
    fallback_reason: str | None


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of verifying one delivered code."""

    status: str
    batch_id: str
    attempts_left: int
    reason: str


class OtpDeliveryBroker:
    """Route verification codes across channels under per-target quotas."""

    def __init__(self, *, window: int = 10, target_limit: int = 3,
                 tenant_limit: int = 6, channel_limit: int = 5,
                 max_batches: int = 32, code_ttl: int = 5,
                 max_attempts: int = 3, suppression: int = 3) -> None:
        self.window = window
        self.target_limit = target_limit
        self.tenant_limit = tenant_limit
        self.channel_limit = channel_limit
        self.max_batches = max_batches
        self.code_ttl = code_ttl
        self.max_attempts = max_attempts
        self.suppression = suppression
        self._lock = threading.RLock()
        self._batches = []
        self._channels = {channel: {"enabled": True, "sent": 0, "failed": 0}
                          for channel in CHANNELS}
        self._sequence = 0
        self._quotas = {"target": {}, "tenant": {}, "channel": {}}
        self._counters = {
            "requested": 0,
            "reused": 0,
            "verified": 0,
            "mismatches": 0,
            "expired": 0,
            "resends": 0,
            "purged": 0,
        }

    def set_channel_enabled(self, channel: str, enabled: bool) -> None:
        raise NotImplementedError

    def request_code(self, target: str, *, tenant: str = "default",
                     purpose: str = "login",
                     preferred_channel: str = "email",
                     now: int = 0) -> DeliveryBatch:
        raise NotImplementedError

    def verify_code(self, target: str, code: str, *,
                    purpose: str = "login",
                    now: int = 0) -> VerifyResult:
        raise NotImplementedError

    def resend(self, target: str, *, purpose: str = "login",
               now: int = 0) -> DeliveryBatch:
        raise NotImplementedError

    def channel_status(self) -> dict:
        raise NotImplementedError

    def target_usage(self, target: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def purge_expired(self, now: int = 0) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
