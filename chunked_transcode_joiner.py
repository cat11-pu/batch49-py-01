"""Chunked parallel transcode planning, boundary verification, and joining."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ChunkError(ValueError):
    """Raised when a chunk plan, report, retry, or join is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ChunkSpec:
    index: int
    start_frame: int
    end_frame: int
    key_aligned: bool


@dataclass(frozen=True)
class ChunkReport:
    index: int
    start_frame: int
    end_frame: int
    params_hash: str
    keyframe_intact: bool = True


@dataclass(frozen=True)
class JoinResult:
    complete: bool
    chunk_count: int
    joined_frames: int
    truncated_frames: int
    reasons: tuple[str, ...]


class ChunkedTranscodeJoiner:
    """Own one chunk plan, its completion reports, retries, and the final join."""

    def __init__(self, *, max_chunk_frames: int = 300, max_chunks: int = 64,
                 max_retries: int = 2, max_total_retries: int = 5) -> None:
        self.max_chunk_frames = max_chunk_frames
        self.max_chunks = max_chunks
        self.max_retries = max_retries
        self.max_total_retries = max_total_retries
        self._lock = threading.RLock()

    def plan_chunks(self, total_frames: int, keyframes) -> list[ChunkSpec]:
        raise NotImplementedError

    def chunks(self) -> list[ChunkSpec]:
        raise NotImplementedError

    def mark_done(self, report: ChunkReport) -> str:
        raise NotImplementedError

    def verify_boundary(self, index: int) -> bool:
        raise NotImplementedError

    def retry_chunk(self, index: int) -> int:
        raise NotImplementedError

    def chunk_state(self, index: int) -> str:
        raise NotImplementedError

    def contiguous_done(self) -> int:
        raise NotImplementedError

    def join(self) -> JoinResult:
        raise NotImplementedError

    def progress(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError
