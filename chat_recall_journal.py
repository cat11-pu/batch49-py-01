"""Chat recall journal: tombstones, edit version chains and channel sequence."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ChatError(ValueError):
    """Raised when a chat journal operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ChatMessage:
    seq: int
    channel: str
    author: str
    body: str
    version: int
    posted_at: str
    edited_at: object = None


@dataclass(frozen=True)
class Tombstone:
    seq: int
    channel: str
    author: str
    recalled_at: str
    recalled_by: str


class ChatRecallJournal:
    """Channel journals with recall tombstones and optimistic edit versions."""

    def __init__(self, *, recall_window_seconds: int = 120,
                 max_versions: int = 5, body_limit: int = 500,
                 max_channels: int = 64) -> None:
        self.recall_window_seconds = recall_window_seconds
        self.max_versions = max_versions
        self.body_limit = body_limit
        self.max_channels = max_channels
        self._lock = threading.RLock()

    def post(self, channel: str, author: str, body: str, *, at: str) -> ChatMessage:
        raise NotImplementedError

    def edit(self, channel: str, seq: int, author: str, body: str,
             *, expected_version: int, at: str) -> ChatMessage:
        raise NotImplementedError

    def recall(self, channel: str, seq: int, actor: str, *, at: str) -> Tombstone:
        raise NotImplementedError

    def history(self, channel: str, seq: int, *, role: str = "member") -> list:
        raise NotImplementedError

    def fetch_range(self, channel: str, start: int, end: int) -> list:
        raise NotImplementedError

    def seq_of(self, channel: str) -> int:
        raise NotImplementedError

    def latest(self, channel: str, seq: int) -> dict:
        raise NotImplementedError

    def channels(self) -> list:
        raise NotImplementedError

    def audit_entries(self) -> list:
        raise NotImplementedError

    def set_version_limit(self, n: int) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
