"""Image signature admission: signature thresholds, revocation propagation, canary rollout."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ImageAdmissionError(ValueError):
    """Raised when an image admission operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PolicyView:
    repository: str
    signers: tuple[str, ...]
    threshold: int
    rollout_percent: int


@dataclass(frozen=True)
class SignerView:
    signer_id: str
    seq: int
    key_version: int
    revoked_at: int | None
    enforce_at: int | None
    enforced: bool


@dataclass(frozen=True)
class SignatureView:
    signer: str
    key_version: int
    at: int
    state: str


@dataclass(frozen=True)
class AdmissionDecision:
    namespace: str
    repository: str
    digest: str
    allowed: bool
    mode: str | None
    reason: str | None
    tainted: bool
    generation: int
    decided_at: int


class ImageSignatureAdmission:
    """Admit container images by signer threshold, revocation state and rollout slice."""

    def __init__(self, *, max_images: int = 64) -> None:
        self.max_images = max_images
        self._lock = threading.RLock()

    def register_signer(self, signer_id: str, *, key_version: int = 1) -> int:
        raise NotImplementedError

    def signer_of(self, signer_id: str) -> SignerView:
        raise NotImplementedError

    def rotate_signer(self, signer_id: str) -> int:
        raise NotImplementedError

    def revoke_signer(self, signer_id: str, *, at: int,
                      enforce_after: int = 30) -> bool:
        raise NotImplementedError

    def propagate(self, now: int) -> tuple[str, ...]:
        raise NotImplementedError

    def set_policy(self, repository: str, *, signers: tuple[str, ...],
                   threshold: int, rollout_percent: int = 0) -> PolicyView:
        raise NotImplementedError

    def policy_of(self, repository: str) -> PolicyView:
        raise NotImplementedError

    def submit_signature(self, repository: str, digest: str, signer: str, *,
                         key_version: int, at: int) -> bool:
        raise NotImplementedError

    def signatures_of(self, repository: str,
                      digest: str) -> tuple[SignatureView, ...]:
        raise NotImplementedError

    def threshold_state(self, repository: str, digest: str) -> dict:
        raise NotImplementedError

    def admit(self, namespace: str, repository: str, digest: str, *,
              now: int) -> AdmissionDecision:
        raise NotImplementedError

    def promote(self, repository: str, digest: str, *, now: int) -> bool:
        raise NotImplementedError

    def rollback(self, repository: str, *, now: int) -> str:
        raise NotImplementedError

    def stable_of(self, repository: str) -> str | None:
        raise NotImplementedError

    def history(self, repository: str) -> tuple[str, ...]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
