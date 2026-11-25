import threading

import pytest

import save_slot_rotator as sr

P1 = {"hp": 100, "gold": 5}
P1_CRC = "783fadec"
P1_SIZE = 19
P2 = {"hp": 90, "gold": 8}
P3 = {"hp": 80, "gold": 11}


def rotator(**kwargs):
    return sr.SaveSlotRotator(**kwargs)


def test_save_creates_active_with_crc_checksum():
    rot = rotator()
    meta = rot.save("acc", dict(P1))
    assert (meta.account, meta.tier, meta.generation) == ("acc", "active", 1)
    assert meta.checksum == P1_CRC
    assert meta.size == P1_SIZE
    assert meta.status == "ok"
    assert rot.load("acc") == P1
    assert rot.generation("acc") == 1
    assert sr.slot_crc(dict(P1)) == P1_CRC


def test_save_rotates_three_generations():
    rot = rotator()
    rot.save("acc", dict(P1))
    rot.save("acc", P2)
    rot.save("acc", P3)
    rot.save("acc", {"hp": 70, "gold": 14})
    assert rot.load("acc", tier="active") == {"hp": 70, "gold": 14}
    assert rot.load("acc", tier="previous") == P3
    assert rot.load("acc", tier="backup") == P2
    metas = rot.slot_meta("acc")
    assert sorted(metas) == ["active", "backup", "previous"]
    assert (metas["active"].generation, metas["previous"].generation,
            metas["backup"].generation) == (4, 3, 2)


def test_save_rejects_oversize_payload():
    rot = rotator(max_slot_bytes=16)
    with pytest.raises(sr.SlotFault) as info:
        rot.save("acc", dict(P1))
    assert info.value.code == "SLOT_TOO_LARGE"
    assert rot.generation("acc") == 0
    with pytest.raises(sr.SlotFault) as info:
        rot.load("acc")
    assert info.value.code == "SLOT_UNAVAILABLE"


def test_load_rejects_missing_and_unknown_tier():
    rot = rotator()
    with pytest.raises(sr.SlotFault) as info:
        rot.load("acc")
    assert info.value.code == "SLOT_UNAVAILABLE"
    rot.save("acc", dict(P1))
    with pytest.raises(sr.SlotFault) as info:
        rot.load("acc", tier="snapshot")
    assert info.value.code == "SLOT_INVALID_TIER"


def test_load_detects_corrupt_active():
    rot = rotator()
    meta = rot.adopt_slot("acc", "active", {"hp": 1}, "00000000", generation=2)
    assert (meta.generation, meta.status) == (2, "unverified")
    with pytest.raises(sr.SlotFault) as info:
        rot.load("acc")
    assert info.value.code == "SLOT_CORRUPT"
    assert rot.slot_meta("acc")["active"].status == "corrupt"
    assert rot.stats()["corrupt_loads"] == 1


def test_stage_mark_promote_makes_active():
    rot = rotator()
    temp_id = rot.stage("acc", dict(P1))
    records = rot.pending("acc")
    assert len(records) == 1
    record = records[0]
    assert (record.complete, record.promotion_marked) == (True, False)
    assert (record.checksum, record.size) == (P1_CRC, P1_SIZE)
    rot.mark_promotion("acc", temp_id)
    assert rot.pending("acc")[0].promotion_marked is True
    meta = rot.promote("acc", temp_id)
    assert (meta.tier, meta.generation, meta.checksum) == ("active", 1, P1_CRC)
    assert rot.load("acc") == P1
    assert rot.pending("acc") == []
    assert rot.stats()["promotions"] == 1


def test_recover_discards_incomplete_temp():
    rot = rotator()
    rot.stage("acc", {"hp": 1}, complete=False)
    outcome = rot.recover("acc")
    assert outcome == {"promoted": 0, "discarded": 1, "candidates": 0}
    assert rot.pending("acc") == []
    with pytest.raises(sr.SlotFault) as info:
        rot.load("acc")
    assert info.value.code == "SLOT_UNAVAILABLE"
    assert rot.stats()["recoveries"] == 1


def test_recover_promotes_marked_temp():
    rot = rotator()
    temp_id = rot.stage("acc", P2)
    rot.mark_promotion("acc", temp_id)
    outcome = rot.recover("acc")
    assert outcome == {"promoted": 1, "discarded": 0, "candidates": 0}
    assert rot.load("acc") == P2
    assert rot.generation("acc") == 1
    assert rot.pending("acc") == []


def test_concurrent_saves_assign_unique_generations():
    rot = rotator()
    payloads = [{"hp": 100 + index, "gold": index} for index in range(8)]
    results = []
    errors = []
    result_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(payload):
        barrier.wait()
        try:
            meta = rot.save("acc", payload)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            with result_lock:
                errors.append(exc)
            return
        with result_lock:
            results.append((meta.generation, payload))

    threads = [threading.Thread(target=worker, args=(payload,))
               for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(generation for generation, _ in results) == list(range(1, 9))
    active_generation, active_payload = max(results)
    assert active_generation == 8
    assert rot.load("acc") == active_payload
    assert rot.stats()["saves"] == 8


def test_rotate_moves_tiers_and_bumps_generation():
    rot = rotator()
    rot.save("acc", dict(P1))
    rot.save("acc", P2)
    moved = rot.rotate("acc")
    assert moved == {"previous": 2, "backup": 1}
    with pytest.raises(sr.SlotFault) as info:
        rot.load("acc")
    assert info.value.code == "SLOT_UNAVAILABLE"
    assert rot.load("acc", tier="previous") == P2
    assert rot.load("acc", tier="backup") == P1
    assert rot.generation("acc") == 3


def test_prune_keeps_requested_candidates():
    rot = rotator()
    rot.save("acc", dict(P1))
    for index in range(3):
        rot.stage("acc", {"hp": index}, complete=False)
    assert len(rot.pending("acc")) == 3
    assert rot.prune("acc", keep_candidates=1) == 2
    assert len(rot.pending("acc")) == 1
    assert rot.load("acc") == P1
    assert rot.stats()["pruned"] == 2


def test_snapshot_restore_roundtrip():
    rot = rotator()
    rot.save("acc", dict(P1))
    rot.save("acc", P2)
    rot.stage("acc", {"hp": 7})
    state = rot.snapshot()
    clone = rotator()
    clone.restore(state)
    assert clone.load("acc") == P2
    assert clone.load("acc", tier="previous") == P1
    assert clone.generation("acc") == 2
    assert len(clone.pending("acc")) == 1
    assert clone.pending("acc")[0].temp_id == rot.pending("acc")[0].temp_id
    rot.save("acc", P3)
    assert clone.load("acc") == P2


def test_restore_rejects_bad_state():
    rot = rotator()
    rot.save("acc", dict(P1))
    with pytest.raises(sr.SlotFault) as info:
        rot.restore({"slots": {}})
    assert info.value.code == "SLOT_INVALID_STATE"
    assert rot.load("acc") == P1
    assert rot.generation("acc") == 1


def test_accounts_are_isolated():
    rot = rotator()
    rot.save("left", dict(P1))
    rot.save("right", P2)
    rot.save("right", P3)
    assert rot.load("left") == P1
    assert rot.load("right") == P3
    assert rot.generation("left") == 1
    assert rot.generation("right") == 2
    rot.rotate("left")
    assert rot.load("right") == P3
    assert rot.slot_meta("left")["previous"].account == "left"


def test_backup_keeps_first_generation_content():
    rot = rotator()
    rot.save("acc", dict(P1))
    rot.save("acc", P2)
    rot.save("acc", P3)
    assert rot.load("acc", tier="backup") == P1
    meta = rot.slot_meta("acc")["backup"]
    assert (meta.generation, meta.checksum, meta.size) == (1, P1_CRC, P1_SIZE)
