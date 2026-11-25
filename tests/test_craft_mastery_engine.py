import copy
import threading

import pytest

import craft_mastery_engine as cm

NOW = "2026-11-25T10:00:00+00:00"
MIDDAY = "2026-11-25T12:00:00+00:00"


def engine(**kwargs):
    return cm.CraftMasteryEngine(**kwargs)


def add_recipe(eng, recipe_id="iron-sword", materials=None, base_exp=10,
               bonus_item="shard"):
    if materials is None:
        materials = {"ore": 2}
    eng.register_recipe(recipe_id, materials=materials, base_exp=base_exp,
                        bonus_item=bonus_item)


def test_register_recipe_rejects_bad_specs():
    eng = engine()
    bad = [
        ("", dict(materials={"ore": 1}, base_exp=10)),
        ("x", dict(materials={}, base_exp=10)),
        ("x", dict(materials={"ore": -1}, base_exp=10)),
        ("x", dict(materials={"ore": 1}, base_exp=0)),
        ("x", dict(materials={"ore": 1}, base_exp=10, bonus_item="")),
    ]
    for recipe_id, spec in bad:
        with pytest.raises(cm.CraftError) as exc:
            eng.register_recipe(recipe_id, **spec)
        assert exc.value.code == "CRAFT_INVALID_RECIPE"


def test_single_craft_consumes_materials_and_gains_exp():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 5)
    result = eng.craft_batch(player="p1", recipe_id="iron-sword", count=1,
                             now=NOW, crit_rolls=[0.9])
    assert result.crafted == 1
    assert result.failed == 0
    assert result.exp_gained == 10
    assert result.bonuses == {}
    assert result.capped is False
    assert eng.inventory_of("p1") == {"ore": 3}
    assert eng.exp_of("p1") == 10
    assert eng.level_of(player="p1", now=NOW) == 0
    assert eng.daily_exp_used(player="p1", now=NOW) == 10


def test_batch_craft_scales_materials_and_exp():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 10)
    result = eng.craft_batch(player="p1", recipe_id="iron-sword", count=3,
                             now=NOW, crit_rolls=[0.9, 0.9, 0.9])
    assert result.crafted == 3
    assert result.failed == 0
    assert result.exp_gained == 30
    assert eng.inventory_of("p1") == {"ore": 4}
    assert eng.stats()["crafted"] == 3


def test_exp_per_piece_drops_after_level_up():
    eng = engine()
    add_recipe(eng, recipe_id="gear", materials={"ore": 1}, base_exp=40,
               bonus_item=None)
    assert eng.grant_exp(player="p1", amount=100, now=NOW) == 100
    assert eng.level_of(player="p1", now=NOW) == 1
    eng.add_item("p1", "ore", 10)
    result = eng.craft_batch(player="p1", recipe_id="gear", count=2, now=NOW,
                             crit_rolls=[0.9, 0.9])
    assert result.exp_gained == 40
    assert eng.exp_of("p1") == 140
    assert eng.level_of(player="p1", now=NOW) == 1


def test_crit_rolls_grant_bonus_items():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 6)
    result = eng.craft_batch(player="p1", recipe_id="iron-sword", count=3,
                             now=NOW, crit_rolls=[0.01, 0.9, 0.02])
    assert result.bonuses == {"shard": 2}
    assert eng.bonus_output("p1") == {"shard": 2}
    assert result.mailed == 0
    assert eng.stats()["crits"] == 2


def test_crit_rolls_length_must_match_count():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 6)
    with pytest.raises(cm.CraftError) as exc:
        eng.craft_batch(player="p1", recipe_id="iron-sword", count=2, now=NOW,
                        crit_rolls=[0.1])
    assert exc.value.code == "CRAFT_INVALID_ROLLS"
    assert eng.inventory_of("p1") == {"ore": 6}
    assert eng.stats()["crafted"] == 0


def test_double_exp_consumes_one_token():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 2)
    eng.add_item("p1", "double_exp_token", 2)
    result = eng.craft_batch(player="p1", recipe_id="iron-sword", count=1,
                             now=NOW, double_exp=True, crit_rolls=[0.9])
    assert result.exp_gained == 20
    assert eng.inventory_of("p1")["double_exp_token"] == 1

    eng.add_item("p3", "ore", 2)
    with pytest.raises(cm.CraftError) as exc:
        eng.craft_batch(player="p3", recipe_id="iron-sword", count=1,
                        now=NOW, double_exp=True)
    assert exc.value.code == "CRAFT_NO_DOUBLE_TOKEN"


def test_daily_cap_truncates_batch_exp():
    eng = engine(daily_exp_cap=30)
    add_recipe(eng)
    assert eng.grant_exp(player="p1", amount=25, now=NOW) == 25
    eng.add_item("p1", "ore", 2)
    result = eng.craft_batch(player="p1", recipe_id="iron-sword", count=1,
                             now=NOW, crit_rolls=[0.9])
    assert result.exp_gained == 5
    assert result.capped is True
    assert eng.exp_of("p1") == 30
    assert eng.daily_exp_used(player="p1", now=NOW) == 30


def test_grant_exp_stops_at_daily_cap():
    eng = engine(daily_exp_cap=50)
    assert eng.grant_exp(player="p1", amount=30, now=NOW) == 30
    assert eng.grant_exp(player="p1", amount=40, now=MIDDAY) == 20
    assert eng.grant_exp(player="p1", amount=1, now=MIDDAY) == 0
    assert eng.exp_of("p1") == 50


def test_level_and_crit_rate_scale_with_exp():
    eng = engine()
    assert eng.grant_exp(player="p1", amount=1000, now=NOW) == 1000
    assert eng.level_of(player="p1", now=NOW) == 10
    assert eng.crit_rate(player="p1", now=NOW) == pytest.approx(0.15)


def test_unknown_recipe_and_bad_count_are_rejected():
    eng = engine()
    with pytest.raises(cm.CraftError) as exc:
        eng.craft_batch(player="p1", recipe_id="missing", count=1, now=NOW)
    assert exc.value.code == "CRAFT_UNKNOWN_RECIPE"
    add_recipe(eng)
    for count in (0, -2):
        with pytest.raises(cm.CraftError) as exc:
            eng.craft_batch(player="p1", recipe_id="iron-sword", count=count,
                            now=NOW)
        assert exc.value.code == "CRAFT_INVALID_REQUEST"


def test_concurrent_exp_grants_do_not_lose_points():
    eng = engine(daily_exp_cap=100000)
    errors = []
    barrier = threading.Barrier(20)

    def worker():
        try:
            barrier.wait(timeout=5)
            assert eng.grant_exp(player="p1", amount=50, now=NOW) == 50
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert eng.exp_of("p1") == 1000
    assert eng.stats()["exp_granted"] == 1000


def test_snapshot_is_detached_and_restore_roundtrips():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 4)
    eng.craft_batch(player="p1", recipe_id="iron-sword", count=1, now=NOW,
                    crit_rolls=[0.9])
    state = eng.snapshot()
    assert set(state) == {"version", "recipes", "players", "inventories",
                          "daily", "stats", "mail"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["inventories"]["p1"]["ore"] = 999
    state["players"].clear()
    assert eng.inventory_of("p1")["ore"] == 2
    assert eng.exp_of("p1") == 10

    clone = engine()
    clone.restore(pristine)
    assert clone.exp_of("p1") == 10
    assert clone.inventory_of("p1")["ore"] == 2
    assert clone.stats()["crafted"] == 1
    assert clone.craft_batch(player="p1", recipe_id="iron-sword", count=1,
                             now=NOW, crit_rolls=[0.9]).crafted == 1


def test_restore_rejects_malformed_state_without_touching_data():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 2)
    eng.grant_exp(player="p1", amount=10, now=NOW)
    good = eng.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, recipes=[]),
        {key: value for key, value in good.items() if key != "mail"},
    ]
    for bad in bad_states:
        with pytest.raises(cm.CraftError) as exc:
            eng.restore(bad)
        assert exc.value.code == "CRAFT_SNAPSHOT_INVALID"
    assert eng.exp_of("p1") == 10
    assert eng.inventory_of("p1") == {"ore": 2}


def test_stats_count_crafted_crits_and_exp():
    eng = engine()
    add_recipe(eng)
    eng.add_item("p1", "ore", 4)
    eng.craft_batch(player="p1", recipe_id="iron-sword", count=2, now=NOW,
                    crit_rolls=[0.01, 0.9])
    assert eng.stats() == {"crafted": 2, "crits": 1, "exp_granted": 20,
                           "mailed": 0, "claimed": 0}
