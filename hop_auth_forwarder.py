"""Per-hop proxy authentication forwarder for multi-hop forward proxy chains."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import threading


_INVALID_REQUEST = "HOP_INVALID_REQUEST"
_UNKNOWN_HOP = "HOP_UNKNOWN_HOP"
_CREDENTIAL_MISSING = "HOP_CREDENTIAL_MISSING"
_CHAIN_EXHAUSTED = "HOP_CHAIN_EXHAUSTED"
_SNAPSHOT_INVALID = "HOP_SNAPSHOT_INVALID"

_HOP_FIELDS = ("name", "scheme", "credential_ref")


class HopAuthError(RuntimeError):
    """Raised when a hop or chain operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class HopAuthForwarder:
    """Maintain an ordered proxy chain, per-hop auth schemes and credential refs."""

    def __init__(self, *, max_auth_failures: int = 2) -> None:
        if (
            isinstance(max_auth_failures, bool)
            or not isinstance(max_auth_failures, int)
            or max_auth_failures < 1
        ):
            raise HopAuthError(_INVALID_REQUEST, "max_auth_failures")

        self.max_auth_failures = max_auth_failures
        self._lock = threading.RLock()

        self._credentials: dict[str, str] = {}
        self._chain: list[dict[str, object]] = []
        self._active: str | None = None

        self._auth_attempts: dict[str, int] = {}
        self._auth_failures: dict[str, int] = {}
        self._consecutive_407: dict[str, int] = {}

        self._forwards = 0
        self._total_auth_attempts = 0
        self._total_auth_failures = 0
        self._failovers = 0
        self._retries = 0

        # A CONNECT request creates a tunnel. Requests sent through that
        # tunnel must not receive credentials intended for the proxy.
        self._tunnel_established = False

    @staticmethod
    def _invalid(detail: object = None) -> HopAuthError:
        return HopAuthError(_INVALID_REQUEST, detail)

    @staticmethod
    def _snapshot_invalid(detail: object = None) -> HopAuthError:
        return HopAuthError(_SNAPSHOT_INVALID, detail)

    @staticmethod
    def _is_nonempty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value)

    def _validated_hops(self, hops: object) -> list[dict[str, str]]:
        if (
            isinstance(hops, (str, bytes, bytearray))
            or not isinstance(hops, Sequence)
        ):
            raise self._invalid("chain")

        try:
            raw_hops = list(hops)
        except Exception as exc:
            raise self._invalid("chain") from exc

        if not raw_hops:
            raise self._invalid("chain")

        validated: list[dict[str, str]] = []
        names: set[str] = set()
        for raw_hop in raw_hops:
            if not isinstance(raw_hop, Mapping):
                raise self._invalid("hop")

            hop: dict[str, str] = {}
            for field in _HOP_FIELDS:
                value = raw_hop.get(field)
                if not self._is_nonempty_string(value):
                    raise self._invalid(field)
                hop[field] = value

            name = hop["name"]
            if name in names:
                raise self._invalid("duplicate hop")
            names.add(name)
            validated.append(hop)

        return validated

    def _index_of(self, hop_name: str) -> int:
        for index, hop in enumerate(self._chain):
            if hop["name"] == hop_name:
                return index
        raise HopAuthError(_UNKNOWN_HOP, hop_name)

    def _next_available(self, index: int) -> int | None:
        for candidate in range(index + 1, len(self._chain)):
            if not self._chain[candidate]["failed"]:
                return candidate
        return None

    def _reset_runtime_state(self) -> None:
        self._auth_attempts = {hop["name"]: 0 for hop in self._chain}
        self._auth_failures = {hop["name"]: 0 for hop in self._chain}
        self._consecutive_407 = {hop["name"]: 0 for hop in self._chain}
        self._forwards = 0
        self._total_auth_attempts = 0
        self._total_auth_failures = 0
        self._failovers = 0
        self._retries = 0
        self._tunnel_established = False

    def set_chain(self, hops) -> list:
        with self._lock:
            validated = self._validated_hops(hops)

            self._chain = [
                {
                    "name": hop["name"],
                    "scheme": hop["scheme"],
                    "credential_ref": hop["credential_ref"],
                    "failed": False,
                }
                for hop in validated
            ]
            self._active = self._chain[0]["name"]
            self._reset_runtime_state()
            return [hop["name"] for hop in self._chain]

    def register_credential(self, ref: str, secret: str) -> None:
        with self._lock:
            if not self._is_nonempty_string(ref) or not isinstance(secret, str):
                raise self._invalid("credential")
            self._credentials[ref] = secret

    def forward(self, request: dict) -> dict:
        with self._lock:
            if not isinstance(request, Mapping):
                raise self._invalid("request")

            raw_headers = request.get("headers", {})
            if not isinstance(raw_headers, Mapping):
                raise self._invalid("headers")

            if not self._chain or self._active is None:
                raise HopAuthError(_CHAIN_EXHAUSTED, "no active hop")

            hop = self._chain[self._index_of(self._active)]
            if hop["failed"]:
                raise HopAuthError(_CHAIN_EXHAUSTED, "no active hop")

            headers = {
                key: value
                for key, value in raw_headers.items()
                if not (
                    isinstance(key, str)
                    and key.lower() == "proxy-authorization"
                )
            }

            method = request.get("method", "")
            is_connect = (
                isinstance(method, str) and method.upper() == "CONNECT"
            )

            ref = hop["credential_ref"]
            secret = self._credentials.get(ref)
            if secret is None:
                raise HopAuthError(_CREDENTIAL_MISSING, ref)

            if is_connect:
                self._tunnel_established = True
            elif not self._tunnel_established:
                headers["Proxy-Authorization"] = (
                    f"{hop['scheme']} {secret}"
                )

            name = hop["name"]
            self._forwards += 1
            return {
                "hop": name,
                "status": None,
                "attempt": self._consecutive_407.get(name, 0) + 1,
                "headers": headers,
            }

    def authenticate_hop(self, hop_name: str, now: str | None = None) -> dict:
        with self._lock:
            self._index_of(hop_name)
            attempts = self._auth_attempts.get(hop_name, 0) + 1
            self._auth_attempts[hop_name] = attempts
            self._total_auth_attempts += 1
            return {"hop": hop_name, "attempts": attempts}

    def _failover_locked(self, hop_name: str) -> str:
        index = self._index_of(hop_name)
        next_index = self._next_available(index)
        if next_index is None:
            raise HopAuthError(_CHAIN_EXHAUSTED, hop_name)

        self._chain[index]["failed"] = True
        self._active = self._chain[next_index]["name"]
        self._failovers += 1
        self._tunnel_established = False
        return self._active

    def mark_response(self, hop_name: str, status: int,
                      now: str | None = None) -> dict:
        with self._lock:
            index = self._index_of(hop_name)
            hop = self._chain[index]

            if status != 407:
                self._consecutive_407[hop_name] = 0
                return {
                    "action": "pass",
                    "hop": hop_name,
                    "status": status,
                }

            self._total_auth_failures += 1
            self._auth_failures[hop_name] = (
                self._auth_failures.get(hop_name, 0) + 1
            )
            consecutive = self._consecutive_407.get(hop_name, 0) + 1
            self._consecutive_407[hop_name] = consecutive

            if consecutive < self.max_auth_failures:
                self._retries += 1
                return {
                    "action": "retry_auth",
                    "hop": hop_name,
                    "attempts": consecutive,
                }

            active = self._failover_locked(hop_name)
            return {
                "action": "failover",
                "hop": hop_name,
                "active": active,
            }

    def failover(self, hop_name: str) -> str:
        with self._lock:
            return self._failover_locked(hop_name)

    def hop_status(self) -> list:
        with self._lock:
            return [
                {
                    "name": hop["name"],
                    "scheme": hop["scheme"],
                    "has_credential": (
                        hop["credential_ref"] in self._credentials
                    ),
                    "active": hop["name"] == self._active,
                    "status": "failed" if hop["failed"] else "ready",
                }
                for hop in self._chain
            ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "hops": len(self._chain),
                "forwards": self._forwards,
                "auth_attempts": self._total_auth_attempts,
                "auth_failures": self._total_auth_failures,
                "failovers": self._failovers,
                "retries": self._retries,
            }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "chain": [
                    {
                        "name": hop["name"],
                        "scheme": hop["scheme"],
                        "credential_ref": hop["credential_ref"],
                    }
                    for hop in self._chain
                ],
                "active": self._active,
                "failed": [
                    hop["name"] for hop in self._chain if hop["failed"]
                ],
                "counters": {
                    "forwards": self._forwards,
                    "auth_attempts": self._total_auth_attempts,
                    "auth_failures": self._total_auth_failures,
                    "failovers": self._failovers,
                    "retries": self._retries,
                    "per_hop_attempts": dict(self._auth_attempts),
                    "per_hop_failures": dict(self._auth_failures),
                    "per_hop_407": dict(self._consecutive_407),
                },
            }

    @staticmethod
    def _snapshot_counter(
        counters: Mapping, name: str, default: int = 0
    ) -> int:
        value = counters.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HopAuthForwarder._snapshot_invalid(name)
        return value

    def restore(self, state: dict) -> None:
        with self._lock:
            if not isinstance(state, Mapping):
                raise self._snapshot_invalid("state")
            version = state.get("version")
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version != 1
            ):
                raise self._snapshot_invalid("version")

            try:
                validated = self._validated_hops(state.get("chain"))
            except HopAuthError as exc:
                raise self._snapshot_invalid("chain") from exc

            names = {hop["name"] for hop in validated}

            raw_failed = state.get("failed", [])
            if (
                isinstance(raw_failed, (str, bytes, bytearray))
                or not isinstance(raw_failed, Sequence)
            ):
                raise self._snapshot_invalid("failed")
            try:
                failed_names = list(raw_failed)
            except Exception as exc:
                raise self._snapshot_invalid("failed") from exc
            if any(
                not self._is_nonempty_string(name) or name not in names
                for name in failed_names
            ):
                raise self._snapshot_invalid("failed")
            if len(set(failed_names)) != len(failed_names):
                raise self._snapshot_invalid("failed")
            failed_set = set(failed_names)

            active = state.get("active")
            if active is None:
                for hop in validated:
                    if hop["name"] not in failed_set:
                        active = hop["name"]
                        break
            elif (
                not self._is_nonempty_string(active)
                or active not in names
                or active in failed_set
            ):
                raise self._snapshot_invalid("active")

            raw_counters = state.get("counters", {})
            if not isinstance(raw_counters, Mapping):
                raise self._snapshot_invalid("counters")

            forwards = self._snapshot_counter(raw_counters, "forwards")
            total_attempts = self._snapshot_counter(
                raw_counters, "auth_attempts"
            )
            total_failures = self._snapshot_counter(
                raw_counters, "auth_failures"
            )
            failovers = self._snapshot_counter(raw_counters, "failovers")
            retries = self._snapshot_counter(raw_counters, "retries")

            raw_attempts = raw_counters.get("per_hop_attempts", {})
            raw_failures = raw_counters.get("per_hop_failures", {})
            raw_407 = raw_counters.get("per_hop_407", {})
            if not all(
                isinstance(value, Mapping)
                for value in (raw_attempts, raw_failures, raw_407)
            ):
                raise self._snapshot_invalid("counters")

            def per_hop(source: Mapping, name: str) -> int:
                value = source.get(name, 0)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise self._snapshot_invalid("counters")
                return value

            attempts = {
                name: per_hop(raw_attempts, name) for name in names
            }
            failures = {
                name: per_hop(raw_failures, name) for name in names
            }
            consecutive = {
                name: per_hop(raw_407, name) for name in names
            }

            chain = [
                {
                    "name": hop["name"],
                    "scheme": hop["scheme"],
                    "credential_ref": hop["credential_ref"],
                    "failed": hop["name"] in failed_set,
                }
                for hop in validated
            ]

            self._chain = chain
            self._active = active
            self._auth_attempts = attempts
            self._auth_failures = failures
            self._consecutive_407 = consecutive
            self._forwards = forwards
            self._total_auth_attempts = total_attempts
            self._total_auth_failures = total_failures
            self._failovers = failovers
            self._retries = retries
            self._tunnel_established = False
