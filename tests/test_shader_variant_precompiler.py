import copy
import threading

import pytest

import shader_variant_precompiler as sv


def precompiler(**kwargs):
    return sv.ShaderVariantPrecompiler(**kwargs)


def material(material_id, *keywords):
    return sv.Material(material_id, tuple(keywords))


def test_normalize_keywords_removes_repeats():
    p = precompiler()
    assert p.normalize_keywords(["alpha", "alpha"]) == ("alpha",)
    assert p.normalize_keywords(()) == ()
    assert p.normalize_keywords(("alpha", "beta")) == ("alpha", "beta")


def test_normalize_keywords_rejects_bad_input():
    p = precompiler(max_keywords=3)
    for bad in (["alpha", 7], ["alpha", ""], 5, None):
        with pytest.raises(sv.ShaderVariantError) as exc:
            p.normalize_keywords(bad)
        assert exc.value.code == "SHADER_KEY_INVALID"
    with pytest.raises(sv.ShaderVariantError) as exc:
        p.normalize_keywords(["a", "b", "c", "d"])
    assert exc.value.code == "SHADER_KEY_LIMIT"


def test_plan_from_materials_dedupes_and_orders_variants():
    p = precompiler()
    materials = [material("m1", "a", "b"), material("m2", "a", "b"),
                 material("m3", "c")]
    assert p.plan_from_materials(materials) == ("a|b", "c")
    assert p.plan_from_materials(materials) == ("a|b", "c")
    assert p.variants() == ("a|b", "c")


def test_plan_from_materials_rejects_bad_materials():
    p = precompiler()
    bad_lists = [
        [material("", "a")],
        [material("m1", "a"), material("m1", "c")],
        [sv.Material("m1", ("a", 5))],
        None,
    ]
    for bad in bad_lists:
        with pytest.raises(sv.ShaderVariantError) as exc:
            p.plan_from_materials(bad)
        assert exc.value.code == "SHADER_MATERIAL_INVALID"
    assert p.variants() == ()


def test_plan_from_materials_enforces_keyword_limit():
    p = precompiler(max_keywords=3)
    with pytest.raises(sv.ShaderVariantError) as exc:
        p.plan_from_materials([material("m1", "a", "b", "c", "d")])
    assert exc.value.code == "SHADER_KEY_LIMIT"


def test_plan_from_materials_enforces_total_budget():
    p = precompiler(total_budget=2)
    materials = [material("m1", "a"), material("m2", "b"), material("m3", "c")]
    with pytest.raises(sv.ShaderVariantError) as exc:
        p.plan_from_materials(materials)
    assert exc.value.code == "SHADER_BUDGET_EXCEEDED"
    assert p.variants() == ()
    two = [material("m1", "a"), material("m2", "b")]
    assert p.plan_from_materials(two) == ("a", "b")


def test_next_batch_respects_batch_size_and_plan_order():
    p = precompiler(batch_size=2)
    p.plan_from_materials([material(f"m{i}", key)
                           for i, key in enumerate("abcde")])
    assert p.next_batch() == ("a", "b")
    assert p.next_batch() == ("c", "d")
    assert p.next_batch() == ("e",)
    assert p.next_batch() == ()


def test_next_batch_hands_each_key_out_once_under_concurrency():
    p = precompiler(batch_size=1)
    p.plan_from_materials([material(f"m{i}", key)
                           for i, key in enumerate("abcdef")])
    batches = []
    errors = []
    barrier = threading.Barrier(6)

    def worker():
        try:
            barrier.wait(timeout=5)
            batches.append(p.next_batch())
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    handed = [key for batch in batches for key in batch]
    assert len(handed) == 6
    assert sorted(handed) == ["a", "b", "c", "d", "e", "f"]


def test_next_batch_skips_already_compiled_keys():
    p = precompiler(batch_size=2)
    p.plan_from_materials([material("m1", "a"), material("m2", "b"),
                           material("m3", "c")])
    assert p.next_batch() == ("a", "b")
    p.mark_compiled("a")
    assert p.next_batch() == ("c",)
    assert p.next_batch() == ()


def test_mark_compiled_validates_known_and_queued_keys():
    p = precompiler()
    p.plan_from_materials([material("m1", "a")])
    with pytest.raises(sv.ShaderVariantError) as exc:
        p.mark_compiled("ghost")
    assert exc.value.code == "SHADER_UNKNOWN_KEY"
    with pytest.raises(sv.ShaderVariantError) as exc:
        p.mark_compiled("a")
    assert exc.value.code == "SHADER_KEY_NOT_QUEUED"


def test_mark_compiled_records_failure_and_progress():
    p = precompiler(batch_size=2)
    p.plan_from_materials([material(f"m{i}", key)
                           for i, key in enumerate("abcde")])
    assert p.next_batch() == ("a", "b")
    p.mark_compiled("a", ok=False, error="link error")
    p.mark_compiled("b")
    assert p.progress() == {"planned": 5, "queued": 0, "compiled": 1,
                            "failed": 1, "remaining": 3}
    assert p.errors() == {"a": "link error"}


def test_fallback_for_drops_the_last_keyword():
    p = precompiler()
    assert p.fallback_for("a|b|c") == "a|b"
    assert p.fallback_for("a") == "default"
    with pytest.raises(sv.ShaderVariantError) as exc:
        p.fallback_for("default")
    assert exc.value.code == "SHADER_KEY_INVALID"


def test_progress_counts_are_exact():
    p = precompiler(batch_size=2)
    p.plan_from_materials([material(f"m{i}", key)
                           for i, key in enumerate("abcd")])
    assert p.progress() == {"planned": 4, "queued": 0, "compiled": 0,
                            "failed": 0, "remaining": 4}
    assert p.next_batch() == ("a", "b")
    assert p.progress()["queued"] == 2
    p.mark_compiled("a")
    p.mark_compiled("b", ok=False)
    assert p.progress() == {"planned": 4, "queued": 0, "compiled": 1,
                            "failed": 1, "remaining": 2}


def test_snapshot_is_detached_and_restore_resumes_queue():
    p = precompiler(batch_size=2)
    p.plan_from_materials([material(f"m{i}", key)
                           for i, key in enumerate("abcde")])
    assert p.next_batch() == ("a", "b")
    p.mark_compiled("a")
    state = p.snapshot()
    assert set(state) == {"version", "materials", "variants", "queued",
                          "compiled", "failed", "errors", "counters"}
    pristine = copy.deepcopy(state)
    state["variants"].clear()
    state["counters"]["batches"] = 99
    assert p.variants() == ("a", "b", "c", "d", "e")
    clone = precompiler(batch_size=2)
    clone.restore(pristine)
    assert clone.variants() == ("a", "b", "c", "d", "e")
    assert clone.progress()["compiled"] == 1
    assert clone.next_batch() == ("c", "d")
    clone.mark_compiled("c")
    assert clone.progress()["compiled"] == 2


def test_restore_rejects_malformed_state():
    p = precompiler()
    p.plan_from_materials([material("m1", "a")])
    bad_states = [
        None,
        {},
        {"version": 2, "materials": [], "variants": [], "queued": [],
         "compiled": [], "failed": [], "errors": {}, "counters": {}},
        {"version": 1, "materials": [], "variants": "nope", "queued": [],
         "compiled": [], "failed": [], "errors": {}, "counters": {}},
        {"version": 1, "materials": [], "variants": [], "queued": [],
         "compiled": [], "failed": [], "errors": [], "counters": {}},
        {"version": 1, "materials": [], "variants": [], "queued": [],
         "compiled": [], "failed": [], "errors": {}, "counters": {"batches": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(sv.ShaderVariantError) as exc:
            p.restore(bad)
        assert exc.value.code == "SHADER_SNAPSHOT_INVALID"
    assert p.variants() == ("a",)
    assert p.progress()["planned"] == 1
