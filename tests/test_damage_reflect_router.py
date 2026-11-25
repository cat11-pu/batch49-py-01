import pytest

import damage_reflect_router as dr


def router(**kwargs):
    return dr.DamageReflectRouter(**kwargs)


def test_register_reflect_rejects_invalid_ratio_and_caps():
    r = router()
    r.register_target("b")
    for ratio, cap in ((0.0, 100), (1.5, 100), (-0.25, 100)):
        with pytest.raises(dr.ReflectRoutingError) as excinfo:
            r.register_reflect("b", ratio, single_cap=cap)
        assert excinfo.value.code == "INVALID_REFLECT"
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.register_reflect("b", 0.5, single_cap=-1)
    assert excinfo.value.code == "INVALID_REFLECT"


def test_route_plain_damage_records_hit():
    r = router()
    r.register_target("b")
    hit = r.route("h1", "a", "b", 300)
    assert hit.final_target == "b"
    assert hit.amount == 300
    assert hit.reflected == 0
    assert hit.reflected_to is None
    assert hit.chain == ("b",)
    assert len(r.ledger()) == 1


def test_route_rejects_unknown_target_and_bad_amount():
    r = router()
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.route("h1", "a", "ghost", 100)
    assert excinfo.value.code == "UNKNOWN_TARGET"
    r.register_target("b")
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.route("h2", "a", "b", 0)
    assert excinfo.value.code == "INVALID_DAMAGE"


def test_route_is_idempotent_by_hit_id():
    r = router()
    r.register_target("b")
    first = r.route("h1", "a", "b", 300)
    second = r.route("h1", "a", "b", 300)
    assert second == first
    assert len(r.ledger()) == 1
    assert r.stats()["routed"] == 1


def test_reflect_returns_ratio_to_attacker():
    r = router()
    r.register_target("b")
    r.register_reflect("b", 0.5, single_cap=10000, frame_cap=10000)
    hit = r.route("h1", "a", "b", 300)
    assert hit.amount == 300
    assert hit.reflected == 150
    assert hit.reflected_to == "a"
    assert hit.final_target == "b"
    assert r.frame_total("b", 0) == 150


def test_reflect_single_cap_trims_overflow():
    r = router()
    r.register_target("b")
    r.register_reflect("b", 0.5, single_cap=200, frame_cap=10000)
    hit = r.route("h1", "a", "b", 1000)
    assert hit.reflected == 200
    assert hit.dropped == 300


def test_reflect_frame_cap_accumulates_across_hits():
    r = router()
    r.register_target("b")
    r.register_reflect("b", 0.5, single_cap=10000, frame_cap=300)
    first = r.route("h1", "a", "b", 400)
    second = r.route("h2", "a", "b", 400)
    assert first.reflected == 200
    assert second.reflected == 100
    assert r.frame_total("b", 0) == 300


def test_unregister_reflect_disables_reflection():
    r = router()
    r.register_target("b")
    r.register_reflect("b", 0.5, single_cap=10000)
    assert r.unregister_reflect("b") is True
    hit = r.route("h1", "a", "b", 300)
    assert hit.reflected == 0
    assert r.unregister_reflect("b") is False


def test_redirect_moves_damage_to_target():
    r = router()
    r.register_target("a")
    r.register_target("b")
    r.register_redirect("a", "b")
    hit = r.route("h1", "src", "a", 250)
    assert hit.original_target == "a"
    assert hit.final_target == "b"
    assert hit.amount == 250
    assert hit.chain == ("a", "b")


def test_redirect_higher_priority_wins():
    r = router()
    for target in ("a", "b", "c"):
        r.register_target(target)
    r.register_redirect("a", "b", priority=5)
    r.register_redirect("a", "c", priority=9)
    hit = r.route("h1", "src", "a", 250)
    assert hit.final_target == "c"
    assert hit.chain == ("a", "c")


def test_redirect_requires_registered_target():
    r = router()
    r.register_target("a")
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.register_redirect("a", "missing")
    assert excinfo.value.code == "UNKNOWN_TARGET"
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.register_redirect("missing", "a")
    assert excinfo.value.code == "UNKNOWN_TARGET"


def test_settle_and_attribution_expose_recorded_hit():
    r = router()
    r.register_target("b")
    r.register_reflect("b", 0.5, single_cap=10000)
    routed = r.route("h1", "a", "b", 300)
    assert r.settle("h1") == routed
    attribution = r.attribution("h1")
    assert attribution["source_id"] == "a"
    assert attribution["original_target"] == "b"
    assert attribution["final_target"] == "b"
    assert attribution["reflected_to"] == "a"
    assert attribution["reflected"] == 150
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.settle("missing")
    assert excinfo.value.code == "UNKNOWN_HIT"


def test_attribution_without_reflection_keeps_plain_fields():
    r = router()
    r.register_target("b")
    r.route("h1", "a", "b", 120)
    assert r.attribution("h1") == {
        "source_id": "a",
        "original_target": "b",
        "final_target": "b",
        "reflected_to": None,
        "reflected": 0,
    }


def test_reset_frame_restores_frame_cap():
    r = router()
    r.register_target("b")
    r.register_reflect("b", 0.5, single_cap=10000, frame_cap=300)
    r.route("h1", "a", "b", 400)
    r.route("h2", "a", "b", 400)
    assert r.frame_total("b", 0) == 300
    r.reset_frame(0)
    assert r.frame_total("b", 0) == 0
    hit = r.route("h3", "a", "b", 400)
    assert hit.reflected == 200


def test_snapshot_restore_roundtrip_and_invalid_state():
    r = router()
    r.register_target("a")
    r.register_target("b")
    r.register_redirect("a", "b", priority=3)
    r.register_reflect("b", 0.5, single_cap=10000)
    r.route("h1", "src", "a", 200)
    saved = r.snapshot()
    r.route("h2", "src", "a", 100)
    assert len(r.ledger()) == 2
    r.restore(saved)
    assert len(r.ledger()) == 1
    assert r.frame_total("b", 0) == 100
    assert r.settle("h1").final_target == "b"
    with pytest.raises(dr.ReflectRoutingError) as excinfo:
        r.restore({"broken": []})
    assert excinfo.value.code == "INVALID_SNAPSHOT"
    assert len(r.ledger()) == 1
