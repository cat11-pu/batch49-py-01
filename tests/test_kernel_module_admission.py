import copy
import hashlib
import threading

import pytest

import kernel_module_admission as km

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def gate(**kwargs):
    return km.KernelModuleAdmission(**kwargs)


def add_rule(g, name="zfs", low="1.0.0", high="2.0.0",
             keys=("k-alpha", "k-old")):
    return g.add_rule(name, min_version=low, max_version=high,
                      allowed_keys=keys)


def register(g, key_id="k-alpha", material="pub-alpha", not_before=0,
             not_after=10000):
    return g.register_key(key_id, material, not_before=not_before,
                          not_after=not_after)


def admit(g, name="zfs", version="1.4.0", digest=DIGEST_A,
          key_id="k-alpha", now=100, allow_rollback=False):
    signature = g.expected_signature(key_id, name, version, digest)
    return g.admit(name, version, digest, signature, key_id=key_id, now=now,
                   allow_rollback=allow_rollback)


def test_add_rule_version_range_and_unknown_module():
    g = gate()
    rule = add_rule(g)
    assert rule == km.VersionRule(name="zfs", min_version="1.0.0",
                                  max_version="2.0.0",
                                  allowed_keys=("k-alpha", "k-old"))
    register(g)

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, version="0.9.9")
    assert exc.value.code == "MODULE_VERSION_DENIED"

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, version="1.4")
    assert exc.value.code == "MODULE_VERSION_DENIED"

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, name="overlay")
    assert exc.value.code == "MODULE_NOT_ALLOWED"

    bad_rules = [
        ("", "1.0.0", "2.0.0", ("k-alpha",)),
        ("zfs", "2.0.0", "1.0.0", ("k-alpha",)),
        ("zfs", "1.0.0", "2.0.0", ()),
        ("zfs", "1.x.0", "2.0.0", ("k-alpha",)),
    ]
    for name, low, high, keys in bad_rules:
        with pytest.raises(km.KernelModuleError) as exc:
            g.add_rule(name, min_version=low, max_version=high,
                       allowed_keys=keys)
        assert exc.value.code == "MODULE_RULE_INVALID"

    assert g.remove_rule("zfs") is True
    assert g.remove_rule("zfs") is False


def test_register_key_window_conflict_and_revocation():
    g = gate()
    add_rule(g)
    assert register(g) is True
    assert register(g) is False

    with pytest.raises(km.KernelModuleError) as exc:
        register(g, material="pub-other")
    assert exc.value.code == "MODULE_KEY_CONFLICT"

    with pytest.raises(km.KernelModuleError) as exc:
        register(g, key_id="k-bad", not_before=500, not_after=500)
    assert exc.value.code == "MODULE_KEY_INVALID"

    register(g, key_id="k-old", material="pub-old", not_before=0,
             not_after=50)
    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, key_id="k-old", now=51)
    assert exc.value.code == "MODULE_KEY_EXPIRED"

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, key_id="k-ghost")
    assert exc.value.code == "MODULE_KEY_UNKNOWN"

    assert g.revoke_key("k-alpha", now=60) is True
    assert g.revoke_key("k-alpha", now=61) is False
    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, now=70)
    assert exc.value.code == "MODULE_KEY_REVOKED"
    assert g.stats()["key_revocations"] == 1


def test_expected_signature_formula_and_rejections():
    g = gate()
    add_rule(g)
    register(g)

    payload = "k-alpha|pub-alpha|zfs|1.4.0|" + DIGEST_A
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert g.expected_signature("k-alpha", "zfs", "1.4.0", DIGEST_A) == expected

    with pytest.raises(km.KernelModuleError) as exc:
        g.admit("zfs", "1.4.0", DIGEST_A, "f" * 64, key_id="k-alpha", now=100)
    assert exc.value.code == "MODULE_SIGNATURE_INVALID"

    with pytest.raises(km.KernelModuleError) as exc:
        g.admit("zfs", "1.4.0", "sha256:abc", expected, key_id="k-alpha",
                now=100)
    assert exc.value.code == "MODULE_DIGEST_INVALID"

    with pytest.raises(km.KernelModuleError) as exc:
        g.admit("zfs", "1.4.0", DIGEST_A.upper(), expected, key_id="k-alpha",
                now=100)
    assert exc.value.code == "MODULE_DIGEST_INVALID"

    with pytest.raises(km.KernelModuleError) as exc:
        g.expected_signature("k-ghost", "zfs", "1.4.0", DIGEST_A)
    assert exc.value.code == "MODULE_KEY_UNKNOWN"


def test_admit_is_idempotent_and_digest_conflict_is_refused():
    g = gate()
    add_rule(g)
    register(g)

    first = admit(g, now=100)
    assert isinstance(first, km.Admission)
    assert first.accepted is True
    record = first.record
    assert isinstance(record, km.ModuleRecord)
    assert (record.name, record.version, record.digest, record.key_id) == (
        "zfs", "1.4.0", DIGEST_A, "k-alpha")
    assert record.state == "admitted"
    assert record.admitted_at == 100
    assert record.measured_at is None
    assert record.measures == 0

    second = admit(g, now=140)
    assert second.accepted is False
    assert second.record == record
    assert g.stats()["admitted"] == 1

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, digest=DIGEST_B, now=150)
    assert exc.value.code == "MODULE_DIGEST_CONFLICT"


def test_load_requires_admission_and_is_idempotent():
    g = gate()
    add_rule(g)
    register(g)

    with pytest.raises(km.KernelModuleError) as exc:
        g.load("zfs", now=100)
    assert exc.value.code == "MODULE_UNKNOWN"

    admit(g, now=110)
    assert g.load("zfs", now=115) is True
    assert g.loaded_modules() == ("zfs",)
    entry = g.effective_manifest()[0]
    assert (entry.name, entry.version, entry.state) == ("zfs", "1.4.0",
                                                        "loaded")
    assert g.load("zfs", now=120) is False
    assert g.stats()["loads"] == 1

    assert g.unload("zfs", now=130) is True
    assert g.unload("zfs", now=131) is False
    assert g.loaded_modules() == ()
    assert g.stats()["unloads"] == 1
    assert g.module_of("zfs").state == "admitted"


def test_measure_matching_digest_requires_loaded_state():
    g = gate()
    add_rule(g)
    register(g)
    admit(g)

    with pytest.raises(km.KernelModuleError) as exc:
        g.measure("zfs", DIGEST_A, now=110)
    assert exc.value.code == "MODULE_NOT_LOADED"

    g.load("zfs", now=115)
    assert g.measure("zfs", DIGEST_A, now=120) is True
    record = g.module_of("zfs")
    assert (record.state, record.measures, record.measured_at) == (
        "loaded", 1, 120)
    assert g.stats()["measures"] == 1

    with pytest.raises(km.KernelModuleError) as exc:
        g.measure("overlay", DIGEST_A, now=121)
    assert exc.value.code == "MODULE_UNKNOWN"


def test_measure_mismatch_quarantines_and_blocks_loading():
    g = gate()
    add_rule(g)
    register(g)
    admit(g)
    g.load("zfs", now=115)

    assert g.measure("zfs", DIGEST_B, now=120) is False
    record = g.module_of("zfs")
    assert (record.state, record.measures, record.measured_at) == (
        "quarantined", 0, None)
    assert g.stats()["mismatches"] == 1
    assert [entry.state for entry in g.effective_manifest()] == ["quarantined"]

    with pytest.raises(km.KernelModuleError) as exc:
        g.load("zfs", now=130)
    assert exc.value.code == "MODULE_BLOCKED"

    assert g.unblock("zfs") is True
    assert g.module_of("zfs").state == "admitted"
    assert g.unblock("zfs") is False


def test_block_unloads_module_and_is_idempotent():
    g = gate()
    add_rule(g)
    register(g)
    admit(g)
    g.load("zfs", now=115)

    assert g.block("zfs", "tainted_page", now=120) is True
    assert g.module_of("zfs").state == "blocked"
    assert g.loaded_modules() == ()
    assert g.stats()["unloads"] == 1
    assert g.stats()["blocks"] == 1

    assert g.block("zfs", "again", now=121) is False
    assert g.stats()["blocks"] == 1

    with pytest.raises(km.KernelModuleError) as exc:
        g.load("zfs", now=130)
    assert exc.value.code == "MODULE_BLOCKED"

    with pytest.raises(km.KernelModuleError) as exc:
        g.block("overlay", "missing", now=131)
    assert exc.value.code == "MODULE_UNKNOWN"


def test_version_rollback_is_denied_after_higher_version_was_loaded():
    g = gate()
    add_rule(g)
    register(g)
    admit(g, version="1.4.0")
    g.load("zfs", now=115)
    g.unload("zfs", now=120)
    assert g.retire("zfs") is True

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, version="1.2.0", now=130)
    assert exc.value.code == "MODULE_ROLLBACK_DENIED"
    assert g.stats()["rollbacks"] == 1

    out = admit(g, version="1.2.0", now=140, allow_rollback=True)
    assert out.accepted is True
    assert g.stats()["rollbacks"] == 1
    assert g.stats()["admitted"] == 2


def test_capacity_counts_records_and_retire_frees_one_slot():
    g = gate(max_modules=1)
    add_rule(g, name="zfs")
    add_rule(g, name="ext4")
    register(g)
    admit(g, name="zfs", now=100)

    with pytest.raises(km.KernelModuleError) as exc:
        admit(g, name="ext4", now=110)
    assert exc.value.code == "MODULE_CAPACITY_EXCEEDED"
    assert g.stats()["rejected"] == 1

    g.load("zfs", now=115)
    with pytest.raises(km.KernelModuleError) as exc:
        g.retire("zfs")
    assert exc.value.code == "MODULE_BLOCKED"

    g.unload("zfs", now=120)
    assert g.retire("zfs") is True
    assert g.retire("zfs") is False
    assert g.stats()["retired"] == 1
    assert g.effective_manifest() == ()

    assert admit(g, name="ext4", now=130).accepted is True
    assert g.stats()["admitted"] == 2


def test_effective_manifest_is_sorted_and_reports_state():
    g = gate()
    add_rule(g, name="zfs")
    add_rule(g, name="ext4")
    register(g)
    admit(g, name="zfs", now=100)
    admit(g, name="ext4", now=105)
    g.load("ext4", now=110)

    manifest = g.effective_manifest()
    assert [entry.name for entry in manifest] == ["ext4", "zfs"]
    assert [(entry.version, entry.state, entry.stale) for entry in manifest] == [
        ("1.4.0", "loaded", False),
        ("1.4.0", "admitted", False),
    ]

    with pytest.raises(km.KernelModuleError) as exc:
        g.module_of("overlay")
    assert exc.value.code == "MODULE_UNKNOWN"


def test_stats_exposes_fixed_counters():
    g = gate()
    add_rule(g)
    register(g)
    assert g.stats() == {
        "admitted": 0,
        "rejected": 0,
        "loads": 0,
        "unloads": 0,
        "blocks": 0,
        "measures": 0,
        "mismatches": 0,
        "rollbacks": 0,
        "key_revocations": 0,
        "retired": 0,
    }

    admit(g, now=100)
    g.load("zfs", now=110)
    g.measure("zfs", DIGEST_A, now=120)
    with pytest.raises(km.KernelModuleError):
        admit(g, name="overlay", now=125)

    assert g.stats() == {
        "admitted": 1,
        "rejected": 1,
        "loads": 1,
        "unloads": 0,
        "blocks": 0,
        "measures": 1,
        "mismatches": 0,
        "rollbacks": 0,
        "key_revocations": 0,
        "retired": 0,
    }


def test_snapshot_restore_roundtrip_and_deep_copy():
    g = gate()
    add_rule(g)
    register(g)
    admit(g, now=100)
    g.load("zfs", now=110)
    g.measure("zfs", DIGEST_A, now=120)

    state = g.snapshot()
    assert set(state) == {"version", "rules", "keys", "modules", "highest",
                          "counters"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["modules"].clear()
    state["counters"]["admitted"] = 99
    assert g.module_of("zfs").state == "loaded"
    assert g.stats()["admitted"] == 1

    clone = gate()
    clone.restore(pristine)
    assert clone.stats() == g.stats()
    assert clone.module_of("zfs") == g.module_of("zfs")
    assert clone.effective_manifest() == g.effective_manifest()
    assert clone.loaded_modules() == ("zfs",)


def test_restore_rejects_malformed_state_without_mutation():
    g = gate()
    add_rule(g)
    register(g)
    admit(g, now=100)

    good = g.snapshot()
    missing_key = {k: v for k, v in good.items() if k != "highest"}
    bad_states = [
        None,
        {},
        dict(good, version=2),
        missing_key,
        dict(good, rules=[]),
        dict(good, counters={}),
    ]
    for bad in bad_states:
        with pytest.raises(km.KernelModuleError) as exc:
            g.restore(bad)
        assert exc.value.code == "MODULE_SNAPSHOT_INVALID"

    assert g.module_of("zfs").state == "admitted"
    assert g.stats()["admitted"] == 1


def test_concurrent_admission_accepts_exactly_once():
    g = gate()
    add_rule(g)
    register(g)

    barrier = threading.Barrier(8)
    outcomes = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            outcomes.append(admit(g, now=100).accepted)
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert g.stats()["admitted"] == 1
    assert len(g.effective_manifest()) == 1
