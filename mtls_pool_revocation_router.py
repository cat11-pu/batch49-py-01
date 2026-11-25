"""mTLS connection pool routing by SNI pattern, CRL epoch propagation and
migration of in-flight leases off revoked certificates."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class MtlsRoutingError(RuntimeError):
    """Raised when a routing or pool operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Lease:
    lease_id: str
    sni: str
    host: str
    conn_id: str
    fingerprint: str
    opened_at: int


@dataclass(frozen=True)
class EndpointView:
    host: str
    sni: str
    fingerprint: str
    weight: int


class MtlsPoolRevocationRouter:
    """Route mTLS connections by SNI, apply CRLs and move stranded leases."""

    def __init__(self, *, max_leases_per_conn: int = 4,
                 max_conns_per_host: int = 2) -> None:
        self.max_leases_per_conn = max_leases_per_conn
        self.max_conns_per_host = max_conns_per_host
        self._lock = threading.RLock()

    def register_endpoint(self, host: str, *, sni: str, fingerprint: str,
                          weight: int = 1) -> None:
        raise NotImplementedError

    def resolve(self, sni: str) -> str:
        raise NotImplementedError

    def open_connection(self, host: str, *,
                        conn_id: str | None = None) -> str:
        raise NotImplementedError

    def acquire(self, sni: str, *, lease_id: str | None = None,
                now: int = 0) -> Lease:
        raise NotImplementedError

    def release(self, lease_id: str, *, ok: bool = True) -> str:
        raise NotImplementedError

    def fail_lease(self, lease_id: str, *, reason: str = "") -> str:
        raise NotImplementedError

    def migrate_lease(self, lease_id: str, *, now: int = 0) -> Lease:
        raise NotImplementedError

    def apply_revocation(self, epoch: int, fingerprints, *,
                         now: int = 0) -> dict:
        raise NotImplementedError

    def rotate_certificate(self, host: str, *, fingerprint: str) -> None:
        raise NotImplementedError

    def drain_connection(self, conn_id: str) -> int:
        raise NotImplementedError

    def active_leases(self, sni: str | None = None) -> tuple[Lease, ...]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: object) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError
