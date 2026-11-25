import copy

import pytest

import scim_group_patch_reconciler as scim


def reconciler(**kwargs):
    return scim.ScimGroupPatchReconciler(**kwargs)


def add(owner, group_id, values):
    return owner.apply_patch(
        group_id=group_id,
        operations=[{"op": "add", "path": "members", "value": values}])


def remove(owner, group_id, value):
    return owner.apply_patch(
        group_id=group_id,
        operations=[{"op": "remove", "path": "members", "value": value}])


def test_create_group_starts_at_version_one():
    owner = reconciler()
    view = owner.create_group("eng")
    assert view.group_id == "eng"
    assert view.version == 1
    assert view.members == ()
    assert view.nested == ()
    with pytest.raises(scim.ScimPatchError) as exc:
        owner.create_group("eng")
    assert exc.value.code == "SCIM_GROUP_DUPLICATE"
    assert owner.get_group("missing") is None


def test_add_dedupes_members_and_noop_keeps_version():
    owner = reconciler()
    owner.create_group("eng")
    result = add(owner, "eng", ["u2", "u1", "u1"])
    assert result.version == 2
    assert result.changed is True
    assert result.added == ("u1", "u2")
    assert result.removed == ()
    assert owner.list_members("eng") == ("u1", "u2")

    again = add(owner, "eng", ["u1"])
    assert again.version == 2
    assert again.changed is False
    assert again.added == ()
    assert again.removed == ()


def test_remove_by_ids_and_by_type_filter():
    owner = reconciler()
    owner.create_group("eng")
    owner.create_group("platform")
    add(owner, "eng", ["u1", "u2"])
    add(owner, "eng", [{"value": "platform", "type": "group"}])
    assert owner.get_group("eng").nested == ("platform",)

    by_id = remove(owner, "eng", ["u1"])
    assert by_id.version == 4
    assert by_id.removed == ("u1",)
    assert by_id.added == ()

    by_filter = remove(owner, "eng", {"filter": {"type": "group"}})
    assert by_filter.version == 5
    assert by_filter.removed == ("platform",)
    assert owner.list_members("eng") == ("u2",)
    assert owner.get_group("eng").nested == ()


def test_replace_swaps_the_whole_member_set():
    owner = reconciler()
    owner.create_group("eng")
    add(owner, "eng", ["u1", "u2"])
    result = owner.apply_patch(
        group_id="eng",
        operations=[{"op": "replace", "path": "members",
                     "value": ["u2", "u3"]}])
    assert result.changed is True
    assert result.version == 3
    assert result.added == ("u3",)
    assert result.removed == ("u1",)
    assert owner.list_members("eng") == ("u2", "u3")


def test_invalid_patch_is_atomic():
    owner = reconciler()
    owner.create_group("eng")
    add(owner, "eng", ["u1"])
    bad_operations = [
        [{"op": "move", "path": "members", "value": ["u2"]}],
        [{"op": "add", "path": "displayName", "value": ["u2"]}],
        [{"op": "add", "path": "members", "value": "u2"}],
        [{"op": "remove", "path": "members",
          "value": {"filter": {"type": "role"}}}],
        [{"op": "add", "path": "members",
          "value": [{"value": "u3", "type": "role"}]}],
    ]
    for operations in bad_operations:
        with pytest.raises(scim.ScimPatchError) as exc:
            owner.apply_patch(group_id="eng", operations=operations)
        assert exc.value.code == "SCIM_INVALID_PATCH"
    assert owner.get_group("eng").version == 2
    assert owner.list_members("eng") == ("u1",)


def test_patch_against_unknown_group_is_rejected():
    owner = reconciler()
    with pytest.raises(scim.ScimPatchError) as exc:
        add(owner, "ghost", ["u1"])
    assert exc.value.code == "SCIM_GROUP_NOT_FOUND"


def test_member_cap_is_enforced_without_partial_add():
    owner = reconciler(max_members=3)
    owner.create_group("eng")
    with pytest.raises(scim.ScimPatchError) as exc:
        add(owner, "eng", ["u1", "u2", "u3", "u4"])
    assert exc.value.code == "SCIM_MEMBER_LIMIT"
    assert owner.get_group("eng").version == 1
    assert owner.list_members("eng") == ()

    add(owner, "eng", ["u1", "u2", "u3"])
    assert owner.get_group("eng").version == 2


def test_nesting_depth_limit_is_enforced():
    owner = reconciler(max_depth=2)
    for name in ("g1", "g2", "g3"):
        owner.create_group(name)
    add(owner, "g1", [{"value": "g2", "type": "group"}])
    assert owner.get_group("g1").version == 2

    with pytest.raises(scim.ScimPatchError) as exc:
        add(owner, "g2", [{"value": "g3", "type": "group"}])
    assert exc.value.code == "SCIM_NESTING_DEPTH"
    assert owner.get_group("g2").version == 1
    assert owner.list_members("g2") == ()
    assert owner.get_group("g1").members == ("g2",)


def test_expand_walks_nested_groups():
    owner = reconciler()
    owner.create_group("g1")
    owner.create_group("g2")
    add(owner, "g2", ["u2", "u3"])
    add(owner, "g1", ["u1", {"value": "g2", "type": "group"}])
    assert owner.list_members("g1") == ("g2", "u1")
    assert owner.list_members("g1", expand=True) == ("u1", "u2", "u3")
    assert owner.list_members("g2", expand=True) == ("u2", "u3")


def test_reconcile_guards_reject_duplicate_and_bad_pages():
    owner = reconciler()
    owner.create_group("eng")
    session = owner.begin_reconcile("eng")
    assert session.token == "rec-0001"
    assert session.group_id == "eng"
    assert session.start_version == 1
    assert session.pages == ()

    with pytest.raises(scim.ScimPatchError) as exc:
        owner.begin_reconcile("eng")
    assert exc.value.code == "SCIM_RECONCILE_ACTIVE"

    with pytest.raises(scim.ScimPatchError) as exc:
        owner.reconcile_page(token="rec-9999", page=0, members=["u1"])
    assert exc.value.code == "SCIM_RECONCILE_NOT_FOUND"

    with pytest.raises(scim.ScimPatchError) as exc:
        owner.reconcile_page(token=session.token, page=1, members=["u1"])
    assert exc.value.code == "SCIM_PAGE_OUT_OF_ORDER"

    with pytest.raises(scim.ScimPatchError) as exc:
        owner.reconcile_page(token=session.token, page=0, members=["  "])
    assert exc.value.code == "SCIM_INVALID_PAGE"


def test_reconcile_converges_to_source_pages():
    owner = reconciler()
    owner.create_group("eng")
    add(owner, "eng", ["u1"])
    session = owner.begin_reconcile("eng")
    assert owner.reconcile_page(token=session.token, page=0,
                                members=["u1", "u2"]) == 0
    assert owner.reconcile_page(token=session.token, page=1,
                                members=["u2", "u3"]) == 1

    result = owner.finish_reconcile(session.token)
    assert result.group_id == "eng"
    assert result.version == 3
    assert result.added == ("u2", "u3")
    assert result.removed == ()
    assert result.pages == 2
    assert owner.list_members("eng") == ("u1", "u2", "u3")
    assert owner.stats()["reconciled"] == 1


def test_reconcile_replays_local_patches_instead_of_overwriting():
    owner = reconciler()
    owner.create_group("eng")
    add(owner, "eng", ["u9"])
    session = owner.begin_reconcile("eng")
    add(owner, "eng", ["x"])
    assert owner.get_group("eng").version == 3

    owner.reconcile_page(token=session.token, page=0, members=["u1", "u2"])
    result = owner.finish_reconcile(session.token)
    assert result.version == 4
    assert result.added == ("u1", "u2")
    assert result.removed == ("u9",)
    assert owner.list_members("eng") == ("u1", "u2", "x")


def test_snapshot_is_detached_and_restore_roundtrips():
    owner = reconciler()
    owner.create_group("eng")
    owner.create_group("platform")
    add(owner, "platform", ["u2"])
    add(owner, "eng", ["u1", {"value": "platform", "type": "group"}])
    state = owner.snapshot()
    assert set(state) == {"version", "groups", "stats"}
    pristine = copy.deepcopy(state)

    state["groups"].clear()
    state["stats"]["patches"] = 99
    assert owner.get_group("eng").version == 2
    assert owner.stats()["patches"] == 2

    clone = reconciler()
    clone.restore(pristine)
    restored = clone.get_group("eng")
    assert restored.version == 2
    assert restored.members == ("platform", "u1")
    assert clone.list_members("eng", expand=True) == ("u1", "u2")
    assert clone.stats()["patches"] == 2


def test_stats_track_patches_and_reconcile_exactly():
    owner = reconciler()
    owner.create_group("g1")
    owner.create_group("g2")
    add(owner, "g1", ["u1"])
    add(owner, "g1", [{"value": "g2", "type": "group"}])
    remove(owner, "g1", ["u1"])
    add(owner, "g1", [{"value": "g2", "type": "group"}])

    session = owner.begin_reconcile("g1")
    owner.reconcile_page(token=session.token, page=0, members=["u1", "u2"])
    owner.finish_reconcile(session.token)

    assert owner.stats() == {"groups": 2, "patches": 4, "changed": 3,
                             "reconciled": 1, "reconcile_pages": 1,
                             "members": 3, "nested_links": 1}


def test_two_groups_can_reconcile_independently():
    owner = reconciler()
    owner.create_group("a")
    owner.create_group("b")
    first = owner.begin_reconcile("a")
    second = owner.begin_reconcile("b")
    assert first.token != second.token

    owner.reconcile_page(token=first.token, page=0, members=["u1"])
    assert owner.finish_reconcile(first.token).version == 2

    owner.reconcile_page(token=second.token, page=0, members=["u2"])
    result = owner.finish_reconcile(second.token)
    assert result.version == 2
    assert owner.list_members("b") == ("u2",)
