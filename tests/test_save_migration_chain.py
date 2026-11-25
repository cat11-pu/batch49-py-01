import threading

import pytest

import save_migration_chain as mc

P1 = {"gold": 10, "level": 3}
P1_CHECKSUM = "83d7c718f71511e6"
P1_SIZE = 21
MIGRATED = {"gold": 10, "level": 3, "step1": 1, "step2": 4, "step3": [3]}
MIGRATED_CHECKSUM = "a77c166b8de49958"


def build(**kwargs):
    params = {"current_version": 4, "slots_per_account": 3}
    params.update(kwargs)
    return mc.SaveMigrationChain(**params)


def register_all(chain, calls):
    def m12(payload, version):
        calls.append((1, 2))
        return {**payload, "step1": 1}

    def m23(payload, version):
        calls.append((2, 3))
        return {**payload, "step2": version * 2}

    def m34(payload, version):
        calls.append((3, 4))
        return {**payload, "step3": [version]}

    chain.register_migration(1, 2, m12)
    chain.register_migration(2, 3, m23)
    chain.register_migration(3, 4, m34)
    return calls


def test_register_migration_rejects_skipped_step():
    chain = build()
    with pytest.raises(mc.SaveError) as info:
        chain.register_migration(1, 3, lambda payload, version: payload)
    assert info.value.code == "MIGRATION_JUMP"
    with pytest.raises(mc.SaveError) as info:
        chain.register_migration(3, 2, lambda payload, version: payload)
    assert info.value.code == "MIGRATION_JUMP"


def test_register_migration_rejects_duplicate_from():
    chain = build()
    chain.register_migration(1, 2, lambda payload, version: payload)
    with pytest.raises(mc.SaveError) as info:
        chain.register_migration(1, 2, lambda payload, version: payload)
    assert info.value.code == "MIGRATION_DUPLICATE"


def test_create_slot_records_meta_and_checksum():
    chain = build()
    meta = chain.create_slot("acc", "s1", dict(P1))
    assert (meta.account, meta.slot_id, meta.version) == ("acc", "s1", 1)
    assert meta.checksum == P1_CHECKSUM
    assert meta.size == P1_SIZE
    assert meta.status == "ok"
    assert chain.slot_payload("acc", "s1") == P1
    assert mc.payload_checksum(dict(P1)) == P1_CHECKSUM


def test_slot_capacity_is_hard_limit():
    chain = build(slots_per_account=2)
    chain.create_slot("acc", "s1", {"a": 1})
    chain.create_slot("acc", "s2", {"a": 2})
    with pytest.raises(mc.SaveError) as info:
        chain.create_slot("acc", "s3", {"a": 3})
    assert info.value.code == "SLOT_CAPACITY"
    assert [m.slot_id for m in chain.list_slots("acc")] == ["s1", "s2"]


def test_plan_upgrade_walks_every_level():
    chain = build()
    register_all(chain, [])
    chain.create_slot("acc", "old", {"a": 1}, version=1)
    chain.create_slot("acc", "mid", {"a": 2}, version=3)
    chain.create_slot("acc", "new", {"a": 3}, version=4)
    assert chain.plan_upgrade("acc", "old") == [(1, 2), (2, 3), (3, 4)]
    assert chain.plan_upgrade("acc", "mid") == [(3, 4)]
    assert chain.plan_upgrade("acc", "new") == []


def test_migrate_runs_chain_and_records_summary():
    chain = build()
    calls = register_all(chain, [])
    chain.create_slot("acc", "s1", dict(P1))
    meta = chain.migrate_slot("acc", "s1")
    assert meta.version == 4
    assert meta.checksum == MIGRATED_CHECKSUM
    assert meta.status == "ok"
    assert chain.slot_payload("acc", "s1") == MIGRATED
    assert calls == [(1, 2), (2, 3), (3, 4)]
    summary = chain.last_migration("acc", "s1")
    assert (summary.from_version, summary.to_version, summary.steps) == (1, 4, 3)
    assert chain.stats()["steps"] == 3


def test_migrate_slot_is_idempotent():
    chain = build()
    calls = register_all(chain, [])
    chain.create_slot("acc", "s1", dict(P1))
    first = chain.migrate_slot("acc", "s1")
    second = chain.migrate_slot("acc", "s1")
    assert (second.version, second.checksum) == (first.version, first.checksum)
    assert calls == [(1, 2), (2, 3), (3, 4)]
    assert chain.stats()["migrations"] == 1
    assert chain.stats()["steps"] == 3


def test_migration_failure_keeps_slot_and_marks_repair():
    chain = build()

    def m12(payload, version):
        return {**payload, "step1": 1}

    def m23(payload, version):
        raise RuntimeError("boom")

    def m34(payload, version):
        return {**payload, "step3": True}

    chain.register_migration(1, 2, m12)
    chain.register_migration(2, 3, m23)
    chain.register_migration(3, 4, m34)
    chain.create_slot("acc", "s1", dict(P1))
    with pytest.raises(mc.SaveError) as info:
        chain.migrate_slot("acc", "s1")
    assert info.value.code == "MIGRATION_FAILED"
    meta = chain.list_slots("acc")[0]
    assert (meta.version, meta.checksum, meta.status) == (1, P1_CHECKSUM, "needs_repair")
    assert chain.slot_payload("acc", "s1") == P1
    assert chain.stats()["repairs"] == 1
    assert chain.stats()["migrations"] == 0


def test_rollback_restores_backup_once():
    chain = build()
    register_all(chain, [])
    chain.create_slot("acc", "s1", dict(P1))
    chain.migrate_slot("acc", "s1")
    meta = chain.rollback_slot("acc", "s1")
    assert (meta.version, meta.checksum) == (1, P1_CHECKSUM)
    assert chain.slot_payload("acc", "s1") == P1
    with pytest.raises(mc.SaveError) as info:
        chain.rollback_slot("acc", "s1")
    assert info.value.code == "SLOT_NO_BACKUP"
    assert chain.stats()["rollbacks"] == 1


def test_verify_marks_checksum_mismatch_corrupt():
    chain = build()
    chain.create_slot("acc", "s1", {"hp": 5}, checksum="0000000000000000")
    assert chain.verify_slot("acc", "s1") is False
    assert chain.list_slots("acc")[0].status == "corrupt"
    assert chain.stats()["verifications"] == 1


def test_verify_accepts_matching_checksum():
    chain = build()
    chain.create_slot("acc", "s1", dict(P1))
    assert chain.verify_slot("acc", "s1") is True
    meta = chain.list_slots("acc")[0]
    assert (meta.status, meta.checksum) == ("ok", P1_CHECKSUM)


def test_list_slots_is_sorted_and_per_account():
    chain = build()
    chain.create_slot("b", "s1", {"a": 1})
    chain.create_slot("a", "s2", {"a": 2})
    chain.create_slot("a", "s1", {"a": 3})
    assert [m.slot_id for m in chain.list_slots("a")] == ["s1", "s2"]
    assert [m.slot_id for m in chain.list_slots("b")] == ["s1"]
    assert chain.list_slots("missing") == []


def test_concurrent_migrate_runs_each_step_once():
    chain = build()
    calls = []
    record_lock = threading.Lock()

    def record(pair):
        with record_lock:
            calls.append(pair)

    def m12(payload, version):
        record((1, 2))
        return {**payload, "step1": 1}

    def m23(payload, version):
        record((2, 3))
        return {**payload, "step2": version * 2}

    def m34(payload, version):
        record((3, 4))
        return {**payload, "step3": [version]}

    chain.register_migration(1, 2, m12)
    chain.register_migration(2, 3, m23)
    chain.register_migration(3, 4, m34)
    chain.create_slot("acc", "s1", dict(P1))

    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            results.append(chain.migrate_slot("acc", "s1"))
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [meta.version for meta in results] == [4] * 8
    assert sorted(calls) == [(1, 2), (2, 3), (3, 4)]
    assert chain.stats()["steps"] == 3


def test_snapshot_restore_keeps_slots_and_backup():
    chain = build()
    register_all(chain, [])
    chain.create_slot("acc", "s1", dict(P1))
    chain.create_slot("acc", "s2", {"hp": 3})
    chain.set_active_slot("acc", "s1")
    chain.migrate_slot("acc", "s1")
    state = chain.snapshot()
    clone = build()
    clone.restore(state)
    assert clone.list_slots("acc") == chain.list_slots("acc")
    assert clone.active_slot("acc") == "s1"
    assert clone.slot_payload("acc", "s1") == MIGRATED
    rolled = clone.rollback_slot("acc", "s1")
    assert (rolled.version, rolled.checksum) == (1, P1_CHECKSUM)
    chain.create_slot("acc", "s3", {"hp": 4})
    assert [m.slot_id for m in clone.list_slots("acc")] == ["s1", "s2"]


def test_restore_rejects_bad_state():
    chain = build()
    chain.create_slot("acc", "s1", dict(P1))
    with pytest.raises(mc.SaveError) as info:
        chain.restore({"current_version": 4})
    assert info.value.code == "SNAPSHOT_INVALID"
    assert [m.slot_id for m in chain.list_slots("acc")] == ["s1"]
    assert chain.slot_payload("acc", "s1") == P1
