import pytest

import static_batch_builder as sb

TRI = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
TRI_IDX = (0, 1, 2)


def make(**kwargs):
    return sb.StaticBatchBuilder(**kwargs)


def add(builder, mesh_id, material="steel", lightmap="lm0",
        vertices=TRI, indices=TRI_IDX):
    return builder.add_mesh(mesh_id, material=material, lightmap=lightmap,
                            vertices=vertices, indices=indices)


def test_group_key_format_and_validation():
    a = make()
    assert a.group_key("steel", "lm0") == "steel|lm0"
    assert a.group_key("glass", "lm2") == "glass|lm2"
    with pytest.raises(sb.BatchError) as empty:
        a.group_key("", "lm0")
    assert empty.value.code == "BATCH_INVALID_ARG"
    with pytest.raises(sb.BatchError) as blank:
        a.group_key("steel", "   ")
    assert blank.value.code == "BATCH_INVALID_ARG"


def test_add_mesh_validates_geometry():
    a = make()
    with pytest.raises(sb.BatchError) as empty:
        add(a, "m0", vertices=(), indices=())
    assert empty.value.code == "BATCH_EMPTY_MESH"
    with pytest.raises(sb.BatchError) as index:
        add(a, "m1", vertices=((0.0, 0.0, 0.0),), indices=(0, 2))
    assert index.value.code == "BATCH_BAD_INDEX"
    with pytest.raises(sb.BatchError) as vertex:
        add(a, "m2", vertices=((0.0, 0.0),), indices=(0,))
    assert vertex.value.code == "BATCH_BAD_VERTEX"


def test_add_mesh_reports_group_and_counts():
    a = make()
    assert add(a, "m0") == "steel|lm0"
    assert add(a, "m1", vertices=TRI, indices=TRI_IDX) == "steel|lm0"
    assert add(a, "m2", material="glass") == "glass|lm0"
    assert a.stats()["meshes"] == 3
    assert a.stats()["groups"] == 2


def test_build_merges_vertices_in_insertion_order():
    a = make()
    add(a, "m0")
    second = ((5.0, 0.0, 0.0), (6.0, 0.0, 0.0), (5.0, 1.0, 0.0))
    add(a, "m1", vertices=second)
    batches = a.build()
    assert len(batches) == 1
    batch = batches[0]
    assert batch.batch_id == "steel|lm0#0"
    assert batch.material == "steel"
    assert batch.lightmap == "lm0"
    assert batch.vertices == TRI + second


def test_merged_indices_are_rebased():
    a = make()
    add(a, "m0")
    add(a, "m1")
    batch = a.build()[0]
    assert batch.indices == (0, 1, 2, 3, 4, 5)


def test_groups_split_by_material_and_lightmap():
    a = make()
    add(a, "m0", material="a", lightmap="lm0")
    add(a, "m1", material="a", lightmap="lm1")
    add(a, "m2", material="b", lightmap="lm0")
    batches = a.build()
    assert [b.batch_id for b in batches] == ["a|lm0#0", "a|lm1#0", "b|lm0#0"]
    assert a.stats()["batches"] == 3
    assert a.stats()["groups"] == 3


def test_batch_ids_stable_across_builds():
    a = make()
    add(a, "m0")
    first = a.build()
    second = a.build()
    assert [b.batch_id for b in first] == [b.batch_id for b in second]
    assert a.stats()["builds"] == 2
    assert a.stats()["rebuilt_groups"] == 1


def test_bounds_covers_merged_group():
    a = make()
    add(a, "m0", vertices=((0.0, 0.0, 0.0), (2.0, -1.0, 3.0)),
        indices=(0, 1, 0))
    add(a, "m1", vertices=((-4.0, 5.0, 1.0), (1.0, 2.0, -6.0)),
        indices=(0, 1, 0))
    a.build()
    assert a.bounds("steel|lm0#0") == ((-4.0, -1.0, -6.0), (2.0, 5.0, 3.0))


def test_bounds_unknown_batch_rejected():
    a = make()
    add(a, "m0")
    a.build()
    with pytest.raises(sb.BatchError) as unknown:
        a.bounds("steel|lm0#9")
    assert unknown.value.code == "BATCH_UNKNOWN_BATCH"
    with pytest.raises(sb.BatchError) as before_build:
        a.bounds("steel|lm0#0")
    assert before_build.value.code == "BATCH_UNKNOWN_BATCH"


def test_remove_mesh_drops_group_when_last_reference():
    a = make()
    add(a, "m0", material="a")
    add(a, "m1", material="b")
    assert a.remove_mesh("m1") == 0
    batches = a.build()
    assert [b.batch_id for b in batches] == ["a|lm0#0"]
    assert a.stats()["meshes"] == 1
    with pytest.raises(sb.BatchError) as unknown:
        a.remove_mesh("ghost")
    assert unknown.value.code == "BATCH_UNKNOWN_MESH"


def test_split_overflow_chunk_counts():
    a = make(max_vertices_per_batch=4)
    vertices = tuple((float(i), 0.0, 0.0) for i in range(10))
    add(a, "m0", vertices=vertices, indices=(0, 1, 2))
    parts = a.split_overflow("steel|lm0")
    assert [len(p.vertices) for p in parts] == [4, 4, 2]
    assert [p.batch_id for p in parts] == ["steel|lm0#0", "steel|lm0#1", "steel|lm0#2"]


def test_split_without_overflow_is_single_batch():
    a = make(max_vertices_per_batch=4)
    add(a, "m0")
    parts = a.split_overflow("steel|lm0")
    assert len(parts) == 1
    assert parts[0].vertices == TRI
    with pytest.raises(sb.BatchError) as unknown:
        a.split_overflow("ghost|lm0")
    assert unknown.value.code == "BATCH_UNKNOWN_GROUP"


def test_max_batches_exceeded():
    a = make(max_batches=1)
    add(a, "m0", material="a")
    add(a, "m1", material="b")
    with pytest.raises(sb.BatchError) as limit:
        a.build()
    assert limit.value.code == "BATCH_LIMIT_EXCEEDED"
    assert a.stats()["batches"] == 0


def test_incremental_rebuild_tracks_dirty_groups():
    a = make()
    add(a, "m0", material="a")
    add(a, "m1", material="b")
    a.build()
    assert a.stats()["rebuilt_groups"] == 2
    a.invalidate("a|lm0")
    assert a.stats()["dirty"] == 1
    a.build()
    assert a.stats()["builds"] == 2
    assert a.stats()["rebuilt_groups"] == 3
    assert a.stats()["dirty"] == 0
    assert len(a.build()) == 2


def test_snapshot_restore_roundtrip():
    a = make()
    add(a, "m0")
    add(a, "m1", material="glass")
    a.build()
    state = a.snapshot()
    b = make()
    b.restore(state)
    assert [x.batch_id for x in b.build()] == [x.batch_id for x in a.build()]
    assert b.build()[0].vertices == a.build()[0].vertices
    assert b.stats()["meshes"] == 2
    with pytest.raises(sb.BatchError) as bad:
        b.restore({"meshes": "nope"})
    assert bad.value.code == "BATCH_SNAPSHOT_INVALID"
    assert b.stats()["meshes"] == 2
