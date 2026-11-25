import pytest

import portal_visibility_solver as pv


def corridor(**kwargs):
    solver = pv.PortalVisibilitySolver(**kwargs)
    solver.add_cell("a", 0.0, 0.0, 10.0, 10.0)
    solver.add_cell("b", 10.0, 0.0, 20.0, 10.0)
    solver.add_cell("c", 20.0, 0.0, 30.0, 10.0)
    solver.add_portal("p1", "a", "b", "v", 10.0, 2.0, 4.0)
    solver.add_portal("p2", "b", "c", "v", 20.0, 1.0, 3.0)
    return solver


def test_add_cell_validates_rectangles_and_ids():
    solver = pv.PortalVisibilitySolver()
    solver.add_cell("a", 0.0, 0.0, 10.0, 10.0)
    with pytest.raises(pv.PortalError) as exc:
        solver.add_cell("a", 0.0, 0.0, 5.0, 5.0)
    assert exc.value.code == "PORTAL_CELL_EXISTS"
    for rect in [(0.0, 0.0, 0.0, 10.0), (0.0, 0.0, 10.0, -1.0)]:
        with pytest.raises(pv.PortalError) as exc:
            solver.add_cell("b", *rect)
        assert exc.value.code == "PORTAL_INVALID_CELL"


def test_add_portal_validates_links_and_axis():
    solver = corridor()
    cases = [(("p3", "a", "zz", "v", 10.0, 0.0, 1.0), "PORTAL_UNKNOWN_CELL"),
             (("p4", "a", "b", "x", 10.0, 0.0, 1.0), "PORTAL_INVALID_PORTAL"),
             (("p5", "a", "b", "v", 10.0, 4.0, 2.0), "PORTAL_INVALID_PORTAL"),
             (("p1", "a", "b", "v", 10.0, 0.0, 1.0), "PORTAL_PORTAL_EXISTS")]
    for args, code in cases:
        with pytest.raises(pv.PortalError) as exc:
            solver.add_portal(*args)
        assert exc.value.code == code


def test_portal_cap_is_enforced():
    solver = pv.PortalVisibilitySolver(max_portals=1)
    solver.add_cell("a", 0.0, 0.0, 10.0, 10.0)
    solver.add_cell("b", 10.0, 0.0, 20.0, 10.0)
    solver.add_portal("p1", "a", "b", "v", 10.0, 0.0, 10.0)
    with pytest.raises(pv.PortalError) as exc:
        solver.add_portal("p2", "a", "b", "v", 10.0, 2.0, 3.0)
    assert exc.value.code == "PORTAL_LIMIT"


def test_solve_requires_camera_cell():
    solver = corridor()
    with pytest.raises(pv.PortalError) as exc:
        solver.solve()
    assert exc.value.code == "PORTAL_NO_CAMERA"
    with pytest.raises(pv.PortalError) as exc:
        solver.set_camera_cell("zz")
    assert exc.value.code == "PORTAL_UNKNOWN_CELL"


def test_solve_single_cell_region():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    solver.close_portal("p1")
    assert solver.solve() == ["a"]
    assert solver.visible_region("a") == (0.0, 0.0, 10.0, 10.0)
    assert solver.visible_area("a") == 100.0


def test_unknown_door_state_is_visible():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    assert solver.solve() == ["a", "b", "c"]


def test_closed_portal_hides_neighbour():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    solver.close_portal("p1")
    assert solver.solve() == ["a"]
    assert solver.visible_region("b") is None


def test_region_is_clipped_to_portal_opening():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    solver.close_portal("p2")
    assert solver.solve() == ["a", "b"]
    assert solver.visible_region("b") == (10.0, 2.0, 20.0, 4.0)
    assert solver.visible_area("b") == 20.0


def test_depth_limit_stops_propagation():
    solver = corridor(max_depth=1)
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    assert solver.solve() == ["a", "b"]
    assert solver.visible_region("c") is None


def test_cycle_visits_each_cell_once():
    solver = corridor()
    solver.add_portal("p3", "c", "a", "v", 25.0, 0.0, 10.0)
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    cells = solver.solve()
    assert cells == ["a", "b", "c"]
    assert len(set(cells)) == 3


def test_clip_budget_limits_crossings():
    solver = pv.PortalVisibilitySolver(clip_budget=1)
    solver.add_cell("a", 0.0, 0.0, 10.0, 10.0)
    solver.add_cell("b", 10.0, 0.0, 20.0, 10.0)
    solver.add_cell("d", -10.0, 0.0, 0.0, 10.0)
    solver.add_portal("p1", "a", "b", "v", 10.0, 0.0, 10.0)
    solver.add_portal("p2", "a", "d", "v", 0.0, 0.0, 10.0)
    solver.set_camera_cell("a")
    solver.set_eye(5.0, 5.0)
    assert solver.solve() == ["a", "b"]
    assert solver.budget_used() == 1


def test_door_fraction_rejects_out_of_range():
    solver = corridor()
    for fraction in (-0.1, 1.5):
        with pytest.raises(pv.PortalError) as exc:
            solver.set_door_fraction("p1", fraction)
        assert exc.value.code == "PORTAL_INVALID_FRACTION"
    with pytest.raises(pv.PortalError) as exc:
        solver.set_door_fraction("nope", 0.5)
    assert exc.value.code == "PORTAL_UNKNOWN_PORTAL"


def test_zero_fraction_blocks_like_closed():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    solver.open_portal("p1")
    solver.set_door_fraction("p1", 0.0)
    assert solver.solve() == ["a"]


def test_visible_objects_filters_by_cell():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    solver.set_door_fraction("p2", 0.0)
    solver.solve()
    objects = [("o3", "b"), ("o1", "a"), ("o2", "c"), ("o4", "a")]
    assert solver.visible_objects(objects) == ["o1", "o3", "o4"]


def test_snapshot_restore_roundtrip_and_malformed():
    solver = corridor()
    solver.set_camera_cell("a")
    solver.set_eye(1.0, 1.0)
    solver.set_door_fraction("p1", 1.0)
    first = solver.solve()
    state = solver.snapshot()
    clone = pv.PortalVisibilitySolver()
    clone.restore(state)
    assert clone.snapshot()["budget"] == state["budget"]
    assert clone.solve() == first
    assert clone.visible_region("b") == solver.visible_region("b")
    bad_states = [None, {}, dict(state, version=5),
                  {key: value for key, value in state.items() if key != "portals"},
                  dict(state, cells="nope")]
    for bad in bad_states:
        with pytest.raises(pv.PortalError) as exc:
            solver.restore(bad)
        assert exc.value.code == "PORTAL_SNAPSHOT_INVALID"
    assert solver.solve() == first
