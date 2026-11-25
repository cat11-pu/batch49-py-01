"""Dependency vulnerability reachability: call graph traversal, evidence
chains and fix priority planning."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ReachabilityError(ValueError):
    """Raised when a graph or vulnerability operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Finding:
    vuln_id: str
    symbol: str
    package: str
    severity: str
    reachable: bool
    depth: int
    evidence: tuple[str, ...]
    entry: str | None
    fixed_in: str | None
    priority: int


class ReachabilityFixPlanner:
    """Build a call graph, trace entry-to-sink evidence and rank fixes."""

    def __init__(self, *, max_nodes: int = 256,
                 max_edges: int = 1024) -> None:
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self._lock = threading.RLock()

    def add_node(self, name: str, *, package: str,
                 version: str = "0.0.0") -> None:
        raise NotImplementedError

    def add_edge(self, caller: str, callee: str) -> None:
        raise NotImplementedError

    def mark_entry(self, node: str, *, route: str = "") -> None:
        raise NotImplementedError

    def add_vulnerability(self, vuln_id: str, *, symbol: str, severity: str,
                          fixed_in: str | None = None) -> None:
        raise NotImplementedError

    def analyze(self) -> tuple[Finding, ...]:
        raise NotImplementedError

    def plan(self, *, limit: int | None = None) -> tuple[str, ...]:
        raise NotImplementedError

    def evidence_for(self, vuln_id: str) -> tuple[str, ...]:
        raise NotImplementedError

    def remove_edge(self, caller: str, callee: str) -> bool:
        raise NotImplementedError

    def remove_vulnerability(self, vuln_id: str) -> bool:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore_state(self, state: object) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError
