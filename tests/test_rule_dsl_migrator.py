import rule_dsl_migrator as rdm

FEATURE_TABLE = {
    "when": "match",
    "~": "matches",
    "emit": "alert",
    "severity=": "level=",
}

SOURCE_V2 = """[rule r-alpha]
when request.path ~ "^/admin"
emit severity=high
"""

SOURCE_V3 = """[rule r-alpha]
match request.path matches "^/admin"
alert level=high
"""

TWO_RULES = """[rule r-alpha]
when a ~ "x"
[rule r-beta]
when b ~ "y"
"""


def make_migrator(**kwargs):
    migrator = rdm.RuleDslMigrator(**kwargs)
    migrator.load_feature_table(FEATURE_TABLE)
    return migrator


def test_detect_version():
    migrator = make_migrator()
    assert migrator.detect_version(SOURCE_V2) == rdm.VERSION_V2
    assert migrator.detect_version(SOURCE_V3) == rdm.VERSION_V3
    assert migrator.detect_version("[rule r-empty]\n") == rdm.VERSION_UNKNOWN


def test_parse_rule_ids_in_order():
    migrator = make_migrator()
    rules = migrator.parse(TWO_RULES)
    assert [rule.rule_id for rule in rules] == ["r-alpha", "r-beta"]


def test_parse_start_line_is_one_based():
    migrator = make_migrator()
    rules = migrator.parse(TWO_RULES)
    assert [rule.start_line for rule in rules] == [1, 3]


def test_migrate_rule_rewrites_keywords():
    migrator = make_migrator()
    rule = migrator.parse(SOURCE_V2)[0]
    migration = migrator.migrate_rule(rule)
    assert migration.migrated_text == SOURCE_V3
    assert migration.rolled_back is False
    assert migration.reject_code is None


def test_longest_match_wins():
    migrator = rdm.RuleDslMigrator()
    migrator.load_feature_table({"sev": "lvl", "severity=": "level="})
    rule = migrator.parse("[rule r-long]\nemit severity=high\n")[0]
    migrated = migrator.migrate_rule(rule).migrated_text
    assert migrated == "[rule r-long]\nemit level=high\n"


def test_string_literal_not_rewritten():
    migrator = make_migrator()
    rule = migrator.parse('[rule r-s]\nwhen body ~ "a ~ b severity=1"\n')[0]
    migrated = migrator.migrate_rule(rule).migrated_text
    assert migrated == '[rule r-s]\nmatch body matches "a ~ b severity=1"\n'


def test_change_entries_carry_line_numbers():
    migrator = make_migrator()
    rule = migrator.parse(SOURCE_V2)[0]
    changes = migrator.migrate_rule(rule).changes
    assert [(item.line, item.old, item.new) for item in changes] == [
        (2, "when", "match"),
        (2, "~", "matches"),
        (3, "emit", "alert"),
        (3, "severity=", "level="),
    ]
    assert {item.rule_id for item in changes} == {"r-alpha"}


def test_semantic_fingerprint_equal_across_versions():
    migrator = make_migrator()
    assert migrator.semantic_fingerprint(SOURCE_V2) == migrator.semantic_fingerprint(
        SOURCE_V3
    )


def test_semantic_fingerprint_detects_literal_change():
    migrator = make_migrator()
    other = SOURCE_V3.replace('"/admin"', '"/root"')
    assert migrator.semantic_fingerprint(SOURCE_V3) != migrator.semantic_fingerprint(
        other
    )


def test_migrate_text_is_idempotent():
    migrator = make_migrator()
    first = migrator.migrate_text(SOURCE_V2)
    assert first.committed == ()
    migrated_once = first.items["r-alpha"].migrated_text
    second = migrator.migrate_text(migrated_once)
    assert second.items["r-alpha"].changes == ()
    assert second.items["r-alpha"].migrated_text == migrated_once


def test_commit_batch_returns_source_order():
    migrator = make_migrator()
    batch = migrator.migrate_batch({"r-beta": "[rule r-beta]\nwhen b ~ \"y\"\n",
                                    "r-alpha": SOURCE_V2})
    assert batch.order == ("r-beta", "r-alpha")
    outcome = migrator.commit_batch(batch)
    assert outcome.committed == ("r-beta", "r-alpha")
    assert outcome.skipped == ()


def test_committed_text_after_commit():
    migrator = make_migrator()
    batch = migrator.migrate_text(SOURCE_V2)
    migrator.commit_batch(batch)
    assert migrator.committed_text("r-alpha") == SOURCE_V3


def test_oversized_rule_rejected_and_skipped():
    migrator = make_migrator(config=rdm.MigrationConfig(max_rule_bytes=40))
    batch = migrator.migrate_text(SOURCE_V2)
    assert batch.items["r-alpha"].reject_code == rdm.REJECT_RULE_TOO_LARGE
    outcome = migrator.commit_batch(batch)
    assert outcome.committed == ()
    assert outcome.skipped == ("r-alpha",)


def test_rollback_keeps_original_text():
    def checker(rule_id, original, migrated):
        return rule_id != "r-alpha"

    migrator = make_migrator(equivalence_checker=checker)
    batch = migrator.migrate_text(SOURCE_V2)
    item = batch.items["r-alpha"]
    assert item.rolled_back is True
    assert item.migrated_text == SOURCE_V2
    assert item.changes == ()


def test_feature_table_returns_copy():
    migrator = make_migrator()
    snapshot = migrator.feature_table()
    snapshot["when"] = "broken"
    assert migrator.feature_table()["when"] == "match"
    assert set(migrator.feature_table()) == set(FEATURE_TABLE)
