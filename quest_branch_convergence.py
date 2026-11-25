"""Quest chain branch accounting with merge gates and one-time rewards."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass


class QuestError(ValueError):
    """Raised when a quest chain operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class NodeProgress:
    node_id: str
    progress: int
    target: int
    completed: bool
    reach_count: int
    reward_granted: bool


class QuestBranchConvergence:
    """Track parallel quest branches, merge gates and shared node rewards."""

    def __init__(self, *, max_branches: int = 8) -> None:
        self.max_branches = max_branches
        self._lock = threading.RLock()

    def start_chain(self, chain_id: str, *, branches: dict[str, list[str]],
                    targets: dict[str, int],
                    rewards: dict[str, int] | None = None,
                    merges: dict[str, list[str]] | None = None) -> None:
        raise NotImplementedError

    def advance_branch(self, chain_id: str, branch_id: str, node_id: str,
                       delta: int, event_id: str) -> dict:
        raise NotImplementedError

    def check_merge(self, chain_id: str, merge_id: str) -> bool:
        raise NotImplementedError

    def unlock_merge(self, chain_id: str, merge_id: str) -> int:
        raise NotImplementedError

    def progress(self, chain_id: str) -> dict:
        raise NotImplementedError

    def node_state(self, chain_id: str, node_id: str) -> NodeProgress:
        raise NotImplementedError

    def reward_total(self, chain_id: str) -> int:
        raise NotImplementedError

    def abandon_chain(self, chain_id: str) -> None:
        raise NotImplementedError

    def resume_chain(self, chain_id: str) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
