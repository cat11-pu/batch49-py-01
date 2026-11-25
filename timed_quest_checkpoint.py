"""Limited-time quest chain: per-stage deadlines, pause freezing, checkpoint rewind."""

from __future__ import annotations

import threading


class QuestTimerError(ValueError):
    """Raised when a quest timer operation is refused."""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TimedQuestCheckpoint:
    """Track a multi-stage timed quest chain with pause and checkpoint rewind."""

    def __init__(self, stages, *, chain_id: str = "chain") -> None:
        self.stages = [(str(stage_id), int(duration_ms)) for stage_id, duration_ms in stages]
        self.chain_id = chain_id
        self._lock = threading.RLock()

    def enter_stage(self, stage_id: str) -> dict:
        raise NotImplementedError

    def finish_chain(self) -> dict:
        raise NotImplementedError

    def pause(self, now_ms: int) -> dict:
        raise NotImplementedError

    def resume(self, now_ms: int) -> dict:
        raise NotImplementedError

    def tick(self, now_ms: int) -> dict:
        raise NotImplementedError

    def remaining_ms(self) -> int:
        raise NotImplementedError

    def paused_total_ms(self) -> int:
        raise NotImplementedError

    def checkpoint(self) -> dict:
        raise NotImplementedError

    def fail_and_rewind(self, reason: str = "manual") -> dict:
        raise NotImplementedError

    def state(self) -> dict:
        raise NotImplementedError

    def grant_temp_item(self, item_id: str, qty: int = 1) -> None:
        raise NotImplementedError

    def grant_reward(self, item_id: str, qty: int = 1) -> None:
        raise NotImplementedError

    def inventory(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError

    def restore(self, state: dict) -> None:
        raise NotImplementedError
