import pytest

import geomip_morph_guard as mg


def make(**kwargs):
    return mg.GeomipMorphGuard(**kwargs)


def test_register_block_limits_and_duplicates():
    a = make(max_blocks=2, morph_frames=4)
    first = a.register_block("b0", vertices=(0, 1, 2), level=0)
    a.register_block("b1", vertices=(2, 3))
    assert first.block_id == "b0"
    assert first.level == 0
    assert first.visible is True
    assert first.vertices == (0, 1, 2)
    with pytest.raises(mg.MorphError) as limit:
        a.register_block("b2")
    assert limit.value.code == "MORPH_BLOCK_LIMIT"
    other = make(max_blocks=4)
    other.register_block("dup")
    with pytest.raises(mg.MorphError) as dupe:
        other.register_block("dup")
    assert dupe.value.code == "MORPH_DUPLICATE_BLOCK"
    assert a.stats()["blocks"] == 2


def test_set_block_level_validates_levels():
    a = make(max_level=4)
    a.register_block("b0")
    with pytest.raises(mg.MorphError) as unknown:
        a.set_block_level("ghost", 1)
    assert unknown.value.code == "MORPH_UNKNOWN_BLOCK"
    with pytest.raises(mg.MorphError) as high:
        a.set_block_level("b0", 5)
    assert high.value.code == "MORPH_LEVEL_RANGE"
    with pytest.raises(mg.MorphError) as low:
        a.set_block_level("b0", -1)
    assert low.value.code == "MORPH_LEVEL_RANGE"
    with pytest.raises(mg.MorphError) as register_level:
        a.register_block("b9", level=9)
    assert register_level.value.code == "MORPH_LEVEL_RANGE"


def test_same_level_keeps_factor_at_one():
    a = make(morph_frames=4)
    a.register_block("b0", level=1)
    state = a.set_block_level("b0", 1)
    assert state.level == 1
    assert a.morph_factor("b0") == 1.0
    assert a.advance() == {}
    assert a.stats()["started"] == 0


def test_morph_factor_progresses_by_advance():
    a = make(morph_frames=4, step_limit=1)
    a.register_block("b0", level=0)
    a.set_block_level("b0", 1)
    assert a.morph_factor("b0") == 0.0
    assert a.advance() == {"b0": 0.25}
    assert a.morph_factor("b0") == 0.25
    assert a.advance() == {"b0": 0.5}
    assert a.advance() == {"b0": 0.75}
    assert a.morph_factor("b0") == 0.75


def test_advance_respects_step_limit():
    a = make(morph_frames=4, step_limit=2)
    a.register_block("b0", level=0)
    a.set_block_level("b0", 1)
    assert a.advance(frames=5) == {"b0": 0.5}
    assert a.morph_factor("b0") == 0.5


def test_morph_completes_and_settles():
    a = make(morph_frames=4, step_limit=2)
    a.register_block("b0", level=0)
    a.set_block_level("b0", 1)
    a.advance(frames=2)
    a.advance(frames=2)
    assert a.morph_factor("b0") == 1.0
    state = a.block_state("b0")
    assert state.level == 1
    assert state.morph_from == 1
    assert state.progress == 4
    assert a.stats()["completed"] == 1


def test_advance_omits_finished_blocks():
    a = make(morph_frames=4, step_limit=2, max_concurrent=4)
    a.register_block("b0")
    a.register_block("b1")
    a.set_block_level("b0", 1)
    a.set_block_level("b1", 1)
    assert a.advance(frames=2) == {"b0": 0.5, "b1": 0.5}
    assert a.advance(frames=2) == {}
    assert a.stats()["completed"] == 2


def test_blend_positions_midpoint():
    a = make(morph_frames=4, step_limit=2)
    a.register_block("b0", level=0)
    a.set_block_level("b0", 1)
    a.advance(frames=2)
    blended = a.blend_positions(
        "b0",
        [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
        [(2.0, 4.0, 6.0), (3.0, 3.0, 3.0)],
    )
    assert blended == ((1.0, 2.0, 3.0), (2.0, 2.0, 2.0))


def test_blend_positions_endpoints():
    a = make(morph_frames=4, step_limit=4)
    a.register_block("b0", level=0)
    a.set_block_level("b0", 1)
    high = [(0.0, 1.0, 2.0)]
    low = [(4.0, 5.0, 6.0)]
    assert a.blend_positions("b0", high, low) == ((0.0, 1.0, 2.0),)
    a.advance(frames=4)
    assert a.blend_positions("b0", high, low) == ((4.0, 5.0, 6.0),)


def test_blend_rejects_mismatched_geometry():
    a = make()
    a.register_block("b0")
    with pytest.raises(mg.MorphError) as count:
        a.blend_positions("b0", [(0.0, 0.0, 0.0)], [(1.0, 1.0, 1.0), (2.0, 2.0, 2.0)])
    assert count.value.code == "MORPH_GEOMETRY_MISMATCH"
    with pytest.raises(mg.MorphError) as arity:
        a.blend_positions("b0", [(0.0, 0.0, 0.0)], [(1.0, 1.0)])
    assert arity.value.code == "MORPH_GEOMETRY_MISMATCH"


def test_seam_vertices_shared_sorted():
    a = make()
    a.register_block("north", vertices=(0, 1, 2, 5))
    a.register_block("south", vertices=(2, 5, 7))
    a.register_block("east", vertices=(5, 2, 9))
    assert a.seam_vertices("north", "south") == (2, 5)
    assert a.seam_vertices("south", "north") == (2, 5)
    assert a.seam_vertices("east", "north") == (2, 5)


def test_seam_vertices_disjoint_and_unknown():
    a = make()
    a.register_block("north", vertices=(0, 1))
    a.register_block("south", vertices=(7, 8))
    assert a.seam_vertices("north", "south") == ()
    with pytest.raises(mg.MorphError) as unknown:
        a.seam_vertices("north", "ghost")
    assert unknown.value.code == "MORPH_UNKNOWN_BLOCK"


def test_concurrent_morph_limit():
    a = make(max_concurrent=2, morph_frames=4, step_limit=2)
    for name in ("b0", "b1", "b2"):
        a.register_block(name)
    a.set_block_level("b0", 1)
    a.set_block_level("b1", 1)
    with pytest.raises(mg.MorphError) as full:
        a.set_block_level("b2", 1)
    assert full.value.code == "MORPH_CONCURRENCY_LIMIT"
    assert a.block_state("b2").level == 0
    a.advance(frames=2)
    a.advance(frames=2)
    assert a.set_block_level("b2", 1).level == 1


def test_snapshot_restore_roundtrip():
    a = make(morph_frames=4, step_limit=1)
    a.register_block("b0", vertices=(0, 1, 2))
    a.register_block("b1", vertices=(2, 3))
    a.set_block_level("b0", 1)
    a.advance()
    state = a.snapshot()
    b = make(morph_frames=4, step_limit=1)
    b.restore(state)
    assert b.morph_factor("b0") == 0.25
    assert b.block_state("b0") == a.block_state("b0")
    assert b.seam_vertices("b0", "b1") == (2,)
    assert b.stats() == a.stats()


def test_restore_rejects_malformed_state():
    a = make()
    with pytest.raises(mg.MorphError) as bad:
        a.restore({"blocks": "not-a-list"})
    assert bad.value.code == "MORPH_SNAPSHOT_INVALID"
    assert a.stats()["blocks"] == 0
    assert a.stats()["completed"] == 0
