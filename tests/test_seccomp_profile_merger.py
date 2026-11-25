import pytest

import seccomp_profile_merger as spm


def merger(**kwargs):
    return spm.SeccompProfileMerger(**kwargs)


def allow(syscall, args=()):
    return spm.Rule(syscall, "allow", args)


def test_merge_sorts_rules_and_aligns_origins():
    base = spm.Layer("base", "deny", (allow("write"), allow("open")))
    work = spm.Layer("workload", "deny", (allow("read"),))
    merged = merger().merge([base, work])
    assert [rule.syscall for rule in merged.rules] == ["open", "read", "write"]
    assert merged.origins == ("base", "workload", "base")
    assert merged.default_action == "deny"
    assert merged.conflicts == ()


def test_default_action_takes_strictest():
    a = spm.Layer("a", "allow", (allow("read"),))
    b = spm.Layer("b", "errno", (allow("write"),))
    assert merger().merge([a, b]).default_action == "errno"
    c = spm.Layer("c", "deny", (allow("execve"),))
    assert merger().merge([a, c]).default_action == "deny"
    d = spm.Layer("d", "allow", (allow("open"),))
    assert merger().merge([a, d]).default_action == "allow"


def test_deny_wins_over_allow_and_reports_conflict():
    base = spm.Layer("base", "allow", (spm.Rule("ptrace", "deny"),))
    exception = spm.Layer("exception", "allow", (allow("ptrace"),))
    merged = merger().merge([base, exception])
    rules = [rule for rule in merged.rules if rule.syscall == "ptrace"]
    assert len(rules) == 1
    assert rules[0].action == "deny"
    assert merged.origins[0] == "base"
    assert len(merged.conflicts) == 1
    conflict = merged.conflicts[0]
    assert conflict["syscall"] == "ptrace"
    assert conflict["layers"] == ["base", "exception"]
    assert conflict["reason"] == "deny_over_allow"


def test_allow_constraints_merge_without_conflict():
    base = spm.Layer("base", "deny", (allow("open", ((0, "eq", 5),)),))
    work = spm.Layer("workload", "deny", (allow("open", ((0, "eq", 5),)),))
    merged = merger().merge([base, work])
    assert len(merged.rules) == 1
    assert merged.rules[0].args == ((0, "eq", 5),)
    assert merged.conflicts == ()


def test_incompatible_args_drop_rule():
    base = spm.Layer("base", "deny", (allow("open", ((0, "eq", 5),)),))
    work = spm.Layer("workload", "deny", (allow("open", ((0, "eq", 7),)),))
    merged = merger().merge([base, work])
    assert [rule.syscall for rule in merged.rules] == []
    assert len(merged.conflicts) == 1
    conflict = merged.conflicts[0]
    assert conflict["reason"] == "args_incompatible"
    assert conflict["layers"] == ["base", "workload"]
    view = merger().explain(merged, "open")
    assert view["action"] == "deny"
    assert view["decided_by"] == "default"
    assert view["conflict"] is True


def test_intra_layer_duplicate_deduped():
    solo = spm.Layer("solo", "deny", (allow("dup"), allow("dup"),))
    merged = merger().merge([solo])
    assert len(merged.rules) == 1
    assert merged.conflicts == ()


def test_intra_layer_conflicting_args_reported():
    solo = spm.Layer("solo", "deny", (
        allow("open", ((0, "eq", 5),)),
        allow("open", ((0, "eq", 7),)),
    ))
    merged = merger().merge([solo])
    assert merged.rules == ()
    assert len(merged.conflicts) == 1
    assert merged.conflicts[0]["layers"] == ["solo", "solo"]


def test_same_content_same_generation():
    m = merger()
    base = spm.Layer("base", "deny", (allow("write"), allow("open")))
    base_reordered = spm.Layer("base", "deny", (allow("open"), allow("write")))
    work = spm.Layer("workload", "allow", (allow("read"),))
    first = m.merge([base, work])
    second = m.merge([base_reordered, work])
    assert first.generation == second.generation
    assert m.snapshot(first) == m.snapshot(second)
    assert m.stats()["merges"] == 2


def test_changed_content_advances_generation():
    m = merger()
    first = m.merge([spm.Layer("a", "deny", (allow("open"),))])
    second = m.merge([spm.Layer("a", "deny", (allow("read"),))])
    assert second.generation > first.generation
    assert m.activate(first) == first.generation
    assert m.activate(second) == second.generation
    with pytest.raises(spm.SeccompMergeError) as exc:
        m.activate(first)
    assert exc.value.code == "SECCOMP_STALE_GENERATION"


def test_explain_names_deciding_layer():
    base = spm.Layer("base", "deny", (allow("open"),))
    work = spm.Layer("workload", "deny", (allow("read"),))
    merged = merger().merge([base, work])
    assert merger().explain(merged, "read") == {
        "syscall": "read", "action": "allow",
        "decided_by": "workload", "conflict": False,
    }
    assert merger().explain(merged, "execve") == {
        "syscall": "execve", "action": "deny",
        "decided_by": "default", "conflict": False,
    }


def test_diff_lists_added_removed_changed():
    m = merger()
    old = m.merge([spm.Layer("base", "allow", (allow("open", ((0, "eq", 5),)),))])
    new = m.merge([spm.Layer("work", "errno", (spm.Rule("open", "deny"), allow("read")))])
    result = m.diff(old, new)
    assert result == {"added": ["read"], "removed": [], "changed": ["open"]}


def test_validate_rejects_bad_layers():
    m = merger()
    with pytest.raises(spm.SeccompMergeError) as exc:
        m.validate(spm.Layer("x", "allow", (spm.Rule("read", "permit"),)))
    assert exc.value.code == "SECCOMP_ACTION_UNKNOWN"
    for bad in ("Read", ""):
        with pytest.raises(spm.SeccompMergeError) as exc2:
            m.validate(spm.Layer("x", "allow", (spm.Rule(bad, "allow"),)))
        assert exc2.value.code == "SECCOMP_SYSCALL_INVALID"
    with pytest.raises(spm.SeccompMergeError) as exc3:
        m.validate(spm.Layer("", "allow", (allow("read"),)))
    assert exc3.value.code == "SECCOMP_LAYER_NAME_INVALID"


def test_limits_layers_rules_args():
    with pytest.raises(spm.SeccompMergeError) as exc:
        merger(max_layers=2).merge([
            spm.Layer("a", "deny", (allow("read"),)),
            spm.Layer("b", "deny", (allow("write"),)),
            spm.Layer("c", "deny", (allow("open"),)),
        ])
    assert exc.value.code == "SECCOMP_LIMIT_EXCEEDED"
    with pytest.raises(spm.SeccompMergeError) as exc2:
        merger(max_rules=2).merge([
            spm.Layer("a", "deny", (allow("read"), allow("write"), allow("open"))),
        ])
    assert exc2.value.code == "SECCOMP_LIMIT_EXCEEDED"
    with pytest.raises(spm.SeccompMergeError) as exc3:
        merger(max_args=1).merge([
            spm.Layer("a", "deny", (allow("open", ((0, "eq", 5), (1, "eq", 6))),)),
        ])
    assert exc3.value.code == "SECCOMP_LIMIT_EXCEEDED"


def test_snapshot_detached():
    m = merger()
    merged = m.merge([spm.Layer("base", "deny", (allow("open"), allow("write")))])
    snap = m.snapshot(merged)
    assert set(snap) == {"default_action", "rules", "origins", "conflicts", "generation"}
    generation = merged.generation
    snap["rules"].clear()
    snap["generation"] = 999
    assert len(merged.rules) == 2
    assert merged.generation == generation


def test_activate_replaces_whole_policy():
    m = merger()
    first = m.merge([spm.Layer("a", "allow", (allow("open"),))])
    second = m.merge([spm.Layer("b", "errno", (allow("read"),))])
    assert m.current() is None
    assert m.activate(first) == first.generation
    assert m.current().rules == first.rules
    m.activate(second)
    assert [rule.syscall for rule in m.current().rules] == ["read"]
    assert m.current().default_action == "errno"
