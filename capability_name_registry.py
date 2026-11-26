"""Bidirectional registry between capability names and 64-bit bit numbers."""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping

SUPPORTED_SCHEMA = 1
_BIT_COUNT = 64
_MAX_MASK = (1 << _BIT_COUNT) - 1
_UNKNOWN_PREFIX = "unknown:"


class CapRegistryError(ValueError):
    """Refusal raised by the capability name registry."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class CapabilityNameRegistry:
    """Map capability names and aliases onto bits 0..63 in both directions."""

    def __init__(self, *, max_name_length: int = 32,
                 max_aliases: int = 8) -> None:
        if (isinstance(max_name_length, bool)
                or not isinstance(max_name_length, int)):
            raise CapRegistryError("INVALID_REQUEST", max_name_length)
        if max_name_length < 1:
            raise CapRegistryError("INVALID_REQUEST", max_name_length)
        if isinstance(max_aliases, bool) or not isinstance(max_aliases, int):
            raise CapRegistryError("INVALID_REQUEST", max_aliases)
        if max_aliases < 0:
            raise CapRegistryError("INVALID_REQUEST", max_aliases)
        self.max_name_length = max_name_length
        self.max_aliases = max_aliases
        self._lock = threading.RLock()
        self._names: dict[str, int] = {}
        self._bits: dict[int, str] = {}
        self._aliases: dict[str, str] = {}
        self._version = 1

    def register(self, name, *, bit, aliases=()):
        self._validate_bit(bit, "BIT_RANGE")
        self._validate_name(name)
        alias_items = self._normalize_aliases(aliases)

        with self._lock:
            if name in self._names:
                raise CapRegistryError("NAME_TAKEN", name)
            if name in self._aliases:
                raise CapRegistryError("ALIAS_TAKEN", name)
            if bit in self._bits:
                raise CapRegistryError("BIT_TAKEN", bit)

            seen = set()
            for alias in alias_items:
                if alias in seen:
                    raise CapRegistryError("ALIAS_TAKEN", alias)
                seen.add(alias)
                if alias == name or alias in self._names or alias in self._aliases:
                    raise CapRegistryError("ALIAS_TAKEN", alias)

            self._names[name] = bit
            self._bits[bit] = name
            for alias in alias_items:
                self._aliases[alias] = name
            self._version += 1

    def unregister(self, name):
        if not isinstance(name, str):
            raise CapRegistryError("INVALID_REQUEST", name)

        with self._lock:
            canonical = self._resolve_unlocked(name)
            bit = self._names[canonical]
            del self._names[canonical]
            del self._bits[bit]
            for alias, target in tuple(self._aliases.items()):
                if target == canonical:
                    del self._aliases[alias]
            self._version += 1

    def encode(self, names):
        requested = self._normalize_names(names)

        with self._lock:
            mask = 0
            unknown = set()
            for name in requested:
                bit = self._lookup_bit_unlocked(name)
                if bit is None:
                    unknown.add(name)
                else:
                    mask |= 1 << bit
            if unknown:
                raise CapRegistryError("UNKNOWN_NAME", tuple(sorted(unknown)))
            return mask

    def decode(self, mask):
        self._validate_mask(mask)

        with self._lock:
            result = []
            for bit in range(_BIT_COUNT):
                if mask & (1 << bit):
                    name = self._bits.get(bit)
                    result.append(name if name is not None
                                  else f"{_UNKNOWN_PREFIX}{bit}")
            return tuple(result)

    def set_ops(self, op, left, right):
        if not isinstance(op, str) or op not in ("and", "or", "xor", "diff"):
            raise CapRegistryError("INVALID_OP", op)

        left_names = self._normalize_names(left)
        right_names = self._normalize_names(right)

        with self._lock:
            left_mask, left_unknown = self._names_to_mask_unlocked(left_names)
            right_mask, right_unknown = self._names_to_mask_unlocked(right_names)
            unknown = left_unknown | right_unknown
            if unknown:
                raise CapRegistryError("UNKNOWN_NAME", tuple(sorted(unknown)))

            if op == "and":
                result_mask = left_mask & right_mask
            elif op == "or":
                result_mask = left_mask | right_mask
            elif op == "xor":
                result_mask = left_mask ^ right_mask
            else:
                result_mask = left_mask & ~right_mask
            return self._mask_to_names_unlocked(result_mask)

    def resolve(self, name):
        if not isinstance(name, str):
            raise CapRegistryError("INVALID_REQUEST", name)
        with self._lock:
            return self._resolve_unlocked(name)

    def aliases(self, name):
        if not isinstance(name, str):
            raise CapRegistryError("INVALID_REQUEST", name)
        with self._lock:
            canonical = self._resolve_unlocked(name)
            return tuple(alias for alias, target in self._aliases.items()
                         if target == canonical)

    def table(self):
        with self._lock:
            return {
                "version": self._version,
                "names": copy.deepcopy(self._names),
                "aliases": copy.deepcopy(self._aliases),
                "bits": copy.deepcopy(self._bits),
            }

    def version(self):
        with self._lock:
            return self._version

    def snapshot(self):
        with self._lock:
            return {
                "schema": SUPPORTED_SCHEMA,
                "version": self._version,
                "names": copy.deepcopy(self._names),
                "aliases": copy.deepcopy(self._aliases),
            }

    def restore(self, state):
        names, aliases, snapshot_version = self._validate_snapshot(state)

        with self._lock:
            conflicts = self._snapshot_conflicts_unlocked(names, aliases)
            if conflicts:
                raise CapRegistryError("SNAPSHOT_CONFLICT", conflicts)

            bits = {bit: name for name, bit in names.items()}
            self._names = names
            self._aliases = aliases
            self._bits = bits
            self._version = max(self._version, snapshot_version)

    def _validate_name(self, name):
        if not isinstance(name, str) or not name or any(
                char.isspace() for char in name):
            raise CapRegistryError("INVALID_REQUEST", name)
        if len(name) > self.max_name_length:
            raise CapRegistryError("NAME_LIMIT", name)
        return name

    @staticmethod
    def _validate_bit(bit, range_code):
        if isinstance(bit, bool) or not isinstance(bit, int):
            raise CapRegistryError("INVALID_REQUEST", bit)
        if bit < 0 or bit >= _BIT_COUNT:
            raise CapRegistryError(range_code, bit)
        return bit

    @staticmethod
    def _validate_mask(mask):
        if isinstance(mask, bool) or not isinstance(mask, int):
            raise CapRegistryError("INVALID_REQUEST", mask)
        if mask < 0 or mask > _MAX_MASK:
            raise CapRegistryError("MASK_RANGE", mask)
        return mask

    def _normalize_aliases(self, aliases):
        if isinstance(aliases, str):
            aliases = (aliases,)
        try:
            alias_items = tuple(aliases)
        except TypeError:
            raise CapRegistryError("INVALID_REQUEST", aliases) from None
        if len(alias_items) > self.max_aliases:
            raise CapRegistryError("ALIAS_LIMIT", len(alias_items))
        for alias in alias_items:
            self._validate_name(alias)
        return alias_items

    @staticmethod
    def _normalize_names(names):
        if isinstance(names, str):
            names = (names,)
        try:
            items = tuple(names)
        except TypeError:
            raise CapRegistryError("INVALID_REQUEST", names) from None
        for name in items:
            if not isinstance(name, str):
                raise CapRegistryError("INVALID_REQUEST", name)
        return items

    def _resolve_unlocked(self, name):
        if name in self._names:
            return name
        canonical = self._aliases.get(name)
        if canonical is not None:
            return canonical
        raise CapRegistryError("NAME_UNKNOWN", name)

    def _lookup_bit_unlocked(self, name):
        if name in self._names:
            return self._names[name]
        canonical = self._aliases.get(name)
        if canonical is not None:
            return self._names[canonical]
        return self._parse_unknown_name(name)

    @staticmethod
    def _parse_unknown_name(name):
        if not name.startswith(_UNKNOWN_PREFIX):
            return None
        suffix = name[len(_UNKNOWN_PREFIX):]
        if (not suffix or len(suffix) > 2
                or not suffix.isascii() or not suffix.isdigit()):
            return None
        if len(suffix) > 1 and suffix.startswith("0"):
            return None
        bit = int(suffix)
        if bit < 0 or bit >= _BIT_COUNT:
            return None
        return bit

    def _names_to_mask_unlocked(self, names):
        mask = 0
        unknown = set()
        for name in names:
            bit = self._lookup_bit_unlocked(name)
            if bit is None:
                unknown.add(name)
            else:
                mask |= 1 << bit
        return mask, unknown

    def _mask_to_names_unlocked(self, mask):
        result = []
        for bit in range(_BIT_COUNT):
            if mask & (1 << bit):
                name = self._bits.get(bit)
                result.append(name if name is not None
                              else f"{_UNKNOWN_PREFIX}{bit}")
        return tuple(result)

    def _validate_snapshot(self, state):
        if not isinstance(state, Mapping):
            raise CapRegistryError("SNAPSHOT_INVALID", state)
        if "schema" in state:
            schema = state["schema"]
            if isinstance(schema, bool) or not isinstance(schema, int):
                raise CapRegistryError("SNAPSHOT_INVALID", schema)
            if schema > SUPPORTED_SCHEMA:
                raise CapRegistryError("SNAPSHOT_VERSION_UNSUPPORTED", schema)

        required = ("schema", "version", "names", "aliases")
        if any(key not in state for key in required):
            raise CapRegistryError("SNAPSHOT_INVALID", state)

        schema = state["schema"]
        if schema < 1:
            raise CapRegistryError("SNAPSHOT_INVALID", schema)

        snapshot_version = state["version"]
        if (isinstance(snapshot_version, bool)
                or not isinstance(snapshot_version, int)
                or snapshot_version < 1):
            raise CapRegistryError("SNAPSHOT_INVALID", snapshot_version)

        raw_names = state["names"]
        raw_aliases = state["aliases"]
        if (not isinstance(raw_names, Mapping)
                or not isinstance(raw_aliases, Mapping)):
            raise CapRegistryError("SNAPSHOT_INVALID", state)

        names = {}
        used_bits = set()
        for name, bit in raw_names.items():
            self._snapshot_name(name)
            if isinstance(bit, bool) or not isinstance(bit, int):
                raise CapRegistryError("SNAPSHOT_INVALID", bit)
            if bit < 0 or bit >= _BIT_COUNT or bit in used_bits:
                raise CapRegistryError("SNAPSHOT_INVALID", bit)
            used_bits.add(bit)
            names[name] = bit

        aliases = {}
        alias_counts = {name: 0 for name in names}
        for alias, canonical in raw_aliases.items():
            self._snapshot_name(alias)
            if not isinstance(canonical, str) or canonical not in names:
                raise CapRegistryError("SNAPSHOT_INVALID", canonical)
            if alias in names:
                raise CapRegistryError("SNAPSHOT_INVALID", alias)
            alias_counts[canonical] += 1
            if alias_counts[canonical] > self.max_aliases:
                raise CapRegistryError("SNAPSHOT_INVALID", canonical)
            aliases[alias] = canonical

        return names, aliases, snapshot_version

    def _snapshot_name(self, name):
        try:
            self._validate_name(name)
        except CapRegistryError as exc:
            raise CapRegistryError("SNAPSHOT_INVALID", exc.detail) from None

    def _snapshot_conflicts_unlocked(self, names, aliases):
        conflicts = []
        for name, bit in names.items():
            current_name = self._bits.get(bit)
            if current_name is not None and current_name != name:
                conflicts.append((bit, current_name, name))
            current_bit = self._names.get(name)
            if current_bit is not None and current_bit != bit:
                conflicts.append((bit, name, current_bit))
            current_alias_target = self._aliases.get(name)
            if current_alias_target is not None and current_alias_target != name:
                conflicts.append((bit, current_alias_target, name))

        for alias, canonical in aliases.items():
            current_name = self._names.get(alias)
            if current_name is not None and current_name != canonical:
                conflicts.append((alias, current_name, canonical))
            current_alias_target = self._aliases.get(alias)
            if (current_alias_target is not None
                    and current_alias_target != canonical):
                conflicts.append((alias, current_alias_target, canonical))

        return tuple(conflicts)
