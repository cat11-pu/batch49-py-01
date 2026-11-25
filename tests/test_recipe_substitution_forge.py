import pytest

import recipe_substitution_forge as rsf


def make_forge():
    return rsf.RecipeSubstitutionForge()


def test_duplicate_recipe_rejected():
    forge = make_forge()
    forge.register_recipe("blade", requirements={"ore": 4})
    with pytest.raises(rsf.ForgeError) as err:
        forge.register_recipe("blade", requirements={"ore": 2})
    assert err.value.code == "FORGE_DUPLICATE_RECIPE"


def test_empty_or_nonpositive_requirements_rejected():
    forge = make_forge()
    with pytest.raises(rsf.ForgeError) as err:
        forge.register_recipe("blade", requirements={})
    assert err.value.code == "FORGE_INVALID_REQUEST"
    with pytest.raises(rsf.ForgeError):
        forge.register_recipe("blade", requirements={"ore": 0})


def test_add_material_validates_quantity_and_conflicts():
    forge = make_forge()
    with pytest.raises(rsf.ForgeError) as err:
        forge.add_material("ore", category="ore", quality=3, qty=0)
    assert err.value.code == "FORGE_INVALID_REQUEST"
    forge.add_material("ore", category="ore", quality=3, qty=5)
    with pytest.raises(rsf.ForgeError) as err:
        forge.add_material("ore", category="ore", quality=4, qty=1)
    assert err.value.code == "FORGE_MATERIAL_CONFLICT"


def test_inventory_and_available_report_registered_stock():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=5)
    assert forge.inventory_of("ore") == 5
    assert forge.available_of("ore") == 5
    assert forge.inventory_of("ghost") == 0


def test_preview_exact_category_usage_without_side_effects():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    forge.register_recipe("blade", requirements={"ore": 4})
    assert forge.preview("blade") == {
        "feasible": True,
        "usage": {"ore": 4},
        "missing": {},
    }
    assert forge.inventory_of("ore") == 10
    assert forge.available_of("ore") == 10


def test_preview_consumes_lowest_quality_material_first():
    forge = make_forge()
    forge.add_material("ore_high", category="ore", quality=4, qty=5)
    forge.add_material("ore_low", category="ore", quality=1, qty=2)
    forge.register_recipe("blade", requirements={"ore": 4})
    assert forge.preview("blade")["usage"] == {"ore_low": 2, "ore_high": 2}


def test_preview_uses_substitute_material_for_remaining_units():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=4, qty=2)
    forge.add_material("scrap", category="scrap", quality=1, qty=10)
    forge.register_recipe(
        "blade",
        requirements={"ore": 4},
        substitutes={"scrap": {"category": "ore", "num": 1, "den": 2}},
    )
    result = forge.preview("blade")
    assert result["feasible"] is True
    assert result["usage"] == {"ore": 2, "scrap": 4}
    assert result["missing"] == {}


def test_preview_reports_missing_categories_when_short():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=4, qty=1)
    forge.register_recipe("blade", requirements={"ore": 4})
    result = forge.preview("blade")
    assert result["feasible"] is False
    assert result["usage"] == {}
    assert result["missing"] == {"ore": 3}


def test_reserve_holds_materials_without_consuming_them():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    forge.register_recipe("blade", requirements={"ore": 4})
    result = forge.reserve("blade")
    assert result["reservation"] == 1
    assert result["usage"] == {"ore": 4}
    assert forge.inventory_of("ore") == 10
    assert forge.available_of("ore") == 6


def test_craft_consumes_materials_and_records_quality():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    forge.register_recipe("blade", requirements={"ore": 4})
    result = forge.craft("blade")
    assert result["item_id"] == "item-1"
    assert result["quality"] == 3
    assert result["usage"] == {"ore": 4}
    assert forge.inventory_of("ore") == 6
    assert forge.quality_of("item-1") == 3


def test_craft_with_insufficient_materials_changes_nothing():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=2)
    forge.register_recipe("blade", requirements={"ore": 4})
    with pytest.raises(rsf.ForgeError) as err:
        forge.craft("blade")
    assert err.value.code == "FORGE_INSUFFICIENT_MATERIALS"
    assert forge.inventory_of("ore") == 2
    assert forge.available_of("ore") == 2


def test_craft_consumes_existing_reservation():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    forge.register_recipe("blade", requirements={"ore": 4})
    forge.reserve("blade")
    result = forge.craft("blade", reservation=1)
    assert result["item_id"] == "item-1"
    assert forge.inventory_of("ore") == 6
    assert forge.available_of("ore") == 6
    with pytest.raises(rsf.ForgeError) as err:
        forge.release(1)
    assert err.value.code == "FORGE_UNKNOWN_RESERVATION"


def test_release_returns_reserved_materials_and_clears_hold():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    forge.register_recipe("blade", requirements={"ore": 4})
    forge.reserve("blade")
    result = forge.release(1)
    assert result["released"] == {"ore": 4}
    assert forge.available_of("ore") == 10
    with pytest.raises(rsf.ForgeError) as err:
        forge.release(1)
    assert err.value.code == "FORGE_UNKNOWN_RESERVATION"


def test_snapshot_restore_returns_to_captured_forge_state():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    forge.register_recipe("blade", requirements={"ore": 4})
    snapshot = forge.snapshot()
    forge.craft("blade")
    assert forge.inventory_of("ore") == 6
    assert forge.quality_of("item-1") == 3
    forge.restore(snapshot)
    assert forge.inventory_of("ore") == 10
    assert forge.available_of("ore") == 10
    with pytest.raises(rsf.ForgeError) as err:
        forge.quality_of("item-1")
    assert err.value.code == "FORGE_UNKNOWN_ITEM"


def test_invalid_snapshot_restore_leaves_forge_untouched():
    forge = make_forge()
    forge.add_material("ore", category="ore", quality=3, qty=10)
    with pytest.raises(rsf.ForgeError) as err:
        forge.restore({"materials": []})
    assert err.value.code == "FORGE_SNAPSHOT_INVALID"
    assert forge.inventory_of("ore") == 10
