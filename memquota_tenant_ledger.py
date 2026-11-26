"""Tenant memory quota ledger: per-tenant accounting, overcommit reclaim in
priority order and OOM victim selection."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass


class MemQuotaError(RuntimeError):
    """Raised when a quota operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Charge:
    charge_id: str
    tenant: str
    pid: int
    bytes: int
    seq: int


@dataclass(frozen=True)
class OomAction:
    tenant: str
    pid: int
    charge_id: str
    bytes: int
    priority: int
    reason: str


@dataclass(frozen=True)
class EnforceReport:
    reclaimed: tuple[str, ...]
    oom: OomAction | None
    usage_before: int
    usage_after: int
    unresolved: bool


OOM_REASON = "MEM_OOM_OVER_LIMIT"
SNAPSHOT_VERSION = 1
_STATS_KEYS = (
    "tenants",
    "charges",
    "usage",
    "global_limit",
    "enforce_calls",
    "reclaimed_charges",
    "oom_selected",
)
_SNAPSHOT_KEYS = {
    "version",
    "global_limit",
    "tenants",
    "charges",
    "counters",
    "stats",
}


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_integer(value: object) -> bool:
    return _is_integer(value) and value > 0


def _is_non_negative_integer(value: object) -> bool:
    return _is_integer(value) and value >= 0


def _is_valid_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _invalid_request(detail: object) -> MemQuotaError:
    return MemQuotaError("MEM_INVALID_REQUEST", detail)


def _invalid_snapshot(detail: object = "invalid snapshot state") -> MemQuotaError:
    return MemQuotaError("MEM_SNAPSHOT_INVALID", detail)


@dataclass
class _TenantRecord:
    name: str
    quota: int
    floor: int
    priority: int
    usage: int = 0


class MemQuotaTenantLedger:
    """Account memory charges per tenant under one global limit."""

    def __init__(self, *, global_limit: int, max_tenants: int = 16) -> None:
        if not _is_positive_integer(global_limit):
            raise _invalid_request({"field": "global_limit"})
        if not _is_positive_integer(max_tenants):
            raise _invalid_request({"field": "max_tenants"})

        self.global_limit = global_limit
        self.max_tenants = max_tenants
        self._lock = threading.RLock()
        self._tenants: dict[str, _TenantRecord] = {}
        self._charges: dict[str, Charge] = {}
        self._charge_seq = 0
        self._global_usage = 0
        self._enforce_calls = 0
        self._reclaimed_charges = 0
        self._oom_selected = 0

    def add_tenant(self, name: str, *, quota: int, floor: int = 0,
                   priority: int = 100) -> None:
        with self._lock:
            if not _is_valid_name(name):
                raise _invalid_request({"field": "name"})
            if not _is_positive_integer(quota):
                raise _invalid_request({"field": "quota"})
            if not _is_non_negative_integer(floor) or floor > quota:
                raise _invalid_request({"field": "floor"})
            if not _is_non_negative_integer(priority):
                raise _invalid_request({"field": "priority"})
            if name in self._tenants:
                raise MemQuotaError(
                    "MEM_TENANT_EXISTS", {"tenant": name})
            if len(self._tenants) >= self.max_tenants:
                raise MemQuotaError(
                    "MEM_CAPACITY_EXCEEDED",
                    {"max_tenants": self.max_tenants},
                )

            self._tenants[name] = _TenantRecord(
                name=name,
                quota=quota,
                floor=floor,
                priority=priority,
            )

    def remove_tenant(self, name: str) -> int:
        with self._lock:
            tenant = self._tenant_or_raise(name)
            charge_count = sum(
                1 for charge in self._charges.values()
                if charge.tenant == name
            )
            if tenant.usage != 0 or charge_count:
                raise MemQuotaError(
                    "MEM_TENANT_BUSY",
                    {
                        "tenant": name,
                        "usage": tenant.usage,
                        "charges": charge_count,
                    },
                )

            del self._tenants[name]
            return tenant.usage

    def charge(self, tenant: str, *, pid: int, bytes: int) -> Charge:
        with self._lock:
            if not _is_valid_name(tenant):
                raise _invalid_request({"field": "tenant"})
            tenant_record = self._tenants.get(tenant)
            if tenant_record is None:
                raise MemQuotaError(
                    "MEM_TENANT_UNKNOWN", {"tenant": tenant})
            if not _is_positive_integer(pid):
                raise _invalid_request({"field": "pid"})
            if not _is_positive_integer(bytes):
                raise _invalid_request({"field": "bytes"})

            used = tenant_record.usage
            if used + bytes > tenant_record.quota:
                raise MemQuotaError(
                    "MEM_QUOTA_EXCEEDED",
                    {
                        "tenant": tenant,
                        "used": used,
                        "requested": bytes,
                        "quota": tenant_record.quota,
                    },
                )

            self._charge_seq += 1
            seq = self._charge_seq
            charge = Charge(
                charge_id=f"chg-{seq}",
                tenant=tenant,
                pid=pid,
                bytes=bytes,
                seq=seq,
            )
            self._charges[charge.charge_id] = charge
            tenant_record.usage += bytes
            self._global_usage += bytes
            return charge

    def release(self, charge_id: str) -> int:
        with self._lock:
            return self._release_locked(charge_id)

    def usage(self) -> int:
        with self._lock:
            return self._global_usage

    def tenant_usage(self, name: str) -> int:
        with self._lock:
            return self._tenant_or_raise(name).usage

    def charges(self, tenant: str | None = None) -> tuple[Charge, ...]:
        with self._lock:
            values = self._charges.values()
            if tenant is not None:
                values = (charge for charge in values
                          if charge.tenant == tenant)
            return tuple(sorted(values, key=lambda charge: charge.seq))

    def enforce(self) -> EnforceReport:
        with self._lock:
            self._enforce_calls += 1
            usage_before = self._global_usage
            reclaimed: list[str] = []

            if self._global_usage > self.global_limit:
                while self._global_usage > self.global_limit:
                    target = self._reclaim_target_locked()
                    if target is None:
                        break

                    self._release_locked(target.charge_id)
                    reclaimed.append(target.charge_id)
                    self._reclaimed_charges += 1

            oom: OomAction | None = None
            if self._global_usage > self.global_limit:
                target = self._oom_target_locked()
                if target is not None:
                    oom = self._oom_action_locked(target)
                    self._oom_selected += 1

            usage_after = self._global_usage
            unresolved = (
                self._global_usage > self.global_limit and oom is None
            )
            return EnforceReport(
                reclaimed=tuple(reclaimed),
                oom=oom,
                usage_before=usage_before,
                usage_after=usage_after,
                unresolved=unresolved,
            )

    def oom_candidates(self, *, limit: int = 3) -> tuple[OomAction, ...]:
        with self._lock:
            if not _is_positive_integer(limit):
                raise _invalid_request({"field": "limit"})

            ordered = sorted(
                self._charges.values(),
                key=self._victim_sort_key_locked,
            )
            return tuple(
                self._oom_action_locked(charge)
                for charge in ordered[:limit]
            )

    def resize_global_limit(self, limit: int) -> None:
        with self._lock:
            if not _is_positive_integer(limit):
                raise _invalid_request({"field": "limit"})
            self.global_limit = limit

    def snapshot(self) -> dict:
        with self._lock:
            state = {
                "version": SNAPSHOT_VERSION,
                "global_limit": self.global_limit,
                "tenants": {
                    name: {
                        "quota": tenant.quota,
                        "floor": tenant.floor,
                        "priority": tenant.priority,
                    }
                    for name, tenant in self._tenants.items()
                },
                "charges": [
                    {
                        "charge_id": charge.charge_id,
                        "tenant": charge.tenant,
                        "pid": charge.pid,
                        "bytes": charge.bytes,
                        "seq": charge.seq,
                    }
                    for charge in sorted(
                        self._charges.values(),
                        key=lambda item: item.seq,
                    )
                ],
                "counters": {
                    "charge_seq": self._charge_seq,
                },
                "stats": self.stats(),
            }
            return copy.deepcopy(state)

    def restore_state(self, state: object) -> None:
        with self._lock:
            parsed = self._parse_snapshot(state)
            (
                global_limit,
                tenants,
                charges,
                charge_seq,
                enforce_calls,
                reclaimed_charges,
                oom_selected,
            ) = parsed

            self.global_limit = global_limit
            self._tenants = tenants
            self._charges = {
                charge.charge_id: charge for charge in charges
            }
            self._charge_seq = charge_seq
            self._global_usage = sum(charge.bytes for charge in charges)
            self._enforce_calls = enforce_calls
            self._reclaimed_charges = reclaimed_charges
            self._oom_selected = oom_selected

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "tenants": len(self._tenants),
                "charges": len(self._charges),
                "usage": self._global_usage,
                "global_limit": self.global_limit,
                "enforce_calls": self._enforce_calls,
                "reclaimed_charges": self._reclaimed_charges,
                "oom_selected": self._oom_selected,
            }

    def _tenant_or_raise(self, name: object) -> _TenantRecord:
        if not _is_valid_name(name) or name not in self._tenants:
            raise MemQuotaError("MEM_TENANT_UNKNOWN", {"tenant": name})
        return self._tenants[name]  # type: ignore[index]

    def _release_locked(self, charge_id: object) -> int:
        if not isinstance(charge_id, str):
            raise MemQuotaError(
                "MEM_CHARGE_UNKNOWN", {"charge_id": charge_id})
        charge = self._charges.pop(
            charge_id, None
        )
        if charge is None:
            raise MemQuotaError(
                "MEM_CHARGE_UNKNOWN", {"charge_id": charge_id})

        tenant = self._tenants.get(charge.tenant)
        if tenant is not None:
            tenant.usage -= charge.bytes
        self._global_usage -= charge.bytes
        return charge.bytes

    def _victim_sort_key_locked(
        self, charge: Charge
    ) -> tuple[int, int, int]:
        tenant = self._tenants[charge.tenant]
        return (-tenant.priority, -charge.bytes, charge.seq)

    def _reclaim_target_locked(self) -> Charge | None:
        candidates = []
        for charge in self._charges.values():
            tenant = self._tenants[charge.tenant]
            if tenant.usage - charge.bytes >= tenant.floor:
                candidates.append(charge)
        if not candidates:
            return None
        return min(candidates, key=self._victim_sort_key_locked)

    def _oom_target_locked(self) -> Charge | None:
        if not self._charges:
            return None
        return min(
            self._charges.values(),
            key=self._victim_sort_key_locked,
        )

    def _oom_action_locked(self, charge: Charge) -> OomAction:
        tenant = self._tenants[charge.tenant]
        return OomAction(
            tenant=charge.tenant,
            pid=charge.pid,
            charge_id=charge.charge_id,
            bytes=charge.bytes,
            priority=tenant.priority,
            reason=OOM_REASON,
        )

    def _parse_snapshot(
        self, state: object
    ) -> tuple[
        int,
        dict[str, _TenantRecord],
        tuple[Charge, ...],
        int,
        int,
        int,
        int,
    ]:
        if (
            not isinstance(state, dict)
            or set(state.keys()) != _SNAPSHOT_KEYS
        ):
            raise _invalid_snapshot("top-level keys do not match snapshot")

        version = state["version"]
        global_limit = state["global_limit"]
        tenant_data = state["tenants"]
        charge_data = state["charges"]
        counters = state["counters"]
        stats_data = state["stats"]

        if not _is_integer(version) or version != SNAPSHOT_VERSION:
            raise _invalid_snapshot("unsupported snapshot version")
        if not _is_positive_integer(global_limit):
            raise _invalid_snapshot("invalid global_limit")
        if not isinstance(tenant_data, dict):
            raise _invalid_snapshot("tenants must be an object")
        if not isinstance(charge_data, list):
            raise _invalid_snapshot("charges must be a list")
        if not isinstance(counters, dict):
            raise _invalid_snapshot("counters must be an object")
        if not isinstance(stats_data, dict):
            raise _invalid_snapshot("stats must be an object")
        if set(counters.keys()) != {"charge_seq"}:
            raise _invalid_snapshot("invalid counters")
        if not _is_non_negative_integer(counters["charge_seq"]):
            raise _invalid_snapshot("invalid charge_seq")
        if set(stats_data.keys()) != set(_STATS_KEYS):
            raise _invalid_snapshot("invalid stats keys")

        tenants: dict[str, _TenantRecord] = {}
        for name, values in tenant_data.items():
            if not _is_valid_name(name) or not isinstance(values, dict):
                raise _invalid_snapshot("invalid tenant record")
            if set(values.keys()) != {"quota", "floor", "priority"}:
                raise _invalid_snapshot("invalid tenant fields")

            quota = values["quota"]
            floor = values["floor"]
            priority = values["priority"]
            if not _is_positive_integer(quota):
                raise _invalid_snapshot("invalid tenant quota")
            if (
                not _is_non_negative_integer(floor)
                or floor > quota
            ):
                raise _invalid_snapshot("invalid tenant floor")
            if not _is_non_negative_integer(priority):
                raise _invalid_snapshot("invalid tenant priority")

            tenants[name] = _TenantRecord(
                name=name,
                quota=quota,
                floor=floor,
                priority=priority,
            )

        charges: list[Charge] = []
        seen_charge_ids: set[str] = set()
        seen_seqs: set[int] = set()
        for item in charge_data:
            if not isinstance(item, dict):
                raise _invalid_snapshot("invalid charge record")
            required = {"charge_id", "tenant", "pid", "bytes", "seq"}
            if set(item.keys()) != required:
                raise _invalid_snapshot("invalid charge fields")

            charge_id = item["charge_id"]
            tenant_name = item["tenant"]
            pid = item["pid"]
            byte_count = item["bytes"]
            seq = item["seq"]

            if (
                not _is_integer(seq)
                or seq <= 0
                or seq in seen_seqs
            ):
                raise _invalid_snapshot("invalid charge sequence")
            if not isinstance(charge_id, str):
                raise _invalid_snapshot("invalid charge id")
            if (
                charge_id != f"chg-{seq}"
                or charge_id in seen_charge_ids
            ):
                raise _invalid_snapshot("invalid charge id")
            if (
                not _is_valid_name(tenant_name)
                or tenant_name not in tenants
            ):
                raise _invalid_snapshot("unknown charge tenant")
            if not _is_positive_integer(pid):
                raise _invalid_snapshot("invalid charge pid")
            if not _is_positive_integer(byte_count):
                raise _invalid_snapshot("invalid charge bytes")

            charge = Charge(
                charge_id=charge_id,
                tenant=tenant_name,
                pid=pid,
                bytes=byte_count,
                seq=seq,
            )
            charges.append(charge)
            seen_charge_ids.add(charge_id)
            seen_seqs.add(seq)
            tenants[tenant_name].usage += byte_count

        charge_seq = counters["charge_seq"]
        if charge_seq < max(seen_seqs, default=0):
            raise _invalid_snapshot("charge_seq is behind charges")

        global_usage = sum(charge.bytes for charge in charges)
        for tenant in tenants.values():
            if tenant.usage > tenant.quota:
                raise _invalid_snapshot("tenant usage exceeds quota")

        expected_stats = {
            "tenants": len(tenants),
            "charges": len(charges),
            "usage": global_usage,
            "global_limit": global_limit,
        }
        for key, expected in expected_stats.items():
            if stats_data[key] != expected:
                raise _invalid_snapshot(f"stats mismatch: {key}")

        cumulative = {}
        for key in (
            "enforce_calls",
            "reclaimed_charges",
            "oom_selected",
        ):
            value = stats_data[key]
            if not _is_non_negative_integer(value):
                raise _invalid_snapshot(f"invalid stats field: {key}")
            cumulative[key] = value

        return (
            global_limit,
            tenants,
            tuple(charges),
            charge_seq,
            cumulative["enforce_calls"],
            cumulative["reclaimed_charges"],
            cumulative["oom_selected"],
        )
