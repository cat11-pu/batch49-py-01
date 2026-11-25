"""seccomp 分层 profile 合并：优先级裁决、参数求交、冲突报告与代次激活。"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class SeccompMergeError(ValueError):
    """分层策略合并被拒绝时抛出。"""

    def __init__(self, code: str, detail: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Rule:
    """一条 syscall 规则，args 为 (参数序号, 比较符, 值) 三元组元组。"""

    syscall: str
    action: str
    args: tuple = ()


@dataclass(frozen=True)
class Layer:
    """一个策略层。"""

    name: str
    default_action: str
    rules: tuple = ()


@dataclass(frozen=True)
class MergedProfile:
    """合并后的生效策略。"""

    default_action: str
    rules: tuple
    origins: tuple
    conflicts: tuple
    generation: int


class SeccompProfileMerger:
    """把基础层、工作负载层与例外层合并成一份生效策略。"""

    def __init__(self, *, max_layers: int = 8, max_rules: int = 256,
                 max_args: int = 6) -> None:
        self.max_layers = max_layers
        self.max_rules = max_rules
        self.max_args = max_args
        self._lock = threading.RLock()

    def merge(self, layers):
        raise NotImplementedError

    def explain(self, merged, syscall):
        raise NotImplementedError

    def diff(self, old, new):
        raise NotImplementedError

    def snapshot(self, merged):
        raise NotImplementedError

    def validate(self, layer):
        raise NotImplementedError

    def current(self):
        raise NotImplementedError

    def activate(self, merged):
        raise NotImplementedError

    def conflicts(self, merged):
        raise NotImplementedError

    def resolve_args(self, left, right):
        raise NotImplementedError

    def action_rank(self, action):
        raise NotImplementedError

    def stats(self):
        raise NotImplementedError
