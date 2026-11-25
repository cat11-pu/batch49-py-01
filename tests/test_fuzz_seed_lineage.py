import pytest

import fuzz_seed_lineage as fl


def lineage(**kwargs):
    return fl.FuzzSeedLineage(**kwargs)


def chain(inst, length=3):
    inst.register_root("s-1", b"root-seed")
    for index in range(2, length + 1):
        inst.mutate(f"s-{index - 1}", f"s-{index}", f"body-{index}".encode(),
                    "bitflip")
    return inst


def test_register_root_creates_depth_zero_node():
    inst = lineage()
    assert inst.register_root("s-1", b"seed-one") == "s-1"
    node = inst.node("s-1")
    assert node.node_id == "s-1"
    assert node.parent_id is None
    assert node.depth == 0
    assert node.deleted is False
    assert node.mutation == "root"
    assert node.digest != ""
    assert inst.roots() == ("s-1",)


def test_register_root_rejects_duplicate_and_invalid():
    inst = lineage()
    inst.register_root("s-1", b"aaa")
    with pytest.raises(fl.LineageError) as exc:
        inst.register_root("s-1", b"bbb")
    assert exc.value.code == "LINEAGE_DUPLICATE"
    with pytest.raises(fl.LineageError) as exc:
        inst.register_root("   ", b"bbb")
    assert exc.value.code == "LINEAGE_INVALID"
    with pytest.raises(fl.LineageError) as exc:
        inst.register_root("s-2", b"")
    assert exc.value.code == "LINEAGE_INVALID"


def test_mutate_links_parent_and_depth():
    inst = lineage()
    inst.register_root("s-1", b"root-seed")
    assert inst.mutate("s-1", "s-2", b"child", "bitflip") == "added"
    child = inst.node("s-2")
    assert child.parent_id == "s-1"
    assert child.depth == 1
    assert child.mutation == "bitflip"
    assert inst.mutate("s-2", "s-3", b"grand", "splice") == "added"
    assert inst.node("s-3").depth == 2


def test_mutate_unknown_parent_raises():
    inst = lineage()
    with pytest.raises(fl.LineageError) as exc:
        inst.mutate("s-404", "s-2", b"child", "bitflip")
    assert exc.value.code == "LINEAGE_UNKNOWN"
    assert inst.stats()["nodes"] == 0


def test_ancestors_returns_root_first_chain():
    inst = chain(lineage(), 3)
    assert inst.ancestors("s-3") == ("s-1", "s-2")
    assert inst.ancestors("s-1") == ()
    assert inst.ancestors("s-2") == ("s-1",)


def test_descendants_returns_all_children_sorted():
    inst = lineage()
    inst.register_root("s-1", b"root-seed")
    inst.mutate("s-1", "s-4", b"child-d", "bitflip")
    inst.mutate("s-1", "s-2", b"child-b", "splice")
    inst.mutate("s-2", "s-3", b"child-c", "havoc")
    assert inst.descendants("s-1") == ("s-2", "s-3", "s-4")
    assert inst.descendants("s-3") == ()


def test_roots_lists_all_registered_roots_sorted():
    inst = lineage()
    inst.register_root("s-9", b"seed-nine")
    inst.register_root("s-1", b"seed-one")
    assert inst.roots() == ("s-1", "s-9")
    assert inst.stats()["roots"] == 2


def test_delete_marks_node_and_second_delete_false():
    inst = chain(lineage(), 3)
    assert inst.delete("s-2") is True
    assert inst.node("s-2").deleted is True
    assert inst.delete("s-2") is False
    assert inst.delete("s-404") is False
    assert inst.node("s-2").parent_id == "s-1"


def test_depth_limit_raises():
    inst = lineage(max_depth=2)
    inst.register_root("s-1", b"root-seed")
    inst.mutate("s-1", "s-2", b"child", "bitflip")
    inst.mutate("s-2", "s-3", b"grand", "bitflip")
    with pytest.raises(fl.LineageError) as exc:
        inst.mutate("s-3", "s-4", b"great", "bitflip")
    assert exc.value.code == "LINEAGE_DEPTH_EXCEEDED"
    assert inst.node("s-3").depth == 2


def test_truncated_children_empty_below_fanout():
    inst = lineage(max_children=2)
    inst.register_root("s-1", b"root-seed")
    inst.mutate("s-1", "s-2", b"child-b", "bitflip")
    inst.mutate("s-1", "s-3", b"child-c", "bitflip")
    assert inst.truncated_children("s-1") == ()
    assert inst.descendants("s-1") == ("s-2", "s-3")


def test_mutate_rejects_duplicate_child_id():
    inst = lineage()
    inst.register_root("s-1", b"root-seed")
    inst.mutate("s-1", "s-2", b"child", "bitflip")
    with pytest.raises(fl.LineageError) as exc:
        inst.mutate("s-1", "s-2", b"other", "splice")
    assert exc.value.code == "LINEAGE_DUPLICATE"
    assert inst.node("s-2").mutation == "bitflip"


def test_lineage_for_includes_self_last():
    inst = chain(lineage(), 3)
    assert inst.lineage_for("s-3") == ("s-1", "s-2", "s-3")
    assert inst.lineage_for("s-1") == ("s-1",)


def test_snapshot_restore_roundtrip():
    inst = chain(lineage(), 3)
    inst.delete("s-2")
    state = inst.snapshot()
    clone = lineage()
    clone.restore(state)
    assert clone.roots() == inst.roots()
    assert clone.ancestors("s-3") == inst.ancestors("s-3")
    assert clone.descendants("s-1") == inst.descendants("s-1")
    assert clone.node("s-2").deleted is True
    assert clone.stats() == inst.stats()


def test_merge_deduplicates_nodes():
    left = lineage()
    left.register_root("s-1", b"root-seed")
    left.mutate("s-1", "s-2", b"child", "bitflip")
    right = lineage()
    right.register_root("s-1", b"root-seed")
    right.register_root("s-9", b"other-seed")
    assert left.merge(right.snapshot()) == 1
    assert left.stats()["nodes"] == 3
    assert left.roots() == ("s-1", "s-9")
    assert left.merge(right.snapshot()) == 0


def test_stats_counts_nodes_mutations_deleted():
    inst = lineage()
    inst.register_root("s-1", b"root-one")
    inst.register_root("s-2", b"root-two")
    inst.mutate("s-1", "s-3", b"child-c", "bitflip")
    inst.mutate("s-1", "s-4", b"child-d", "splice")
    inst.delete("s-4")
    assert inst.stats() == {"nodes": 4, "roots": 2, "mutations": 2,
                            "deleted": 1}
