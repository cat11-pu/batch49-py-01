"""DNS-over-HTTPS answer verification, resolver quorum and response cache."""

from __future__ import annotations


class DohVerifyError(ValueError):
    """Raised when a DoH query, submission or resolution is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DohResponseVerifier:
    """Validate DoH answers against the query and require resolver agreement."""

    def __init__(self, *, min_ttl: int = 30, max_ttl: int = 3600,
                 quorum: int = 2, max_resolvers: int = 8,
                 max_cache: int = 128, chain_depth: int = 8) -> None:
        self.min_ttl = min_ttl
        self.max_ttl = max_ttl
        self.quorum = quorum
        self.max_resolvers = max_resolvers
        self.max_cache = max_cache
        self.chain_depth = chain_depth

    def register_resolver(self, resolver_id: str, endpoint: str, *,
                          spki_sha256: str, now: int = 0) -> dict:
        raise NotImplementedError

    def retire_resolver(self, resolver_id: str, *, now: int = 0) -> bool:
        raise NotImplementedError

    def resolvers(self) -> list:
        raise NotImplementedError

    def begin_query(self, name: str, qtype: str, *, nonce: str,
                    now: int = 0) -> dict:
        raise NotImplementedError

    def submit(self, query_id: str, resolver_id: str, response: dict, *,
               now: int = 0) -> dict:
        raise NotImplementedError

    def resolve(self, query_id: str, *, now: int = 0) -> dict:
        raise NotImplementedError

    def cache_get(self, name: str, qtype: str, *, now: int = 0):
        raise NotImplementedError

    def flush(self, *, now: int = 0) -> int:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
