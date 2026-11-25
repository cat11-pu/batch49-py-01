"""Biometric unlock policy: template generations, liveness thresholds, fallbacks."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class BiometricError(ValueError):
    """Raised when a biometric policy operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TemplateRecord:
    """One biometric template enrolled for a device."""

    template_id: str
    device: str
    generation: int
    enrolled_at: int
    status: str


@dataclass(frozen=True)
class DevicePolicy:
    """Per-device unlock policy."""

    device: str
    biometric_enabled: bool
    liveness_threshold: float
    password_fallback_limit: int


@dataclass(frozen=True)
class UnlockResult:
    """Outcome of one unlock or password fallback attempt."""

    status: str
    reason: str
    generation: int
    failures: int
    cooldown_until: int | None


class BiometricUnlockPolicy:
    """Track template generations and unlock decisions per device."""

    def __init__(self, *, liveness_threshold: float = 0.80,
                 failure_limit: int = 3, cooldown: int = 5,
                 compat_window: int = 10, max_templates: int = 8,
                 password_fallback_limit: int = 2) -> None:
        self.liveness_threshold = liveness_threshold
        self.failure_limit = failure_limit
        self.cooldown = cooldown
        self.compat_window = compat_window
        self.max_templates = max_templates
        self.password_fallback_limit = password_fallback_limit
        self._lock = threading.RLock()
        self._devices = {}
        self._templates = []
        self._thresholds = {}
        self._counters = {
            "enrollments": 0,
            "upgrades": 0,
            "unlocks": 0,
            "fallbacks": 0,
            "failures": 0,
            "cooldowns": 0,
            "password_fallbacks": 0,
        }

    def enroll(self, device: str, template_id: str, *,
               liveness_threshold: float | None = None,
               now: int = 0) -> TemplateRecord:
        raise NotImplementedError

    def upgrade_template(self, device: str, *, liveness_threshold: float,
                         now: int = 0) -> int:
        raise NotImplementedError

    def unlock(self, device: str, template_id: str, liveness_score: float, *,
               now: int = 0) -> UnlockResult:
        raise NotImplementedError

    def fallback_password(self, device: str, password_ok: bool, *,
                          now: int = 0) -> UnlockResult:
        raise NotImplementedError

    def set_device_policy(self, device: str, *,
                          biometric_enabled: bool | None = None,
                          liveness_threshold: float | None = None,
                          password_fallback_limit: int | None = None
                          ) -> DevicePolicy:
        raise NotImplementedError

    def device_policy(self, device: str) -> DevicePolicy:
        raise NotImplementedError

    def list_templates(self, device: str) -> list[TemplateRecord]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
