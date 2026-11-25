import pytest

import contact_impulse_resolver as ci


def resolver(**kwargs):
    return ci.ContactImpulseResolver(**kwargs)


def circle(body_id, x=0.0, y=0.0, **kwargs):
    return ci.RigidBody(body_id=body_id, shape="circle", x=x, y=y, **kwargs)


def box(body_id, x=0.0, y=0.0, **kwargs):
    return ci.RigidBody(body_id=body_id, shape="box", x=x, y=y, **kwargs)


def test_add_body_rejects_duplicate_and_invalid_shape():
    r = resolver()
    r.add_body(circle("a"))
    with pytest.raises(ci.ContactError) as excinfo:
        r.add_body(circle("a"))
    assert excinfo.value.code == "DUPLICATE_BODY"
    with pytest.raises(ci.ContactError) as excinfo:
        r.add_body(ci.RigidBody(body_id="bad", shape="capsule"))
    assert excinfo.value.code == "INVALID_BODY"
    with pytest.raises(ci.ContactError) as excinfo:
        r.add_body(circle("tiny", radius=0.0))
    assert excinfo.value.code == "INVALID_BODY"
    assert r.remove_body("a") is True
    assert r.remove_body("a") is False
    with pytest.raises(ci.ContactError) as excinfo:
        r.body("a")
    assert excinfo.value.code == "UNKNOWN_BODY"


def test_circle_circle_contact_geometry():
    r = resolver()
    r.add_body(circle("a", 0.0, 0.0))
    r.add_body(circle("b", 1.5, 0.0))
    r.step(0.1)
    found = r.contacts()
    assert len(found) == 1
    contact = found[0]
    assert (contact.a, contact.b) == ("a", "b")
    assert contact.nx == pytest.approx(1.0)
    assert contact.ny == pytest.approx(0.0)
    assert contact.penetration == pytest.approx(0.5)


def test_separated_circles_produce_no_contact():
    r = resolver()
    r.add_body(circle("a", 0.0, 0.0))
    r.add_body(circle("b", 3.0, 0.0))
    r.step(0.1)
    assert r.contacts() == []


def test_box_box_penetration_is_corrected():
    r = resolver()
    r.add_body(box("bx", 0.0, 0.8, restitution=0.0))
    r.add_body(box("fl", 0.0, -1.0, half_w=10.0, half_h=1.0, static=True))
    r.step(0.1)
    found = r.contacts()
    assert len(found) == 1
    assert found[0].penetration == pytest.approx(0.2)
    assert r.body("bx").y == pytest.approx(0.96)


def test_circle_floor_impulse_with_restitution():
    r = resolver()
    r.add_body(circle("ball", 0.0, 1.99, vy=-10.0))
    r.add_body(box("fl", 0.0, -1.0, half_w=10.0, half_h=1.0, static=True))
    r.step(0.1)
    assert r.contacts()[0].impulse == pytest.approx(15.0)
    assert r.body("ball").vy == pytest.approx(5.0)
    assert r.body("fl").y == pytest.approx(-1.0)
    assert r.body("fl").vy == pytest.approx(0.0)


def test_static_body_does_not_move():
    r = resolver()
    r.add_body(box("fl", 0.0, -1.0, half_w=10.0, half_h=1.0, static=True, vy=99.0))
    r.step(0.1)
    assert r.body("fl").y == pytest.approx(-1.0)
    assert r.body("fl").x == pytest.approx(0.0)


def test_circle_box_side_impulse():
    r = resolver()
    r.add_body(circle("ball", 2.9, 0.0, vx=-4.0))
    r.add_body(box("wall", 0.0, 0.0, half_w=2.0, half_h=2.0, static=True))
    r.step(0.1)
    assert r.body("ball").vx == pytest.approx(2.0)


def test_position_correction_capped_per_frame():
    r = resolver()
    r.add_body(circle("ball", 0.0, 0.5, restitution=0.0))
    r.add_body(box("fl", 0.0, -1.0, half_w=10.0, half_h=1.0, static=True))
    r.step(0.1)
    assert r.contacts()[0].penetration == pytest.approx(0.5)
    assert r.body("ball").y == pytest.approx(0.75)


def test_contacts_sorted_and_pairs_normalized():
    r = resolver()
    r.add_body(circle("c", 3.0, 0.0))
    r.add_body(circle("a", 0.0, 0.0))
    r.add_body(circle("b", 1.5, 0.0))
    r.step(0.1)
    pairs = [(contact.a, contact.b) for contact in r.contacts()]
    assert pairs == [("a", "b"), ("b", "c")]
    stats = r.stats()
    assert stats["bodies"] == 3
    assert stats["contacts"] == 2


def test_two_fixed_runs_are_reproducible():
    def build():
        r = resolver()
        r.add_body(circle("ball", 0.0, 1.99, vy=-10.0))
        r.add_body(box("fl", 0.0, -1.0, half_w=10.0, half_h=1.0, static=True))
        return r

    first = build()
    second = build()
    for _ in range(3):
        first.step(0.1)
        second.step(0.1)
    assert first.body("ball").y == pytest.approx(second.body("ball").y)
    assert first.body("ball").vy == pytest.approx(second.body("ball").vy)


def test_sleeping_body_skips_integration():
    r = resolver()
    r.add_body(circle("sleeper", 0.0, 5.0, vy=-10.0))
    r.add_body(circle("mover", 10.0, 0.0, vx=1.0))
    r.sleep_body("sleeper")
    assert r.sleeping("sleeper") is True
    r.step(0.1)
    assert r.body("sleeper").y == pytest.approx(5.0)
    assert r.body("sleeper").vy == pytest.approx(-10.0)
    assert r.body("mover").x == pytest.approx(10.1)


def test_apply_impulse_wakes_body():
    r = resolver()
    r.add_body(circle("ball", 0.0, 0.0, mass=2.0))
    r.sleep_body("ball")
    assert r.sleeping("ball") is True
    r.apply_impulse("ball", 0.0, 4.0)
    assert r.sleeping("ball") is False
    assert r.body("ball").vy == pytest.approx(2.0)
    r.step(0.1)
    assert r.body("ball").y == pytest.approx(0.2)


def test_step_rejects_invalid_dt():
    r = resolver()
    r.add_body(circle("a"))
    for dt in (0.0, -0.1, 0.5):
        with pytest.raises(ci.ContactError) as excinfo:
            r.step(dt)
        assert excinfo.value.code == "INVALID_STEP"


def test_set_material_updates_body():
    r = resolver()
    r.add_body(circle("a"))
    r.set_material("a", 0.9, 0.6)
    assert r.body("a").restitution == pytest.approx(0.9)
    assert r.body("a").friction == pytest.approx(0.6)
    with pytest.raises(ci.ContactError) as excinfo:
        r.set_material("missing", 0.5, 0.5)
    assert excinfo.value.code == "UNKNOWN_BODY"


def test_snapshot_restore_roundtrip_and_invalid_state():
    r = resolver()
    r.add_body(circle("ball", 0.0, 5.0, vy=-10.0))
    r.add_body(box("fl", 0.0, -1.0, half_w=10.0, half_h=1.0, static=True))
    r.step(0.1)
    assert r.body("ball").y == pytest.approx(4.0)
    saved = r.snapshot()
    r.step(0.1)
    assert r.body("ball").y == pytest.approx(3.0)
    r.restore(saved)
    assert r.body("ball").y == pytest.approx(4.0)
    assert r.body("ball").vy == pytest.approx(-10.0)
    with pytest.raises(ci.ContactError) as excinfo:
        r.restore({"nope": 1})
    assert excinfo.value.code == "INVALID_SNAPSHOT"
    assert r.body("ball").y == pytest.approx(4.0)
