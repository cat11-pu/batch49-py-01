"""Low-latency HLS part publisher: part cutting, playlist versions, blocking reload."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PartPublishError(ValueError):
    """Refused part publishing operation carrying a stable error code."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


DEFAULT_PART_TARGET_MS = 500
DEFAULT_MIN_PART_MS = 200
DEFAULT_HOLD_BACK_PARTS = 2
DEFAULT_WINDOW_PARTS = 6
DEFAULT_MAX_PENDING_PARTS = 64
DEFAULT_BLOCKING_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class Chunk:
    data: bytes
    duration_ms: int


@dataclass(frozen=True)
class Part:
    media_seq: int
    part_index: int
    duration_ms: int
    size: int


class LlHlsPartPublisher:
    """Cut incoming chunks into independently decodable parts and publish playlists."""

    def __init__(
        self,
        part_target_ms: int = DEFAULT_PART_TARGET_MS,
        min_part_ms: int = DEFAULT_MIN_PART_MS,
        hold_back_parts: int = DEFAULT_HOLD_BACK_PARTS,
        window_parts: int = DEFAULT_WINDOW_PARTS,
        max_pending_parts: int = DEFAULT_MAX_PENDING_PARTS,
        blocking_timeout_ms: int = DEFAULT_BLOCKING_TIMEOUT_MS,
    ) -> None:
        self.part_target_ms = part_target_ms
        self.min_part_ms = min_part_ms
        self.hold_back_parts = hold_back_parts
        self.window_parts = window_parts
        self.max_pending_parts = max_pending_parts
        self.blocking_timeout_ms = blocking_timeout_ms
        self._lock = threading.RLock()

    def push_chunk(self, data: bytes, duration_ms: int) -> int:
        raise NotImplementedError

    def cut_part(self, media_seq: int) -> tuple[int, int] | None:
        raise NotImplementedError

    def publish(self, media_seq: int) -> dict:
        raise NotImplementedError

    def playlist(self, media_seq: int | None = None) -> dict:
        raise NotImplementedError

    def blocking_reload(self, after_version: int, timeout_ms: int | None = None) -> dict:
        raise NotImplementedError

    def preload_hint(self, media_seq: int = 0) -> dict | None:
        raise NotImplementedError

    def hold_back(self) -> int:
        raise NotImplementedError

    def pending_count(self) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
