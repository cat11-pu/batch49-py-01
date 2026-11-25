import copy
import hashlib

import pytest

import syscall_profile_selector as sp


def make_file(tmp_path, name, data=b"#!/bin/false\npayload\n"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def digest_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inode_of(path):
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}"


def selector(**kwargs):
    return sp.SyscallProfileSelector(**kwargs)


def test_register_returns_generation_and_lists_profile():
    table = selector()
    assert table.generation() == 0
    assert table.register("web", path="/usr/bin/nginx", policy={"allow": ["read"]}) == 1
    assert table.register("db", path="/usr/bin/postgres", policy={"allow": ["read"]}) == 2
    assert table.generation() == 2
    assert table.profiles() == ["db", "web"]
    assert table.stats()["registered"] == 2


def test_duplicate_profile_name_rules():
    table = selector()
    first = table.register("web", path="/a", policy={"allow": ["read"]})
    assert table.register("web", path="/a", policy={"allow": ["read"]}) == first
    assert table.generation() == first
    with pytest.raises(sp.ProfileSelectError) as exc:
        table.register("web", path="/b", policy={"allow": ["write"]})
    assert exc.value.code == "PROFILE_EXISTS"
    assert table.generation() == first
    assert table.stats()["rejected"] == 1


def test_digest_rule_outranks_inode_and_path(tmp_path):
    exe = make_file(tmp_path, "prog")
    table = selector()
    table.register("path-rule", path=str(exe), policy={"p": "path"})
    table.register("inode-rule", inode=inode_of(exe), policy={"p": "inode"})
    table.register("digest-rule", digest=digest_of(exe), policy={"p": "digest"})
    binding = table.select(str(exe))
    assert binding["profile"] == "digest-rule"
    assert binding["policy"] == {"p": "digest"}
    table.unregister("digest-rule")
    assert table.select(str(exe))["profile"] == "inode-rule"
    table.unregister("inode-rule")
    assert table.select(str(exe))["profile"] == "path-rule"


def test_unmatched_executable_gets_default_deny():
    table = selector(default_profile="deny-all")
    binding = table.select("/usr/bin/definitely-absent")
    assert binding["profile"] == "deny-all"
    assert binding["denied"] is True
    assert table.stats()["denied"] == 1


def test_unreadable_executable_denied_without_raise(tmp_path):
    table = selector()
    binding = table.select(str(tmp_path / "missing"))
    assert binding["profile"] == "deny-all"
    assert binding["denied"] is True
    assert binding["reason"] == "unreadable"
    assert table.stats()["denied"] == 1


def test_different_argv_are_separate_cache_entries(tmp_path):
    exe = make_file(tmp_path, "prog")
    table = selector()
    table.register("web", path=str(exe), policy={"p": "web"})
    first = table.select(str(exe), argv=("--serve",))
    second = table.select(str(exe), argv=("--serve",))
    third = table.select(str(exe), argv=("--probe",))
    assert first["profile"] == second["profile"] == third["profile"] == "web"
    cache = table.cache_stats()
    assert cache["size"] == 2
    assert cache["hits"] == 1
    assert cache["misses"] == 2
    assert cache["evictions"] == 0
    assert table.stats()["selects"] == 3


def test_content_change_rematches_and_misses_cache(tmp_path):
    exe = make_file(tmp_path, "prog", data=b"old-binary")
    table = selector()
    table.register("by-digest", digest=digest_of(exe), policy={"p": "old"})
    table.register("by-path", path=str(exe), policy={"p": "fallback"})
    assert table.select(str(exe))["profile"] == "by-digest"
    exe.write_bytes(b"new-binary")
    binding = table.select(str(exe))
    assert binding["profile"] == "by-path"
    assert binding["policy"] == {"p": "fallback"}
    assert table.cache_stats()["misses"] == 2


def test_invalidate_path_evicts_matching_entries(tmp_path):
    first = make_file(tmp_path, "first")
    second = make_file(tmp_path, "second")
    table = selector()
    table.register("a", path=str(first), policy={"p": "a"})
    table.register("b", path=str(second), policy={"p": "b"})
    table.select(str(first))
    table.select(str(second))
    table.select(str(first))
    generation = table.generation()
    assert table.invalidate(path=str(first)) == 1
    assert table.cache_stats()["size"] == 1
    assert table.generation() == generation
    before = table.cache_stats()["misses"]
    table.select(str(first))
    assert table.cache_stats()["misses"] == before + 1


def test_binding_not_current_after_rule_update(tmp_path):
    exe = make_file(tmp_path, "prog")
    table = selector()
    table.register("web", path=str(exe), policy={"p": "web"})
    binding = table.select(str(exe))
    assert table.binding_current(binding) is True
    table.register("other", path="/usr/bin/other", policy={"p": "other"})
    assert table.binding_current(binding) is False


def test_interpreter_script_selected_by_interpreter_and_records_script(tmp_path):
    interpreter = make_file(tmp_path, "fake-interp", data=b"interpreter")
    script = make_file(tmp_path, "job.sh",
                       data=f"#!{interpreter}\necho hello\n".encode())
    table = selector()
    table.register("interp-profile", path=str(interpreter), policy={"p": "interp"})
    binding = table.select(str(script))
    assert binding["profile"] == "interp-profile"
    assert binding["interpreter"] == str(interpreter)
    assert binding["script"] == str(script)


def test_conflicting_rules_arbitrated_and_reported(tmp_path):
    exe = make_file(tmp_path, "prog")
    table = selector()
    table.register("beta", path=str(exe), policy={"p": "beta"})
    table.register("alpha", path=str(exe), policy={"p": "alpha"})
    binding = table.select(str(exe))
    assert binding["profile"] == "alpha"
    assert binding["conflicts"] == ("beta",)
    assert table.stats()["conflicts"] == 1


def test_cache_lru_evicts_oldest_at_capacity(tmp_path):
    table = selector(cache_size=2)
    for name in ("a", "b", "c"):
        path = make_file(tmp_path, name)
        table.register(name, path=str(path), policy={"p": name})
    for name in ("a", "b", "c"):
        table.select(str(tmp_path / name))
    cache = table.cache_stats()
    assert cache["size"] == 2
    assert cache["evictions"] >= 1


def test_rule_table_cap_rejects_registration():
    table = selector(max_rules=2)
    table.register("a", path="/a", policy={})
    table.register("b", path="/b", policy={})
    generation = table.generation()
    with pytest.raises(sp.ProfileSelectError) as exc:
        table.register("c", path="/c", policy={})
    assert exc.value.code == "RULE_TABLE_FULL"
    assert table.generation() == generation
    assert table.profiles() == ["a", "b"]


def test_snapshot_restore_roundtrip_and_invalid_state(tmp_path):
    exe = make_file(tmp_path, "prog")
    table = selector()
    table.register("web", path=str(exe), policy={"p": "web"})
    table.select(str(exe))
    state = table.snapshot()
    assert {"generation", "rules", "stats"} <= set(state)
    pristine = copy.deepcopy(state)
    state["rules"].clear()
    state["stats"]["selects"] = 999
    assert table.select(str(exe))["profile"] == "web"
    assert table.stats()["selects"] == 2

    clone = selector()
    clone.restore(pristine)
    assert clone.generation() == table.generation()
    assert clone.profiles() == table.profiles()
    assert clone.stats() == table.stats()
    assert clone.select(str(exe))["profile"] == "web"

    bad_states = [None, {}, {"generation": "1", "rules": [], "stats": {}},
                  {"generation": 1, "rules": {}, "stats": {}},
                  {"generation": 1, "rules": [], "stats": {"selects": "x"}}]
    for bad in bad_states:
        with pytest.raises(sp.ProfileSelectError) as exc:
            clone.restore(bad)
        assert exc.value.code == "STATE_INVALID"
    assert clone.profiles() == ["web"]


def test_stats_and_invalid_rule():
    exe = None
    table = selector()
    path = "/usr/bin/anything"
    table.register("web", path=path, policy={})
    table.select(path)
    table.select(path)
    table.select("/usr/bin/missing")
    with pytest.raises(sp.ProfileSelectError) as exc:
        table.register("bad", policy={})
    assert exc.value.code == "INVALID_RULE"
    assert table.stats() == {"selects": 3, "conflicts": 0, "denied": 1,
                             "registered": 1, "rejected": 1}
