"""Bidirectional registry between capability names and 64-bit bit numbers."""

from __future__ import annotations

import threading

SUPPORTED_SCHEMA = 1


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
        self.max_name_length = max_name_length
        self.max_aliases = max_aliases
        self._lock = threading.RLock()
        self._names: dict = {}
        self._bits: dict = {}
        self._aliases: dict = {}
        self._version = 1

    def register(self, name, *, bit, aliases=()):
        raise NotImplementedError

    def unregister(self, name):
        raise NotImplementedError

    def encode(self, names):
        raise NotImplementedError

    def decode(self, mask):
        raise NotImplementedError

    def set_ops(self, op, left, right):
        raise NotImplementedError

    def resolve(self, name):
        raise NotImplementedError

    def aliases(self, name):
        raise NotImplementedError

    def table(self):
        raise NotImplementedError

    def version(self):
        raise NotImplementedError

    def snapshot(self):
        raise NotImplementedError

    def restore(self, state):
        raise NotImplementedError
