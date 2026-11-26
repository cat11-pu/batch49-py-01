"""OAuth device authorization polling guard: user-code phishing, poll pacing
and token binding."""

from __future__ import annotations

import copy
import secrets
import threading


_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_USER_CODE_LENGTH = 8
_DEVICE_STATES = frozenset({"pending", "approved", "denied", "consumed"})
_STAT_KEYS = (
    "issued",
    "approved",
    "denied",
    "polls",
    "tokens_issued",
    "slow_downs",
    "refreshes",
    "revocations",
    "verify_failures",
)
_CONFIG_KEYS = (
    "interval",
    "expires_in",
    "slow_down_step",
    "max_interval",
    "max_pending_per_client",
    "max_verify_attempts",
    "verify_lock_window",
    "token_ttl",
    "max_bindings",
)


class DeviceCodePollError(ValueError):
    """Raised when a device flow operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class DeviceCodePollGuard:
    """Track device codes, user codes and token families for one client pool."""

    def __init__(self, *, interval: int = 5, expires_in: int = 600,
                 slow_down_step: int = 5, max_interval: int = 60,
                 max_pending_per_client: int = 8, max_verify_attempts: int = 5,
                 verify_lock_window: int = 300, token_ttl: int = 300,
                 max_bindings: int = 32) -> None:
        self.interval = interval
        self.expires_in = expires_in
        self.slow_down_step = slow_down_step
        self.max_interval = max_interval
        self.max_pending_per_client = max_pending_per_client
        self.max_verify_attempts = max_verify_attempts
        self.verify_lock_window = verify_lock_window
        self.token_ttl = token_ttl
        self.max_bindings = max_bindings
        self._lock = threading.RLock()

        self._devices: dict[str, dict] = {}
        self._user_codes: dict[str, str] = {}
        self._refreshes: dict[str, dict] = {}
        self._bindings: dict[str, dict] = {}
        self._revoked_families: set[str] = set()
        self._counters = {key: 0 for key in _STAT_KEYS}
        self._verify_streak = 0
        self._verify_locked_until: int | None = None
        self._clock = 0

    def normalize_user_code(self, raw: str) -> str:
        if not isinstance(raw, str):
            raise DeviceCodePollError(
                "DCP_USER_CODE_INVALID", "user code must be a string"
            )
        compact = raw.replace(" ", "").replace("-", "").upper()
        if (
            len(compact) != _USER_CODE_LENGTH
            or any(char not in _USER_CODE_ALPHABET for char in compact)
        ):
            raise DeviceCodePollError(
                "DCP_USER_CODE_INVALID", "user code must contain 8 valid characters"
            )
        return f"{compact[:4]}-{compact[4:]}"

    def issue(self, client_id: str, scopes, *, now: int = 0,
              device_code: str = None, user_code: str = None) -> dict:
        with self._lock:
            self._clock = max(self._clock, now)
            if not isinstance(client_id, str) or not client_id.strip():
                raise DeviceCodePollError(
                    "DCP_CLIENT_INVALID", "client_id must be non-empty"
                )
            normalized_scopes = self._normalize_scopes(scopes)

            if device_code is not None:
                if not isinstance(device_code, str) or len(device_code) < 16:
                    raise DeviceCodePollError(
                        "DCP_DEVICE_CODE_INVALID",
                        "device_code must contain at least 16 characters",
                    )
                if device_code in self._devices:
                    raise DeviceCodePollError(
                        "DCP_DEVICE_CODE_INVALID", "device_code is already in use"
                    )
            if user_code is not None:
                normalized_user_code = self.normalize_user_code(user_code)

            if user_code is not None and normalized_user_code in self._user_codes:
                raise DeviceCodePollError(
                    "DCP_USER_CODE_TAKEN", "user_code is already in use"
                )

            pending = sum(
                1
                for record in self._devices.values()
                if record["client_id"] == client_id
                and record["state"] == "pending"
                and record["expires_at"] > now
            )
            if pending >= self.max_pending_per_client:
                raise DeviceCodePollError(
                    "DCP_CAPACITY_EXCEEDED", "pending device-code capacity reached"
                )

            if device_code is None:
                device_code = self._generate_unique_device_code()
            if user_code is None:
                normalized_user_code = self._generate_unique_user_code()

            expires_at = now + self.expires_in
            record = {
                "client_id": client_id,
                "scopes": normalized_scopes,
                "state": "pending",
                "interval": self.interval,
                "expires_at": expires_at,
                "subject": None,
                "issued_at": now,
                "last_poll_at": now,
            }
            self._devices[device_code] = record
            self._user_codes[normalized_user_code] = device_code
            self._counters["issued"] += 1

            return {
                "device_code": device_code,
                "user_code": normalized_user_code,
                "expires_at": expires_at,
                "interval": self.interval,
                "verification_uri": "https://device.example.com/activate",
                "scope": list(normalized_scopes),
            }

    def approve(self, user_code: str, *, subject: str, now: int = 0) -> dict:
        with self._lock:
            self._clock = max(self._clock, now)
            self._check_verify_lock(now)

            device_code = self._lookup_user_code(user_code, now)
            record = self._devices[device_code]
            self._reset_verify_streak()

            if now >= record["expires_at"]:
                raise DeviceCodePollError("DCP_EXPIRED", "user_code has expired")
            if record["state"] == "approved":
                if record["subject"] != subject:
                    raise DeviceCodePollError(
                        "DCP_SUBJECT_MISMATCH",
                        "user_code was approved by a different subject",
                    )
                return {
                    "status": "approved",
                    "already_approved": True,
                    "device_code": device_code,
                }
            if record["state"] in {"denied", "consumed"}:
                raise DeviceCodePollError(
                    "DCP_ALREADY_RESOLVED", "user_code has already been resolved"
                )

            record["state"] = "approved"
            record["subject"] = subject
            record["approved_at"] = now
            self._counters["approved"] += 1
            return {
                "status": "approved",
                "already_approved": False,
                "device_code": device_code,
            }

    def deny(self, user_code: str, *, now: int = 0) -> bool:
        with self._lock:
            self._clock = max(self._clock, now)
            self._check_verify_lock(now)

            device_code = self._lookup_user_code(user_code, now)
            record = self._devices[device_code]
            self._reset_verify_streak()

            if now >= record["expires_at"]:
                raise DeviceCodePollError("DCP_EXPIRED", "user_code has expired")
            if record["state"] == "approved":
                raise DeviceCodePollError(
                    "DCP_ALREADY_APPROVED", "user_code has already been approved"
                )
            if record["state"] == "denied":
                return False
            if record["state"] != "pending":
                raise DeviceCodePollError(
                    "DCP_ALREADY_RESOLVED", "user_code has already been resolved"
                )

            record["state"] = "denied"
            record["denied_at"] = now
            self._counters["denied"] += 1
            return True

    def poll(self, device_code: str, *, client_id: str, now: int = 0) -> dict:
        with self._lock:
            self._clock = max(self._clock, now)
            self._counters["polls"] += 1

            record = self._devices.get(device_code)
            if record is None:
                raise DeviceCodePollError(
                    "DCP_DEVICE_CODE_UNKNOWN", "device_code is unknown"
                )
            if record["client_id"] != client_id:
                raise DeviceCodePollError(
                    "DCP_CLIENT_MISMATCH", "device_code belongs to another client"
                )
            if record["state"] == "consumed":
                raise DeviceCodePollError(
                    "DCP_TOKEN_CONSUMED", "device_code has already been exchanged"
                )

            interval = record["interval"]
            if now >= record["expires_at"]:
                return {
                    "status": "expired_token",
                    "interval": interval,
                    "retry_after": 0,
                }

            if now - record["last_poll_at"] < interval:
                interval = min(interval + self.slow_down_step, self.max_interval)
                record["interval"] = interval
                record["last_poll_at"] = now
                self._counters["slow_downs"] += 1
                return {
                    "status": "slow_down",
                    "interval": interval,
                    "retry_after": interval,
                }

            if record["state"] == "pending":
                record["last_poll_at"] = now
                return {
                    "status": "authorization_pending",
                    "interval": interval,
                    "retry_after": interval,
                }
            if record["state"] == "denied":
                record["last_poll_at"] = now
                return {
                    "status": "access_denied",
                    "interval": interval,
                    "retry_after": 0,
                }

            self._ensure_binding_capacity(now)
            record["last_poll_at"] = now
            token_response = self._issue_token_pair(
                record["client_id"],
                record["subject"],
                record["scopes"],
                now,
            )
            record["state"] = "consumed"
            record["consumed_at"] = now
            self._counters["tokens_issued"] += 1
            return {
                "status": "token",
                **token_response,
                "interval": interval,
                "retry_after": 0,
            }

    def refresh(self, refresh_token: str, *, client_id: str, now: int = 0) -> dict:
        with self._lock:
            self._clock = max(self._clock, now)
            record = self._refreshes.get(refresh_token)
            if record is None:
                raise DeviceCodePollError(
                    "DCP_REFRESH_INVALID", "refresh_token is unknown"
                )
            if record["client_id"] != client_id:
                raise DeviceCodePollError(
                    "DCP_CLIENT_MISMATCH", "refresh_token belongs to another client"
                )

            family = record["family"]
            if family in self._revoked_families:
                raise DeviceCodePollError(
                    "DCP_TOKEN_REVOKED", "token family has been revoked"
                )
            if record["used"]:
                self._revoke_family(family, now)
                raise DeviceCodePollError(
                    "DCP_REFRESH_REUSED",
                    "refresh_token reuse detected; token family revoked",
                )

            old_access = self._bindings.get(record["access_token"])
            replacing_active = bool(
                old_access
                and old_access["family"] == family
                and old_access["active"]
                and old_access["expires_at"] > now
            )
            self._ensure_binding_capacity(now, replacing_active=replacing_active)

            access_token = self._generate_token("access")
            new_refresh = self._generate_token("refresh")
            expires_at = now + self.token_ttl
            scopes = list(record["scopes"])
            subject = record["subject"]

            record["used"] = True
            if old_access is not None:
                old_access["active"] = False
            self._bindings[access_token] = {
                "access_token": access_token,
                "client_id": client_id,
                "subject": subject,
                "scopes": scopes,
                "family": family,
                "issued_at": now,
                "expires_at": expires_at,
                "active": True,
                "revoked": False,
            }
            self._refreshes[new_refresh] = {
                "refresh_token": new_refresh,
                "client_id": client_id,
                "subject": subject,
                "scopes": scopes,
                "family": family,
                "issued_at": now,
                "used": False,
                "revoked": False,
                "access_token": access_token,
            }
            self._counters["refreshes"] += 1
            self._counters["tokens_issued"] += 1
            return self._token_response(access_token, new_refresh, scopes)

    def revoke(self, token: str, *, now: int = 0) -> bool:
        with self._lock:
            self._clock = max(self._clock, now)
            record = self._bindings.get(token)
            if record is None:
                record = self._refreshes.get(token)
            if record is None:
                return False

            family = record["family"]
            if family in self._revoked_families:
                return False
            self._revoke_family(family, now)
            return True

    def bindings(self) -> list:
        with self._lock:
            active = [
                record
                for record in self._bindings.values()
                if record["active"]
                and not record["revoked"]
                and record["family"] not in self._revoked_families
                and record["expires_at"] > self._clock
            ]
            active.sort(key=lambda record: (
                record["issued_at"], record["access_token"]
            ))
            return [
                {
                    "access_token": record["access_token"],
                    "client_id": record["client_id"],
                    "subject": record["subject"],
                    "scopes": list(record["scopes"]),
                    "expires_at": record["expires_at"],
                    "family": record["family"],
                }
                for record in active
            ]

    def describe(self, device_code: str) -> dict:
        with self._lock:
            record = self._devices.get(device_code)
            if record is None:
                raise DeviceCodePollError(
                    "DCP_DEVICE_CODE_UNKNOWN", "device_code is unknown"
                )
            return {
                "client_id": record["client_id"],
                "state": record["state"],
                "interval": record["interval"],
                "expires_at": record["expires_at"],
                "subject": record["subject"],
            }

    def purge(self, *, now: int = 0) -> int:
        with self._lock:
            self._clock = max(self._clock, now)
            expired = [
                device_code
                for device_code, record in self._devices.items()
                if now >= record["expires_at"]
            ]
            for device_code in expired:
                del self._devices[device_code]
            if expired:
                expired_set = set(expired)
                stale_user_codes = [
                    user_code
                    for user_code, device_code in self._user_codes.items()
                    if device_code in expired_set
                ]
                for user_code in stale_user_codes:
                    del self._user_codes[user_code]
            return len(expired)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "config": {key: getattr(self, key) for key in _CONFIG_KEYS},
                "devices": copy.deepcopy(self._devices),
                "user_codes": copy.deepcopy(self._user_codes),
                "refreshes": copy.deepcopy(self._refreshes),
                "bindings": copy.deepcopy(self._bindings),
                "revoked_families": sorted(self._revoked_families),
                "counters": copy.deepcopy(self._counters),
                "verify_streak": self._verify_streak,
                "verify_locked_until": self._verify_locked_until,
                "clock": self._clock,
            }

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                candidate = copy.deepcopy(state)
                validated = self._validate_snapshot(candidate)
            except Exception as exc:
                if (
                    isinstance(exc, DeviceCodePollError)
                    and exc.code == "DCP_SNAPSHOT_INVALID"
                ):
                    raise
                raise DeviceCodePollError(
                    "DCP_SNAPSHOT_INVALID", "snapshot structure is invalid"
                ) from exc

            for key in _CONFIG_KEYS:
                setattr(self, key, validated["config"][key])
            self._devices = validated["devices"]
            self._user_codes = validated["user_codes"]
            self._refreshes = validated["refreshes"]
            self._bindings = validated["bindings"]
            self._revoked_families = validated["revoked_families"]
            self._counters = validated["counters"]
            self._verify_streak = validated["verify_streak"]
            self._verify_locked_until = validated["verify_locked_until"]
            self._clock = validated["clock"]

    def stats(self) -> dict:
        with self._lock:
            return {key: self._counters[key] for key in _STAT_KEYS}

    @staticmethod
    def _normalize_scopes(scopes) -> list[str]:
        if isinstance(scopes, str):
            scopes = [scopes]
        try:
            normalized = sorted(set(scopes))
        except (TypeError, ValueError) as exc:
            raise DeviceCodePollError(
                "DCP_SCOPE_INVALID", "scopes must be an iterable of hashable values"
            ) from exc
        if any(not isinstance(scope, str) for scope in normalized):
            raise DeviceCodePollError(
                "DCP_SCOPE_INVALID", "scopes must contain strings"
            )
        return normalized

    def _generate_unique_device_code(self) -> str:
        while True:
            device_code = secrets.token_urlsafe(32)
            if device_code not in self._devices:
                return device_code

    def _generate_unique_user_code(self) -> str:
        while True:
            compact = "".join(
                secrets.choice(_USER_CODE_ALPHABET)
                for _ in range(_USER_CODE_LENGTH)
            )
            user_code = f"{compact[:4]}-{compact[4:]}"
            if user_code not in self._user_codes:
                return user_code

    @staticmethod
    def _generate_token(kind: str) -> str:
        return f"{kind}_{secrets.token_urlsafe(32)}"

    def _lookup_user_code(self, user_code: str, now: int) -> str:
        try:
            normalized = self.normalize_user_code(user_code)
        except DeviceCodePollError:
            self._record_verify_failure(now)
            raise DeviceCodePollError(
                "DCP_USER_CODE_UNKNOWN", "user_code is unknown"
            ) from None
        device_code = self._user_codes.get(normalized)
        if device_code is None or device_code not in self._devices:
            self._record_verify_failure(now)
            raise DeviceCodePollError(
                "DCP_USER_CODE_UNKNOWN", "user_code is unknown"
            )
        return device_code

    def _record_verify_failure(self, now: int) -> None:
        self._counters["verify_failures"] += 1
        self._verify_streak += 1
        if self._verify_streak >= self.max_verify_attempts:
            self._verify_streak = 0
            self._verify_locked_until = now + self.verify_lock_window

    def _check_verify_lock(self, now: int) -> None:
        if self._verify_locked_until is None:
            return
        if now < self._verify_locked_until:
            raise DeviceCodePollError(
                "DCP_CODE_LOCKED", "user-code verification is temporarily locked"
            )
        self._verify_locked_until = None
        self._verify_streak = 0

    def _reset_verify_streak(self) -> None:
        self._verify_streak = 0

    def _ensure_binding_capacity(
        self, now: int, *, replacing_active: bool = False
    ) -> None:
        active = sum(
            1
            for record in self._bindings.values()
            if record["active"]
            and not record["revoked"]
            and record["family"] not in self._revoked_families
            and record["expires_at"] > now
        )
        if replacing_active:
            active -= 1
        if active >= self.max_bindings:
            raise DeviceCodePollError(
                "DCP_CAPACITY_EXCEEDED", "active token-binding capacity reached"
            )

    def _issue_token_pair(
        self, client_id: str, subject: str, scopes: list[str], now: int
    ) -> dict:
        access_token = self._generate_token("access")
        refresh_token = self._generate_token("refresh")
        family = secrets.token_urlsafe(24)
        expires_at = now + self.token_ttl
        self._bindings[access_token] = {
            "access_token": access_token,
            "client_id": client_id,
            "subject": subject,
            "scopes": list(scopes),
            "family": family,
            "issued_at": now,
            "expires_at": expires_at,
            "active": True,
            "revoked": False,
        }
        self._refreshes[refresh_token] = {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "subject": subject,
            "scopes": list(scopes),
            "family": family,
            "issued_at": now,
            "used": False,
            "revoked": False,
            "access_token": access_token,
        }
        return self._token_response(access_token, refresh_token, scopes)

    def _token_response(
        self, access_token: str, refresh_token: str, scopes: list[str]
    ) -> dict:
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": self.token_ttl,
            "scope": list(scopes),
        }

    def _revoke_family(self, family: str, now: int) -> None:
        if family in self._revoked_families:
            return
        self._revoked_families.add(family)
        for record in self._bindings.values():
            if record["family"] == family:
                record["active"] = False
                record["revoked"] = True
        for record in self._refreshes.values():
            if record["family"] == family:
                record["revoked"] = True
        self._counters["revocations"] += 1

    @staticmethod
    def _snapshot_invalid(detail: str) -> DeviceCodePollError:
        return DeviceCodePollError("DCP_SNAPSHOT_INVALID", detail)

    def _validate_snapshot(self, state: object) -> dict:
        if not isinstance(state, dict):
            raise self._snapshot_invalid("snapshot must be a mapping")
        required = {
            "config",
            "devices",
            "user_codes",
            "refreshes",
            "bindings",
            "revoked_families",
            "counters",
            "verify_streak",
            "verify_locked_until",
            "clock",
        }
        if not required.issubset(state):
            raise self._snapshot_invalid("snapshot is missing fields")

        config = state["config"]
        if not isinstance(config, dict) or any(
            key not in config for key in _CONFIG_KEYS
        ):
            raise self._snapshot_invalid("invalid configuration")
        for key in _CONFIG_KEYS:
            value = config[key]
            if not isinstance(value, int) or isinstance(value, bool):
                raise self._snapshot_invalid("invalid configuration value")

        devices = state["devices"]
        if not isinstance(devices, dict):
            raise self._snapshot_invalid("invalid device table")
        for device_code, record in devices.items():
            if not isinstance(device_code, str) or not isinstance(record, dict):
                raise self._snapshot_invalid("invalid device record")
            if not {
                "client_id",
                "scopes",
                "state",
                "interval",
                "expires_at",
                "subject",
                "issued_at",
                "last_poll_at",
            }.issubset(record):
                raise self._snapshot_invalid("incomplete device record")
            if (
                not isinstance(record["client_id"], str)
                or not isinstance(record["scopes"], list)
                or any(not isinstance(scope, str) for scope in record["scopes"])
                or record["state"] not in _DEVICE_STATES
                or not isinstance(record["interval"], int)
                or not isinstance(record["expires_at"], int)
                or not (
                    record["subject"] is None
                    or isinstance(record["subject"], str)
                )
                or not isinstance(record["issued_at"], int)
                or not isinstance(record["last_poll_at"], int)
            ):
                raise self._snapshot_invalid("invalid device record value")

        user_codes = state["user_codes"]
        if not isinstance(user_codes, dict):
            raise self._snapshot_invalid("invalid user-code index")
        for user_code, device_code in user_codes.items():
            if not isinstance(user_code, str) or not isinstance(device_code, str):
                raise self._snapshot_invalid("invalid user-code entry")
            if device_code not in devices:
                raise self._snapshot_invalid("dangling user-code entry")
            try:
                normalized = self.normalize_user_code(user_code)
            except DeviceCodePollError as exc:
                raise self._snapshot_invalid("invalid user code") from exc
            if normalized != user_code:
                raise self._snapshot_invalid("non-canonical user code")

        refreshes = state["refreshes"]
        if not isinstance(refreshes, dict):
            raise self._snapshot_invalid("invalid refresh index")
        for token, record in refreshes.items():
            if not isinstance(token, str) or not isinstance(record, dict):
                raise self._snapshot_invalid("invalid refresh record")
            if not {
                "refresh_token",
                "client_id",
                "subject",
                "scopes",
                "family",
                "issued_at",
                "used",
                "revoked",
                "access_token",
            }.issubset(record):
                raise self._snapshot_invalid("incomplete refresh record")
            if (
                record["refresh_token"] != token
                or not isinstance(record["client_id"], str)
                or not isinstance(record["subject"], str)
                or not isinstance(record["scopes"], list)
                or any(not isinstance(scope, str) for scope in record["scopes"])
                or not isinstance(record["family"], str)
                or not isinstance(record["issued_at"], int)
                or not isinstance(record["used"], bool)
                or not isinstance(record["revoked"], bool)
                or not isinstance(record["access_token"], str)
            ):
                raise self._snapshot_invalid("invalid refresh record value")

        bindings = state["bindings"]
        if not isinstance(bindings, dict):
            raise self._snapshot_invalid("invalid binding table")
        for token, record in bindings.items():
            if not isinstance(token, str) or not isinstance(record, dict):
                raise self._snapshot_invalid("invalid binding record")
            if not {
                "access_token",
                "client_id",
                "subject",
                "scopes",
                "family",
                "issued_at",
                "expires_at",
                "active",
                "revoked",
            }.issubset(record):
                raise self._snapshot_invalid("incomplete binding record")
            if (
                record["access_token"] != token
                or not isinstance(record["client_id"], str)
                or not isinstance(record["subject"], str)
                or not isinstance(record["scopes"], list)
                or any(not isinstance(scope, str) for scope in record["scopes"])
                or not isinstance(record["family"], str)
                or not isinstance(record["issued_at"], int)
                or not isinstance(record["expires_at"], int)
                or not isinstance(record["active"], bool)
                or not isinstance(record["revoked"], bool)
            ):
                raise self._snapshot_invalid("invalid binding record value")

        revoked_families = state["revoked_families"]
        if not isinstance(revoked_families, (list, set, tuple)):
            raise self._snapshot_invalid("invalid revoked-family set")
        if any(not isinstance(family, str) for family in revoked_families):
            raise self._snapshot_invalid("invalid revoked-family entry")

        counters = state["counters"]
        if not isinstance(counters, dict) or any(
            key not in counters for key in _STAT_KEYS
        ):
            raise self._snapshot_invalid("invalid counters")
        for key in _STAT_KEYS:
            value = counters[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise self._snapshot_invalid("invalid counter value")

        verify_streak = state["verify_streak"]
        locked_until = state["verify_locked_until"]
        clock = state["clock"]
        if (
            not isinstance(verify_streak, int)
            or isinstance(verify_streak, bool)
            or verify_streak < 0
            or not (locked_until is None or isinstance(locked_until, int))
            or isinstance(locked_until, bool)
            or not isinstance(clock, int)
            or isinstance(clock, bool)
        ):
            raise self._snapshot_invalid("invalid verification state")

        return {
            "config": config,
            "devices": devices,
            "user_codes": user_codes,
            "refreshes": refreshes,
            "bindings": bindings,
            "revoked_families": set(revoked_families),
            "counters": counters,
            "verify_streak": verify_streak,
            "verify_locked_until": locked_until,
            "clock": clock,
        }
