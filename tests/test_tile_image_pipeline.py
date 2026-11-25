import copy

import pytest

import tile_image_pipeline as tp


def pipeline(**kwargs):
    return tp.TileImagePipeline(32, 20, **kwargs)


def test_plan_tiles_lays_out_grid_in_row_major_order():
    p = pipeline()
    specs = p.plan_tiles(100, 60)
    assert len(specs) == 12
    assert [spec.tile_id for spec in specs] == list(range(12))
    assert (specs[0].x, specs[0].y) == (0, 0)
    assert (specs[0].width, specs[0].height) == (32, 20)
    assert (specs[3].x, specs[3].y) == (96, 0)
    assert (specs[3].width, specs[3].height) == (4, 20)
    assert (specs[11].x, specs[11].y) == (96, 40)


def test_plan_tiles_expands_read_extent_by_accumulated_halo():
    p = pipeline()
    p.register_stage("decode", lambda spec: spec, halo=2)
    p.register_stage("denoise", lambda spec: spec, halo=3, edge_mode="reflect")
    specs = p.plan_tiles(160, 120)
    assert len(specs) == 30
    inner = specs[7]
    assert (inner.x, inner.y) == (64, 20)
    assert inner.halo == 5
    assert (inner.read_x, inner.read_y) == (59, 15)
    assert (inner.read_width, inner.read_height) == (42, 30)


def test_plan_tiles_rejects_non_positive_size():
    p = pipeline()
    for width, height in [(0, 10), (10, 0), (-32, 10)]:
        with pytest.raises(tp.TilePipelineError) as exc:
            p.plan_tiles(width, height)
        assert exc.value.code == "TILE_INVALID_SPEC"


def test_plan_tiles_enforces_tile_budget():
    p = tp.TileImagePipeline(16, 16, max_tiles=4)
    with pytest.raises(tp.TilePipelineError) as exc:
        p.plan_tiles(64, 64)
    assert exc.value.code == "TILE_LIMIT_EXCEEDED"


def test_register_stage_assigns_sequence_numbers():
    p = pipeline()
    assert p.register_stage("decode", lambda spec: spec, halo=0) == 1
    assert p.register_stage("denoise", lambda spec: spec, halo=1) == 2
    assert p.register_stage("sharpen", lambda spec: spec, halo=1,
                            edge_mode="zero", cost_per_pixel=2) == 3
    assert p.stages() == ("decode", "denoise", "sharpen")


def test_register_stage_rejects_bad_configuration():
    p = pipeline()
    bad_calls = [
        dict(name="", func=lambda spec: spec, halo=0),
        dict(name="decode", func=None, halo=0),
        dict(name="decode", func=lambda spec: spec, halo=-1),
        dict(name="decode", func=lambda spec: spec, halo=0, edge_mode="mirror"),
        dict(name="decode", func=lambda spec: spec, halo=0, cost_per_pixel=-2),
    ]
    for kwargs in bad_calls:
        with pytest.raises(tp.TilePipelineError) as exc:
            p.register_stage(**kwargs)
        assert exc.value.code == "TILE_STAGE_INVALID"
    assert p.stages() == ()


def test_run_tile_unknown_id_raises():
    p = pipeline()
    p.enqueue(64, 40)
    with pytest.raises(tp.TilePipelineError) as exc:
        p.run_tile(99)
    assert exc.value.code == "TILE_UNKNOWN_TILE"


def test_run_tile_returns_stable_digest_per_tile():
    p = pipeline()
    p.register_stage("decode", lambda spec: spec, halo=1)
    p.register_stage("sharpen", lambda spec: spec, halo=1)
    p.enqueue(64, 40)
    first = p.run_tile(0)
    second = p.run_tile(1)
    assert (first.tile_id, second.tile_id) == (0, 1)
    assert len(first.stage_digests) == 2
    assert first.digest != second.digest
    assert p.tile_result(0).digest == first.digest
    assert p.run_tile(0).digest == first.digest
    assert p.stats()["computed"] == 2


def test_run_tile_rejects_oversized_buffer():
    p = tp.TileImagePipeline(32, 20, max_buffer_bytes=1000)
    p.enqueue(64, 40)
    with pytest.raises(tp.TilePipelineError) as exc:
        p.run_tile(0)
    assert exc.value.code == "TILE_BUFFER_EXCEEDED"
    assert p.stats()["computed"] == 0


def test_drain_processes_queue_in_order_and_caps_inflight():
    p = tp.TileImagePipeline(32, 20, max_inflight=3)
    p.register_stage("decode", lambda spec: spec, halo=0)
    assert p.enqueue(100, 60) == 12
    results = p.drain()
    assert [result.tile_id for result in results] == list(range(12))
    stats = p.stats()
    assert stats["pending"] == 0
    assert stats["computed"] == 12
    assert stats["peak_inflight"] == 3


def test_enqueue_is_idempotent_for_same_image():
    p = pipeline()
    assert p.enqueue(100, 60) == 12
    assert p.enqueue(100, 60) == 0
    assert p.stats()["pending"] == 12
    assert p.stats()["planned"] == 12


def test_enqueue_rejects_conflicting_size():
    p = pipeline()
    p.enqueue(100, 60)
    with pytest.raises(tp.TilePipelineError) as exc:
        p.enqueue(96, 60)
    assert exc.value.code == "TILE_INVALID_SPEC"
    assert p.stats()["pending"] == 12


def test_snapshot_is_detached_and_restore_resumes_pending():
    p = pipeline()
    p.register_stage("decode", lambda spec: spec, halo=1)
    p.enqueue(100, 60)
    for tile_id in (0, 1, 2):
        p.run_tile(tile_id)
    state = p.snapshot()
    assert set(state) == {"version", "stages", "specs", "pending",
                          "results", "counters"}
    pristine = copy.deepcopy(state)
    state["pending"].clear()
    state["counters"]["computed"] = 999
    assert p.stats()["computed"] == 3
    assert p.resume() == 9
    clone = pipeline()
    clone.restore(pristine)
    assert clone.resume() == 9
    assert clone.stats()["computed"] == 3
    assert clone.tile_result(2).digest == p.tile_result(2).digest


def test_restore_rejects_malformed_state():
    p = pipeline()
    p.enqueue(64, 40)
    p.run_tile(0)
    bad_states = [
        None,
        {},
        {"version": 2, "stages": [], "specs": [], "pending": [],
         "results": {}, "counters": {}},
        {"version": 1, "stages": "nope", "specs": [], "pending": [],
         "results": {}, "counters": {}},
        {"version": 1, "stages": [], "specs": [], "pending": "nope",
         "results": {}, "counters": {}},
        {"version": 1, "stages": [], "specs": [], "pending": [],
         "results": {}, "counters": {"computed": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(tp.TilePipelineError) as exc:
            p.restore(bad)
        assert exc.value.code == "TILE_SNAPSHOT_INVALID"
    assert p.stats()["computed"] == 1
    assert p.stats()["pending"] == 3


def test_stats_track_each_transition():
    p = pipeline()
    p.register_stage("decode", lambda spec: spec, halo=0)
    p.enqueue(64, 40)
    p.drain()
    assert p.stats() == {
        "stages": 1,
        "planned": 4,
        "computed": 4,
        "pending": 0,
        "peak_inflight": 4,
        "buffer_peak": 10240,
        "cancelled": 0,
    }
