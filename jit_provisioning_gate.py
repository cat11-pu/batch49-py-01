"""JIT provisioning gate: domain ownership, attribute mapping and merge cooldown."""

from __future__ import annotations

from dataclasses import dataclass


class JitProvisionError(ValueError):
    """Raised when a provisioning operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TenantDomain:
    domain: str
    tenant: str
    verified: bool


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    tenant: str
    email: str
    attributes: dict
    sources: dict
    merged_from: tuple[str, ...]


@dataclass(frozen=True)
class ProvisionResult:
    status: str
    account_id: str
    tenant: str
    attributes: dict
    sources: dict


@dataclass(frozen=True)
class MergeRequest:
    request_id: str
    account_id: str
    email: str
    status: str
    requested_at: int
    confirmable_at: int


class JitProvisioningGate:
    """Provision first-login accounts from verified email domains."""

    def __init__(self, *, cooldown: int = 3600, rate_limit: int = 10,
                 rate_window: int = 3600) -> None:
        self.cooldown = cooldown
        self.rate_limit = rate_limit
        self.rate_window = rate_window

    def register_domain(self, *, domain: str, tenant: str,
                        verified: bool = True) -> TenantDomain:
        raise NotImplementedError

    def register_mapping(self, *, tenant: str, target: str, source: str,
                         default=None, priority: int = 0) -> None:
        raise NotImplementedError

    def resolve_tenant(self, *, email: str) -> str | None:
        raise NotImplementedError

    def map_attributes(self, *, tenant: str,
                       attributes) -> tuple[dict, dict]:
        raise NotImplementedError

    def provision(self, *, email: str, attributes,
                  now: int) -> ProvisionResult:
        raise NotImplementedError

    def get_account(self, account_id: str) -> AccountRecord | None:
        raise NotImplementedError

    def find_account(self, *, email: str) -> AccountRecord | None:
        raise NotImplementedError

    def request_merge(self, *, account_id: str, email: str,
                      now: int) -> MergeRequest:
        raise NotImplementedError

    def confirm_merge(self, *, request_id: str, now: int) -> AccountRecord:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state) -> None:
        raise NotImplementedError
