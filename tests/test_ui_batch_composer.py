import pytest

import ui_batch_composer as ui

RECT = (0.0, 0.0, 32.0, 16.0)
CLIP_A = (0, 0, 320, 180)
CLIP_B = (400, 400, 480, 500)


def make(**kwargs):
    return ui.UiBatchComposer(**kwargs)


def quad(comp, element_id, *, page="hud", layer=0, rect=RECT,
         clip=CLIP_A, mask=None):
    comp.push_quad(element_id, page=page, layer=layer, rect=rect,
                   clip=clip, mask=mask)


def test_push_quad_validates_arguments():
    a = make()
    with pytest.raises(ui.UiError) as empty_id:
        quad(a, "")
    assert empty_id.value.code == "UI_INVALID_ARG"
    with pytest.raises(ui.UiError) as empty_page:
        quad(a, "q0", page="")
    assert empty_page.value.code == "UI_INVALID_ARG"
    with pytest.raises(ui.UiError) as flat_rect:
        quad(a, "q0", rect=(0.0, 0.0, 0.0, 16.0))
    assert flat_rect.value.code == "UI_INVALID_ARG"
    with pytest.raises(ui.UiError) as negative_layer:
        quad(a, "q0", layer=-1)
    assert negative_layer.value.code == "UI_INVALID_ARG"
    with pytest.raises(ui.UiError) as empty_mask:
        quad(a, "q0", mask="")
    assert empty_mask.value.code == "UI_INVALID_ARG"
    assert a.stats()["pending"] == 0


def test_batches_empty_until_flush():
    a = make()
    assert a.batches() == ()
    quad(a, "q0")
    assert a.batches() == ()
    assert a.stats()["pending"] == 1
    assert len(a.flush()) == 1
    assert a.stats()["pending"] == 0


def test_same_state_quads_merge_into_one_batch():
    a = make()
    for name in ("q0", "q1", "q2"):
        quad(a, name)
    batches = a.flush()
    assert len(batches) == 1
    assert batches[0].elements == ("q0", "q1", "q2")
    assert batches[0].page == "hud"
    assert batches[0].clip == CLIP_A
    assert batches[0].mask is None
    assert a.stats()["quads"] == 3


def test_page_switch_breaks_batch():
    a = make()
    quad(a, "q0")
    quad(a, "q1")
    quad(a, "q2", page="fx")
    batches = a.flush()
    assert [len(b.elements) for b in batches] == [2, 1]
    assert [b.page for b in batches] == ["hud", "fx"]


def test_clip_change_breaks_batch():
    a = make()
    a.push_clip("q0", page="hud", layer=0, rect=RECT, clip=CLIP_A)
    a.push_clip("q1", page="hud", layer=0, rect=RECT, clip=CLIP_A)
    a.push_clip("q2", page="hud", layer=0, rect=RECT, clip=CLIP_B)
    batches = a.flush()
    assert [len(b.elements) for b in batches] == [2, 1]
    assert [b.clip for b in batches] == [CLIP_A, CLIP_B]


def test_mask_change_breaks_batch():
    a = make()
    a.push_mask("q0", page="hud", layer=0, rect=RECT, mask_op="circle")
    a.push_mask("q1", page="hud", layer=0, rect=RECT, mask_op="circle")
    a.push_mask("q2", page="hud", layer=0, rect=RECT, mask_op="rounded")
    batches = a.flush()
    assert [len(b.elements) for b in batches] == [2, 1]
    assert [b.mask for b in batches] == ["circle", "rounded"]


def test_push_mask_and_clip_require_their_arguments():
    a = make()
    with pytest.raises(ui.UiError) as no_mask:
        a.push_mask("q0", page="hud", layer=0, rect=RECT)
    assert no_mask.value.code == "UI_INVALID_ARG"
    with pytest.raises(ui.UiError) as no_clip:
        a.push_clip("q1", page="hud", layer=0, rect=RECT, clip=None)
    assert no_clip.value.code == "UI_INVALID_ARG"
    assert a.stats()["pending"] == 0


def test_layer_order_overrides_submission_order():
    a = make()
    quad(a, "top", layer=2)
    quad(a, "bottom", layer=0)
    quad(a, "middle", layer=1)
    batches = a.flush()
    assert len(batches) == 1
    assert batches[0].elements == ("bottom", "middle", "top")


def test_quad_limit_breaks_before_next_element():
    a = make(max_quads_per_batch=2)
    for name in ("q0", "q1", "q2", "q3", "q4"):
        quad(a, name)
    batches = a.flush()
    assert [len(b.elements) for b in batches] == [2, 2, 1]
    assert [b.batch_id for b in batches] == ["b0", "b1", "b2"]


def test_max_pages_exceeded_commits_nothing():
    a = make(max_pages=1)
    quad(a, "q0", page="hud")
    quad(a, "q1", page="fx")
    with pytest.raises(ui.UiError) as pages:
        a.flush()
    assert pages.value.code == "UI_PAGE_LIMIT"
    assert a.batches() == ()
    assert a.stats()["pending"] == 2
    assert a.stats()["quads"] == 0


def test_max_batches_exceeded_commits_nothing():
    a = make(max_batches=1)
    quad(a, "q0")
    quad(a, "q1")
    quad(a, "q2", page="fx")
    with pytest.raises(ui.UiError) as limit:
        a.flush()
    assert limit.value.code == "UI_BATCH_LIMIT"
    assert a.batches() == ()
    assert a.stats()["pending"] == 3


def test_reorder_changes_layer_order():
    a = make()
    quad(a, "a", layer=0)
    quad(a, "b", layer=1)
    a.reorder("a", 5)
    batches = a.flush()
    assert batches[0].elements == ("b", "a")
    with pytest.raises(ui.UiError) as gone:
        a.reorder("a", 0)
    assert gone.value.code == "UI_UNKNOWN_ELEMENT"


def test_reorder_unknown_element_rejected():
    a = make()
    with pytest.raises(ui.UiError) as unknown:
        a.reorder("ghost", 1)
    assert unknown.value.code == "UI_UNKNOWN_ELEMENT"
    assert a.stats()["pending"] == 0


def test_flush_appends_new_batches_across_calls():
    a = make()
    quad(a, "q0")
    first = a.flush()
    assert [b.batch_id for b in first] == ["b0"]
    quad(a, "q1")
    second = a.flush()
    assert [b.batch_id for b in second] == ["b0", "b1"]
    assert [len(b.elements) for b in second] == [1, 1]
    assert a.stats()["flushes"] == 2
    assert a.stats()["quads"] == 2


def test_snapshot_restore_roundtrip():
    a = make()
    quad(a, "q0")
    quad(a, "q1", page="fx")
    a.flush()
    quad(a, "q2", layer=3)
    state = a.snapshot()
    b = make()
    b.restore(state)
    assert [x.elements for x in b.batches()] == [x.elements for x in a.batches()]
    assert b.stats() == a.stats()
    assert [x.elements for x in b.flush()] == [("q2",)]
    with pytest.raises(ui.UiError) as bad:
        b.restore({"pending": "nope"})
    assert bad.value.code == "UI_SNAPSHOT_INVALID"
    assert b.stats()["pending"] == 0
