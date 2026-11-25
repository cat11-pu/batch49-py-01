"""检测规则 DSL 的版本迁移组件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

VERSION_V2 = "v2"
VERSION_V3 = "v3"
VERSION_UNKNOWN = "unknown"

REJECT_RULE_TOO_LARGE = "rule_too_large"
REJECT_BATCH_TOO_LARGE = "batch_too_large"

SINGLE_BATCH_ID = "single"


@dataclass(frozen=True)
class MigrationConfig:
    keep_unknown_modifiers: bool = True
    max_rule_bytes: int = 65536
    max_rules_per_batch: int = 256


@dataclass(frozen=True)
class ParsedRule:
    rule_id: str
    start_line: int
    body_lines: tuple[str, ...]


@dataclass(frozen=True)
class ChangeEntry:
    rule_id: str
    line: int
    old: str
    new: str


@dataclass(frozen=True)
class MacroExpansion:
    macro: str
    params: tuple[str, ...]
    renamed: dict[str, str] = field(default_factory=dict)


@dataclass
class RuleMigration:
    rule_id: str
    original_text: str
    migrated_text: str
    changes: tuple[ChangeEntry, ...] = ()
    rolled_back: bool = False
    pending_review: bool = False
    reject_code: str | None = None


@dataclass
class BatchReport:
    batch_id: str
    items: dict[str, RuleMigration]
    order: tuple[str, ...] = ()
    committed: tuple[str, ...] = ()
    rolled_back: tuple[str, ...] = ()
    pending_review: tuple[str, ...] = ()
    rejected: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CommitOutcome:
    committed: tuple[str, ...]
    skipped: tuple[str, ...]


EquivalenceChecker = Callable[[str, str, str], bool]


class RuleDslMigrator:
    """把 v2 规则源文本迁移成 v3 语法，并对迁移结果做等价抽检。"""

    def __init__(
        self,
        config: MigrationConfig | None = None,
        equivalence_checker: EquivalenceChecker | None = None,
    ) -> None:
        self.config = config or MigrationConfig()
        self.equivalence_checker = equivalence_checker
        self._feature_table: dict[str, str] = {}
        self._committed: dict[str, str] = {}
        self._pending: list[str] = []

    def load_feature_table(self, table: Mapping[str, str]) -> None:
        raise NotImplementedError

    def feature_table(self) -> dict[str, str]:
        raise NotImplementedError

    def detect_version(self, source: str) -> str:
        raise NotImplementedError

    def parse(self, source: str) -> tuple[ParsedRule, ...]:
        raise NotImplementedError

    def expand_macros(self, rule: ParsedRule) -> tuple[MacroExpansion, ...]:
        raise NotImplementedError

    def rename_macro_params(self, expansion: MacroExpansion) -> MacroExpansion:
        raise NotImplementedError

    def semantic_fingerprint(self, text: str) -> str:
        raise NotImplementedError

    def migrate_rule(self, rule: ParsedRule) -> RuleMigration:
        raise NotImplementedError

    def migrate_text(self, source: str) -> BatchReport:
        raise NotImplementedError

    def migrate_batch(self, sources: Mapping[str, str]) -> BatchReport:
        raise NotImplementedError

    def commit_batch(self, report: BatchReport) -> CommitOutcome:
        raise NotImplementedError

    def committed_text(self, rule_id: str) -> str:
        raise NotImplementedError

    def pending_reviews(self) -> tuple[str, ...]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
