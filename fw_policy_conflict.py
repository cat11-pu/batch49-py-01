"""Firewall policy evaluation, first-match lookup and conflict analysis."""

from __future__ import annotations

import copy
import ipaddress
import threading
from typing import Any

_ALL_PORTS = ((1, 65535),)
_ACTIONS = frozenset({"allow", "deny"})
_RULE_PROTOCOLS = frozenset({"tcp", "udp", "icmp", "any"})
_QUERY_PROTOCOLS = frozenset({"tcp", "udp", "icmp"})


class FwPolicyError(ValueError):
    """Raised when a rule or query is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_decimal_text(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdigit()


def _normalize_name(name: object) -> str:
    if not isinstance(name, str):
        raise FwPolicyError("FW_RULE_INVALID", {"field": "name"})
    normalized = name.strip()
    if not normalized:
        raise FwPolicyError("FW_RULE_INVALID", {"field": "name"})
    return normalized


def _parse_endpoint(value: object, field: str) -> tuple[str, ipaddress.IPv4Network]:
    if not isinstance(value, str):
        raise FwPolicyError("FW_ADDRESS_INVALID", {"field": field, "value": value})

    text = value.strip()
    if text == "any":
        return "any", ipaddress.IPv4Network("0.0.0.0/0")

    try:
        if "/" in text:
            if text.count("/") != 1:
                raise ValueError("invalid CIDR")
            address_text, prefix_text = text.split("/", 1)
            if not _is_decimal_text(prefix_text):
                raise ValueError("invalid prefix")
            if len(prefix_text) > 1 and prefix_text[0] == "0":
                raise ValueError("non-canonical prefix")

            prefix = int(prefix_text)
            if not 0 <= prefix <= 32:
                raise ValueError("prefix out of range")

            address = ipaddress.IPv4Address(address_text)
            network = ipaddress.IPv4Network(f"{address}/{prefix}", strict=True)
            canonical = f"{network.network_address}/{network.prefixlen}"
            if text != canonical:
                raise ValueError("non-canonical CIDR")
            return canonical, network

        address = ipaddress.IPv4Address(text)
        canonical = f"{address}/32"
        return canonical, ipaddress.IPv4Network(canonical)
    except (ValueError, TypeError) as exc:
        raise FwPolicyError(
            "FW_ADDRESS_INVALID", {"field": field, "value": value}
        ) from exc


def _parse_port_number(text: str) -> int:
    value = text.strip()
    if not _is_decimal_text(value):
        raise FwPolicyError("FW_PORT_INVALID", {"port": text})
    number = int(value)
    if not 1 <= number <= 65535:
        raise FwPolicyError("FW_PORT_INVALID", {"port": text})
    return number


def _parse_port_ranges(ports: object, proto: str) -> tuple[tuple[int, int], ...]:
    if proto == "icmp":
        if ports is not None:
            raise FwPolicyError("FW_PORT_INVALID", {"ports": ports})
        return ()

    if ports is None:
        return ()
    if not isinstance(ports, str):
        raise FwPolicyError("FW_PORT_INVALID", {"ports": ports})

    text = ports.strip()
    if not text:
        raise FwPolicyError("FW_PORT_INVALID", {"ports": ports})

    ranges: list[tuple[int, int]] = []
    for raw_segment in text.split(","):
        segment = raw_segment.strip()
        if not segment:
            raise FwPolicyError("FW_PORT_INVALID", {"ports": ports})

        if "-" in segment:
            parts = segment.split("-")
            if len(parts) != 2:
                raise FwPolicyError("FW_PORT_INVALID", {"ports": ports})
            start = _parse_port_number(parts[0])
            end = _parse_port_number(parts[1])
            if start > end:
                raise FwPolicyError("FW_PORT_INVALID", {"ports": ports})
        else:
            start = end = _parse_port_number(segment)

        ranges.append((start, end))

    ranges.sort()
    merged: list[list[int]] = []
    for start, end in ranges:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    return tuple((start, end) for start, end in merged)


def _ranges_cover(
    outer: tuple[tuple[int, int], ...],
    inner: tuple[tuple[int, int], ...],
) -> bool:
    for inner_start, inner_end in inner:
        for outer_start, outer_end in outer:
            if outer_start <= inner_start and inner_end <= outer_end:
                break
        else:
            return False
    return True


def _ranges_overlap(
    left: tuple[tuple[int, int], ...],
    right: tuple[tuple[int, int], ...],
) -> bool:
    for left_start, left_end in left:
        for right_start, right_end in right:
            if left_start <= right_end and right_start <= left_end:
                return True
    return False


class FirewallPolicyAnalyzer:
    """Keep ordered rules, answer flow queries and report policy conflicts."""

    def __init__(
        self,
        *,
        default_action: str = "deny",
        max_rules: int = 256,
    ) -> None:
        if default_action not in _ACTIONS:
            raise FwPolicyError(
                "FW_RULE_INVALID",
                {"field": "default_action", "value": default_action},
            )
        if not _is_integer(max_rules) or max_rules < 0:
            raise FwPolicyError(
                "FW_RULE_INVALID", {"field": "max_rules", "value": max_rules}
            )

        self.default_action = default_action
        self.max_rules = max_rules
        self._lock = threading.RLock()
        self._rules: dict[str, dict[str, object]] = {}
        self._next_seq = 1
        self._stats = {"added": 0, "removed": 0, "queries": 0}

    def add_rule(
        self,
        name: str,
        *,
        action: str,
        src: str,
        dst: str,
        proto: str = "tcp",
        ports: str | None = None,
        priority: int = 100,
        enabled: bool = True,
        zone: str | None = None,
    ) -> dict:
        with self._lock:
            rule_name = _normalize_name(name)

            if action not in _ACTIONS:
                raise FwPolicyError(
                    "FW_RULE_INVALID", {"field": "action", "value": action}
                )
            if proto not in _RULE_PROTOCOLS:
                raise FwPolicyError("FW_PROTO_INVALID", {"proto": proto})
            if not _is_integer(priority):
                raise FwPolicyError(
                    "FW_RULE_INVALID", {"field": "priority", "value": priority}
                )
            if zone is not None and not isinstance(zone, str):
                raise FwPolicyError("FW_RULE_INVALID", {"field": "zone", "value": zone})

            src_text, src_network = _parse_endpoint(src, "src")
            dst_text, dst_network = _parse_endpoint(dst, "dst")
            port_ranges = _parse_port_ranges(ports, proto)

            if rule_name in self._rules:
                raise FwPolicyError("FW_RULE_EXISTS", {"name": rule_name})
            if len(self._rules) >= self.max_rules:
                raise FwPolicyError("FW_CAPACITY", {"max_rules": self.max_rules})

            rule = {
                "name": rule_name,
                "action": action,
                "src": src_text,
                "dst": dst_text,
                "proto": proto,
                "ports": ports.strip() if isinstance(ports, str) else ports,
                "port_ranges": port_ranges,
                "priority": priority,
                "enabled": bool(enabled),
                "zone": zone,
                "seq": self._next_seq,
                "src_network": src_network,
                "dst_network": dst_network,
            }
            self._next_seq += 1
            self._rules[rule_name] = rule
            self._stats["added"] += 1
            return self._rule_view(rule)

    def remove_rule(self, name: str) -> bool:
        with self._lock:
            if not isinstance(name, str):
                return False
            rule_name = name.strip()
            if not rule_name or rule_name not in self._rules:
                return False

            del self._rules[rule_name]
            self._stats["removed"] += 1
            return True

    def enable_rule(self, name: str, *, enabled: bool = True) -> bool:
        with self._lock:
            if not isinstance(name, str):
                return False
            rule_name = name.strip()
            rule = self._rules.get(rule_name)
            if rule is None:
                return False

            rule["enabled"] = bool(enabled)
            return True

    def query(
        self,
        src_ip: str,
        dst_ip: str,
        *,
        proto: str = "tcp",
        port: int | None = None,
        zone: str | None = None,
    ) -> dict:
        with self._lock:
            self._validate_query(src_ip, dst_ip, proto, port, zone)
            self._stats["queries"] += 1
            result, _ = self._evaluate_locked(src_ip, dst_ip, proto, port, zone)
            return result

    def explain(
        self,
        src_ip: str,
        dst_ip: str,
        *,
        proto: str = "tcp",
        port: int | None = None,
        zone: str | None = None,
    ) -> dict:
        with self._lock:
            self._validate_query(src_ip, dst_ip, proto, port, zone)
            self._stats["queries"] += 1
            result, evaluated = self._evaluate_locked(src_ip, dst_ip, proto, port, zone)
            explained = dict(result)
            explained["evaluated"] = evaluated
            return explained

    def conflicts(self) -> list:
        with self._lock:
            return self._conflicts_locked()

    def summary(self) -> dict:
        with self._lock:
            return {
                "rules": len(self._rules),
                "enabled": sum(bool(rule["enabled"]) for rule in self._rules.values()),
                "conflicts": len(self._conflicts_locked()),
                "default_action": self.default_action,
            }

    def snapshot(self) -> dict:
        with self._lock:
            rules = [
                self._snapshot_rule(rule)
                for rule in sorted(self._rules.values(), key=lambda item: item["seq"])
            ]
            return copy.deepcopy(
                {
                    "rules": rules,
                    "seq": self._next_seq,
                    "next_seq": self._next_seq,
                    "stats": dict(self._stats),
                }
            )

    def restore(self, state: dict) -> None:
        with self._lock:
            try:
                frozen_state = copy.deepcopy(state)
                candidate = self._candidate_from_state(frozen_state)
            except FwPolicyError as exc:
                if exc.code == "FW_SNAPSHOT_INVALID":
                    raise
                raise FwPolicyError(
                    "FW_SNAPSHOT_INVALID",
                    {"original_code": exc.code, "detail": exc.detail},
                ) from exc
            except Exception as exc:
                raise FwPolicyError(
                    "FW_SNAPSHOT_INVALID", {"reason": "invalid snapshot structure"}
                ) from exc

            self._rules = candidate["rules"]
            self._next_seq = candidate["next_seq"]
            self._stats = candidate["stats"]

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    @staticmethod
    def _rule_view(rule: dict[str, Any]) -> dict[str, Any]:
        view = {
            key: value
            for key, value in rule.items()
            if key not in {"src_network", "dst_network"}
        }
        view["port_ranges"] = [list(pair) for pair in rule["port_ranges"]]
        return copy.deepcopy(view)

    @staticmethod
    def _snapshot_rule(rule: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in rule.items()
            if key not in {"src_network", "dst_network"}
        }

    @staticmethod
    def _validate_query(
        src_ip: object,
        dst_ip: object,
        proto: object,
        port: object,
        zone: object,
    ) -> None:
        try:
            if not isinstance(src_ip, str):
                raise TypeError("src_ip must be a string")
            ipaddress.IPv4Address(src_ip)

            if not isinstance(dst_ip, str):
                raise TypeError("dst_ip must be a string")
            ipaddress.IPv4Address(dst_ip)
        except (ValueError, TypeError) as exc:
            raise FwPolicyError("FW_QUERY_INVALID", {"field": "address"}) from exc

        if proto not in _QUERY_PROTOCOLS:
            raise FwPolicyError("FW_QUERY_INVALID", {"field": "proto", "proto": proto})

        if proto in {"tcp", "udp"}:
            if not _is_integer(port) or not 1 <= port <= 65535:
                raise FwPolicyError("FW_QUERY_INVALID", {"field": "port", "port": port})
        elif proto == "icmp" and port is not None:
            raise FwPolicyError("FW_QUERY_INVALID", {"field": "port", "port": port})

        if zone is not None and not isinstance(zone, str):
            raise FwPolicyError("FW_QUERY_INVALID", {"field": "zone", "zone": zone})

    def _ordered_rules_locked(self, *, enabled_only: bool) -> list[dict[str, Any]]:
        rules = list(self._rules.values())
        if enabled_only:
            rules = [rule for rule in rules if rule["enabled"]]
        rules.sort(key=lambda rule: (rule["priority"], rule["seq"]))
        return rules

    @staticmethod
    def _zone_matches(rule_zone: str | None, query_zone: str | None) -> bool:
        return rule_zone is None or rule_zone == query_zone

    def _evaluate_locked(
        self,
        src_ip: str,
        dst_ip: str,
        proto: str,
        port: int | None,
        zone: str | None,
    ) -> tuple[dict[str, Any], list[str]]:
        source = ipaddress.IPv4Address(src_ip)
        destination = ipaddress.IPv4Address(dst_ip)
        evaluated: list[str] = []

        for rule in self._ordered_rules_locked(enabled_only=True):
            evaluated.append(rule["name"])
            if self._rule_matches(rule, source, destination, proto, port, zone):
                return (
                    {
                        "action": rule["action"],
                        "rule": rule["name"],
                        "default": False,
                    },
                    evaluated,
                )

        return (
            {
                "action": self.default_action,
                "rule": None,
                "default": True,
            },
            evaluated,
        )

    @staticmethod
    def _rule_matches(
        rule: dict[str, Any],
        source: ipaddress.IPv4Address,
        destination: ipaddress.IPv4Address,
        proto: str,
        port: int | None,
        query_zone: str | None = None,
    ) -> bool:
        if not FirewallPolicyAnalyzer._zone_matches(rule["zone"], query_zone):
            return False
        if source not in rule["src_network"]:
            return False
        if destination not in rule["dst_network"]:
            return False
        if rule["proto"] != "any" and rule["proto"] != proto:
            return False
        if not rule["port_ranges"]:
            return True
        return port is not None and any(
            start <= port <= end for start, end in rule["port_ranges"]
        )

    def _conflicts_locked(self) -> list[dict[str, str]]:
        rules = self._ordered_rules_locked(enabled_only=True)
        entries: list[tuple[int, int, dict[str, str]]] = []

        for earlier_index, earlier in enumerate(rules):
            for offset in range(1, len(rules) - earlier_index):
                later_index = earlier_index + offset
                later = rules[later_index]
                kind = self._pair_kind(earlier, later)
                if kind is None:
                    continue
                entries.append(
                    (
                        earlier_index,
                        later_index,
                        {
                            "kind": kind,
                            "earlier": earlier["name"],
                            "later": later["name"],
                        },
                    )
                )

        entries.sort(key=lambda entry: (entry[0], entry[1]))
        return [entry[2] for entry in entries]

    @classmethod
    def _pair_kind(cls, earlier: dict[str, Any], later: dict[str, Any]) -> str | None:
        if not cls._zones_can_conflict(earlier["zone"], later["zone"]):
            return None
        if not cls._spaces_overlap(earlier, later):
            return None
        if cls._rule_covers(earlier, later):
            if earlier["action"] == later["action"]:
                return "redundant"
            return "shadow"
        return "correlated"

    @staticmethod
    def _zones_can_conflict(first_zone: str | None, second_zone: str | None) -> bool:
        return first_zone is None or second_zone is None or first_zone == second_zone

    @classmethod
    def _rule_covers(cls, earlier: dict[str, Any], later: dict[str, Any]) -> bool:
        if not later["src_network"].subnet_of(earlier["src_network"]):
            return False
        if not later["dst_network"].subnet_of(earlier["dst_network"]):
            return False
        if earlier["proto"] != "any" and earlier["proto"] != later["proto"]:
            return False

        earlier_ports = earlier["port_ranges"] or _ALL_PORTS
        later_ports = later["port_ranges"] or _ALL_PORTS
        return _ranges_cover(earlier_ports, later_ports)

    @classmethod
    def _spaces_overlap(cls, earlier: dict[str, Any], later: dict[str, Any]) -> bool:
        if not earlier["src_network"].overlaps(later["src_network"]):
            return False
        if not earlier["dst_network"].overlaps(later["dst_network"]):
            return False
        if (
            earlier["proto"] != "any"
            and later["proto"] != "any"
            and earlier["proto"] != later["proto"]
        ):
            return False

        earlier_ports = earlier["port_ranges"] or _ALL_PORTS
        later_ports = later["port_ranges"] or _ALL_PORTS
        return _ranges_overlap(earlier_ports, later_ports)

    def _candidate_from_state(self, state: object) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise FwPolicyError("FW_SNAPSHOT_INVALID", {"reason": "state not object"})

        raw_rules = state.get("rules")
        if isinstance(raw_rules, dict):
            rule_items = list(raw_rules.items())
        elif isinstance(raw_rules, list):
            rule_items = [(None, raw_rule) for raw_rule in raw_rules]
        else:
            raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "rules"})

        raw_seq = state.get("seq", state.get("next_seq", state.get("sequence", 1)))
        if not _is_integer(raw_seq) or raw_seq < 1:
            raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "seq"})

        raw_stats = state.get("stats")
        if not isinstance(raw_stats, dict):
            raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "stats"})
        stats = copy.deepcopy(raw_stats)
        for key in ("added", "removed", "queries"):
            if not _is_integer(stats.get(key)) or stats[key] < 0:
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": f"stats.{key}"})

        rules: dict[str, dict[str, Any]] = {}
        max_seq = 0
        for dictionary_name, raw_rule in rule_items:
            if not isinstance(raw_rule, dict):
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "rules[]"})

            fields = dict(raw_rule)
            if dictionary_name is not None and not isinstance(fields.get("name"), str):
                fields["name"] = dictionary_name

            required = (
                "name",
                "action",
                "src",
                "dst",
                "proto",
                "priority",
                "enabled",
            )
            if any(field not in fields for field in required):
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "rules[]"})
            fields.setdefault("ports", None)
            fields.setdefault("zone", None)

            rule_name = _normalize_name(fields["name"])
            if fields["action"] not in _ACTIONS:
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "action"})
            if fields["proto"] not in _RULE_PROTOCOLS:
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "proto"})
            if not _is_integer(fields["priority"]):
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "priority"})
            if not isinstance(fields["enabled"], bool):
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "enabled"})
            if fields["zone"] is not None and not isinstance(fields["zone"], str):
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "zone"})

            port_ranges = _parse_port_ranges(fields["ports"], fields["proto"])
            src_text, src_network = _parse_endpoint(fields["src"], "src")
            dst_text, dst_network = _parse_endpoint(fields["dst"], "dst")

            raw_rule_seq = fields.get("seq")
            if not _is_integer(raw_rule_seq) or raw_rule_seq < 1:
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "seq"})
            if rule_name in rules:
                raise FwPolicyError("FW_SNAPSHOT_INVALID", {"field": "name"})

            ports_value = fields["ports"]
            rules[rule_name] = {
                "name": rule_name,
                "action": fields["action"],
                "src": src_text,
                "dst": dst_text,
                "proto": fields["proto"],
                "ports": ports_value if isinstance(ports_value, str) else None,
                "port_ranges": port_ranges,
                "priority": fields["priority"],
                "enabled": fields["enabled"],
                "zone": fields["zone"],
                "seq": raw_rule_seq,
                "src_network": src_network,
                "dst_network": dst_network,
            }
            max_seq = max(max_seq, raw_rule_seq)

        return {
            "rules": rules,
            "next_seq": max(raw_seq, max_seq + 1),
            "stats": stats,
        }
