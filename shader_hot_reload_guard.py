"""Shader hot reload guard: include tracking, rebuild staging and frame-boundary commit."""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ShaderReloadError(ValueError):
    """Refused reload operation; ``code`` carries a SHADER_* identifier."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProgramInfo:
    program_id: str
    path: str
    generation: int
    state: str
    includes: tuple[str, ...]


@dataclass(frozen=True)
class RebuildOutcome:
    program_id: str
    ok: bool
    generation: int
    error: str | None


class ShaderHotReloadGuard:
    """Track shader sources with includes and swap rebuilt programs at frame boundaries."""

    def __init__(self, compile_fn, *, max_rebuilds_per_frame: int = 2,
                 max_dependencies: int = 64) -> None:
        self.compile_fn = compile_fn
        self.max_rebuilds_per_frame = max_rebuilds_per_frame
        self.max_dependencies = max_dependencies
        self._lock = threading.RLock()

    def watch(self, program_id: str, path: str,
              include_edges: dict | None = None) -> ProgramInfo:
        """Register a program with its source path and include graph."""
        raise NotImplementedError

    def touch_file(self, path: str) -> int:
        """Mark every program depending on the file dirty; return the count."""
        raise NotImplementedError

    def dependencies(self, program_id: str) -> tuple[str, ...]:
        """Return the sorted include closure of one program."""
        raise NotImplementedError

    def rebuild(self, program_id: str) -> RebuildOutcome:
        """Compile a dirty program and stage the result for the next frame."""
        raise NotImplementedError

    def commit_at_frame_boundary(self) -> int:
        """Switch staged programs at the frame boundary; return how many switched."""
        raise NotImplementedError

    def rollback(self, program_id: str) -> int:
        """Return one program to its previous live generation."""
        raise NotImplementedError

    def generation(self, program_id: str) -> int:
        """Return the live generation number of one program."""
        raise NotImplementedError

    def program_state(self, program_id: str) -> str:
        """Return ready, dirty, staged or failed for one program."""
        raise NotImplementedError

    def pending(self) -> tuple[str, ...]:
        """Return the programs staged and waiting for the frame boundary."""
        raise NotImplementedError

    def errors(self) -> dict:
        """Return the last compile error per failed program."""
        raise NotImplementedError

    def programs(self) -> tuple[str, ...]:
        """Return watched program ids in ascending order."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Capture programs, include graphs, generations and counters."""
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        """Replace the live state with a snapshot."""
        raise NotImplementedError

    def stats(self) -> dict:
        """Return reload guard counters."""
        raise NotImplementedError
