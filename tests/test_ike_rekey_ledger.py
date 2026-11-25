import threading

import pytest

import ike_rekey_ledger as ike


def ledger(**kwargs):
    return ike.IkeRekeyLedger(**kwargs)


def test_create_sa_allocates_the_first_generation():
    g = ledger()
    out = g.create_sa("branch-a", now=10)
    assert out["initiator"] == "branch-a"
    assert out["generation"] == 1
    assert out["spi"] == "spi-1"
    assert out["state"] == "active"
    assert out["created_at"] == 10
    assert out["expires_at"] == 3610
    assert out["retire_at"] is None
    view = g.state("branch-a")
    assert view["active_generation"] == 1
    assert view["active_spi"] == "spi-1"
    assert g.stats()["sas_created"] == 1


def test_create_sa_validates_identity_duplicate_and_limit():
    g = ledger(max_initiators=1)
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.create_sa("   ")
    assert exc.value.code == "IKE_INITIATOR_INVALID"
    g.create_sa("branch-a")
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.create_sa("branch-a")
    assert exc.value.code == "IKE_INITIATOR_EXISTS"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.create_sa("branch-b")
    assert exc.value.code == "IKE_INITIATOR_LIMIT"
    other = ledger()
    with pytest.raises(ike.IkeLedgerError) as exc:
        other.create_sa("branch-c", spi="  ")
    assert exc.value.code == "IKE_SPI_INVALID"


def test_receive_advances_the_message_window():
    g = ledger()
    g.create_sa("branch-a")
    out = g.receive("branch-a", 0, "informational", now=5)
    assert out["action"] == "noted"
    assert out["generation"] == 1
    assert out["duplicate"] is False
    view = g.window("branch-a")
    assert view["highest"] == 0
    assert view["size"] == 64
    assert view["seen"] == [0]
    assert g.stats()["messages"] == 1


def test_rekey_request_creates_a_new_generation():
    g = ledger()
    g.create_sa("branch-a")
    out = g.receive("branch-a", 0, "rekey_request", {"nonce": "n1"}, now=50)
    assert out["action"] == "rekeyed"
    assert out["generation"] == 2
    assert out["duplicate"] is False
    gens = g.generations("branch-a")
    assert [item["generation"] for item in gens] == [1, 2]
    assert gens[0]["state"] == "closing"
    assert gens[0]["retire_at"] == 50
    assert gens[1]["state"] == "active"
    assert gens[1]["spi"] == "spi-2"
    assert g.state("branch-a")["active_generation"] == 2
    assert g.stats()["rekeys"] == 1


def test_duplicate_request_returns_the_cached_result():
    g = ledger()
    g.create_sa("branch-a")
    first = g.receive("branch-a", 0, "rekey_request", {"nonce": "n1"}, now=10)
    second = g.receive("branch-a", 0, "rekey_request", {"nonce": "n1"}, now=11)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["generation"] == first["generation"] == 2
    assert len(g.generations("branch-a")) == 2
    assert g.stats()["duplicates"] == 1
    assert g.stats()["rekeys"] == 1


def test_message_ids_outside_the_window_are_refused():
    g = ledger(window_size=4)
    g.create_sa("branch-a")
    g.receive("branch-a", 2, "informational")
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 7, "informational")
    assert exc.value.code == "IKE_MSG_OUT_OF_WINDOW"
    g.receive("branch-a", 6, "informational")
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 1, "informational")
    assert exc.value.code == "IKE_MSG_TOO_OLD"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", -1, "informational")
    assert exc.value.code == "IKE_MSG_INVALID"


def test_out_of_order_message_inside_the_window_is_accepted():
    g = ledger(window_size=4)
    g.create_sa("branch-a")
    g.receive("branch-a", 0, "informational")
    g.receive("branch-a", 3, "informational")
    out = g.receive("branch-a", 1, "informational")
    assert out["action"] == "noted"
    view = g.window("branch-a")
    assert view["highest"] == 3
    assert view["seen"] == [0, 1, 3]


def test_local_rekey_keeps_the_old_generation_during_overlap():
    g = ledger(overlap_seconds=120)
    g.create_sa("branch-a")
    fresh = g.rekey("branch-a", now=100, nonce="local-1")
    assert fresh["generation"] == 2
    assert fresh["state"] == "active"
    assert fresh["created_at"] == 100
    assert fresh["expires_at"] == 3700
    old = g.generations("branch-a")[0]
    assert old["state"] == "closing"
    assert old["retire_at"] == 100
    assert g.state("branch-a")["active_generation"] == 2


def test_delete_sa_rules_for_old_active_and_unknown_generations():
    g = ledger()
    g.create_sa("branch-a")
    g.rekey("branch-a", now=10)
    assert g.delete_sa("branch-a", 1) is True
    assert g.delete_sa("branch-a", 1) is False
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.delete_sa("branch-a", 2)
    assert exc.value.code == "IKE_SA_ACTIVE"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.delete_sa("branch-a", 9)
    assert exc.value.code == "IKE_SA_UNKNOWN"
    gens = g.generations("branch-a")
    assert gens[0]["state"] == "deleted"
    assert gens[1]["state"] == "active"
    assert g.stats()["deletes"] == 1


def test_prune_drops_closing_generation_after_the_overlap():
    g = ledger(overlap_seconds=120)
    g.create_sa("branch-a", now=0)
    g.rekey("branch-a", now=100)
    assert g.prune(now=219) == 0
    assert g.prune(now=220) == 1
    gens = g.generations("branch-a")
    assert [item["generation"] for item in gens] == [2]
    assert g.stats()["pruned"] == 1


def test_receive_validates_kind_and_payload_without_side_effects():
    g = ledger()
    g.create_sa("branch-a")
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 0, "bogus")
    assert exc.value.code == "IKE_KIND_INVALID"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 0, "rekey_request")
    assert exc.value.code == "IKE_PAYLOAD_INVALID"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 0, "rekey_request", {"nonce": ""})
    assert exc.value.code == "IKE_PAYLOAD_INVALID"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 0, "delete_request", {"generation": "x"})
    assert exc.value.code == "IKE_PAYLOAD_INVALID"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 0, "delete_request", {})
    assert exc.value.code == "IKE_PAYLOAD_INVALID"
    assert len(g.generations("branch-a")) == 1
    assert g.window("branch-a")["highest"] == -1
    assert g.stats()["messages"] == 0


def test_delete_request_targets_the_requested_generation():
    g = ledger()
    g.create_sa("branch-a")
    g.rekey("branch-a", now=50)
    out = g.receive("branch-a", 0, "delete_request", {"generation": 1})
    assert out["action"] == "deleted"
    assert out["generation"] == 1
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 1, "delete_request", {"generation": 2})
    assert exc.value.code == "IKE_SA_ACTIVE"
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.receive("branch-a", 2, "delete_request", {"generation": 9})
    assert exc.value.code == "IKE_SA_UNKNOWN"


def test_snapshot_and_restore_roundtrip():
    g = ledger()
    g.create_sa("branch-a")
    g.receive("branch-a", 0, "rekey_request", {"nonce": "n1"}, now=10)
    snap = g.snapshot()
    assert snap["stats"]["rekeys"] == 1
    g.delete_sa("branch-a", 1)
    g.rekey("branch-a", now=20)
    assert g.state("branch-a")["active_generation"] == 3
    g.restore(snap)
    gens = g.generations("branch-a")
    assert [item["generation"] for item in gens] == [1, 2]
    assert gens[0]["state"] == "closing"
    assert gens[1]["state"] == "active"
    assert g.stats()["rekeys"] == 1


def test_restore_rejects_malformed_state():
    g = ledger()
    g.create_sa("branch-a")
    g.rekey("branch-a", now=10)
    with pytest.raises(ike.IkeLedgerError) as exc:
        g.restore({"sas": []})
    assert exc.value.code == "IKE_SNAPSHOT_INVALID"
    assert g.state("branch-a")["active_generation"] == 2
    assert len(g.generations("branch-a")) == 2


def test_concurrent_duplicate_request_rekeys_only_once():
    g = ledger()
    g.create_sa("branch-a")
    results = []
    lock = threading.Lock()

    def worker():
        try:
            out = g.receive("branch-a", 0, "rekey_request", {"nonce": "n1"}, now=10)
        except ike.IkeLedgerError as exc:
            with lock:
                results.append(exc.code)
        else:
            with lock:
                results.append((out["duplicate"], out["generation"]))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [(False, 2)] + [(True, 2)] * 7
    assert len(g.generations("branch-a")) == 2
    assert g.stats()["rekeys"] == 1
