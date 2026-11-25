import copy

import pytest

import jit_provisioning_gate as jit


def gate(**kwargs):
    return jit.JitProvisioningGate(**kwargs)


def with_domain(**kwargs):
    owner = gate(**kwargs)
    owner.register_domain(domain="corp.example", tenant="t1")
    return owner


def provision(owner, email="alice@corp.example", attributes=None, now=1000):
    return owner.provision(email=email,
                           attributes={} if attributes is None else attributes,
                           now=now)


def test_register_domain_normalizes_and_resolves_verified_domain():
    owner = gate()
    domain = owner.register_domain(domain="Corp.Example", tenant="t1")
    assert domain.domain == "corp.example"
    assert domain.tenant == "t1"
    assert domain.verified is True
    assert owner.resolve_tenant(email="Alice@corp.example") == "t1"
    assert owner.resolve_tenant(email="bob@other.example") is None
    assert owner.stats()["resolved"] == 1


def test_duplicate_domain_and_blank_fields_are_rejected():
    owner = gate()
    owner.register_domain(domain="corp.example", tenant="t1")
    with pytest.raises(jit.JitProvisionError) as exc:
        owner.register_domain(domain="CORP.EXAMPLE", tenant="t2")
    assert exc.value.code == "JIT_DOMAIN_DUPLICATE"
    for kwargs in (
        dict(domain="  ", tenant="t1"),
        dict(domain="corp.example", tenant="   "),
    ):
        with pytest.raises(jit.JitProvisionError) as exc:
            owner.register_domain(**kwargs)
        assert exc.value.code == "JIT_INVALID_REQUEST"


def test_provision_creates_account_with_mapped_attributes():
    owner = with_domain()
    owner.register_mapping(tenant="t1", target="displayName", source="name",
                           default="unknown", priority=0)
    result = provision(owner, attributes={"name": "Alice"})
    assert result.status == "created"
    assert result.account_id == "acct-0001"
    assert result.tenant == "t1"
    assert result.attributes == {"displayName": "Alice"}
    assert result.sources == {"displayName": "name"}

    account = owner.get_account("acct-0001")
    assert account.email == "alice@corp.example"
    assert account.tenant == "t1"
    assert account.merged_from == ()
    assert owner.find_account(email="ALICE@corp.example").account_id == (
        "acct-0001")
    assert owner.stats()["provisioned"] == 1
    assert owner.stats()["accounts"] == 1


def test_missing_attribute_uses_default_and_records_source():
    owner = with_domain()
    owner.register_mapping(tenant="t1", target="displayName", source="name",
                           default="unknown", priority=0)
    result = provision(owner, attributes={})
    assert result.attributes == {"displayName": "unknown"}
    assert result.sources == {"displayName": "default"}
    assert owner.get_account("acct-0001").sources == {
        "displayName": "default"}


def test_equal_priority_conflicting_sources_are_rejected():
    owner = gate()
    owner.register_mapping(tenant="t1", target="displayName", source="name",
                           priority=0)
    owner.register_mapping(tenant="t1", target="displayName",
                           source="nickname", priority=0)
    with pytest.raises(jit.JitProvisionError) as exc:
        owner.map_attributes(tenant="t1",
                             attributes={"name": "A", "nickname": "B"})
    assert exc.value.code == "JIT_MAPPING_CONFLICT"


def test_higher_priority_mapping_wins():
    owner = gate()
    owner.register_mapping(tenant="t1", target="displayName", source="name",
                           default="unknown", priority=1)
    owner.register_mapping(tenant="t1", target="displayName",
                           source="nickname", priority=2)
    mapped, sources = owner.map_attributes(
        tenant="t1", attributes={"name": "A", "nickname": "B"})
    assert mapped == {"displayName": "B"}
    assert sources == {"displayName": "nickname"}

    fallback, fallback_sources = owner.map_attributes(
        tenant="t1", attributes={"name": "A"})
    assert fallback == {"displayName": "A"}
    assert fallback_sources == {"displayName": "name"}


def test_provision_unknown_domain_creates_nothing():
    owner = with_domain()
    with pytest.raises(jit.JitProvisionError) as exc:
        provision(owner, email="x@nowhere.example")
    assert exc.value.code == "JIT_DOMAIN_UNKNOWN"
    assert owner.stats()["rejected"] == 1
    assert owner.stats()["accounts"] == 0
    assert owner.get_account("acct-0001") is None


def test_provision_rejects_malformed_email_and_attributes():
    owner = with_domain()
    for email in ("", "no-at", "a@", "@corp.example"):
        with pytest.raises(jit.JitProvisionError) as exc:
            provision(owner, email=email)
        assert exc.value.code == "JIT_INVALID_REQUEST"
    with pytest.raises(jit.JitProvisionError) as exc:
        provision(owner, attributes=["name"])
    assert exc.value.code == "JIT_INVALID_REQUEST"
    assert owner.stats()["accounts"] == 0


def test_tenant_rate_limit_blocks_and_then_recovers():
    owner = with_domain(rate_limit=2, rate_window=3600)
    provision(owner, email="a@corp.example", now=1000)
    provision(owner, email="b@corp.example", now=1000)
    with pytest.raises(jit.JitProvisionError) as exc:
        provision(owner, email="c@corp.example", now=1010)
    assert exc.value.code == "JIT_RATE_LIMITED"
    assert owner.stats()["rate_limited"] == 1
    assert owner.stats()["accounts"] == 2

    later = provision(owner, email="d@corp.example", now=1000 + 3600 + 1)
    assert later.status == "created"
    assert owner.stats()["accounts"] == 3


def test_request_merge_creates_pending_request_with_cooldown():
    owner = with_domain()
    provision(owner)
    request = owner.request_merge(account_id="acct-0001",
                                  email="alice.work@corp.example", now=2000)
    assert request.request_id == "merge-0001"
    assert request.account_id == "acct-0001"
    assert request.email == "alice.work@corp.example"
    assert request.status == "pending"
    assert request.requested_at == 2000
    assert request.confirmable_at == 5600
    assert owner.get_account("acct-0001").merged_from == ()
    assert owner.stats()["merge_requests"] == 1


def test_confirm_merge_during_cooldown_is_refused():
    owner = with_domain()
    provision(owner)
    request = owner.request_merge(account_id="acct-0001",
                                  email="alice.work@corp.example", now=2000)
    with pytest.raises(jit.JitProvisionError) as exc:
        owner.confirm_merge(request_id=request.request_id, now=5599)
    assert exc.value.code == "JIT_MERGE_COOLDOWN"
    assert owner.get_account("acct-0001").merged_from == ()
    assert owner.stats()["merged"] == 0


def test_confirm_merge_after_cooldown_links_the_account():
    owner = with_domain()
    provision(owner)
    request = owner.request_merge(account_id="acct-0001",
                                  email="alice.work@corp.example", now=2000)
    merged = owner.confirm_merge(request_id=request.request_id, now=5600)
    assert merged.account_id == "acct-0001"
    assert merged.merged_from == ("alice.work@corp.example",)
    assert owner.stats()["merged"] == 1


def test_unknown_and_repeated_merge_confirmation_are_rejected():
    owner = with_domain()
    provision(owner)
    with pytest.raises(jit.JitProvisionError) as exc:
        owner.confirm_merge(request_id="merge-9999", now=5600)
    assert exc.value.code == "JIT_MERGE_NOT_FOUND"

    request = owner.request_merge(account_id="acct-0001",
                                  email="alice.work@corp.example", now=2000)
    owner.confirm_merge(request_id=request.request_id, now=5600)
    with pytest.raises(jit.JitProvisionError) as exc:
        owner.confirm_merge(request_id=request.request_id, now=5601)
    assert exc.value.code == "JIT_MERGE_ALREADY_DONE"


def test_snapshot_is_detached_and_restore_roundtrips():
    owner = with_domain()
    owner.register_mapping(tenant="t1", target="displayName", source="name",
                           default="unknown", priority=0)
    provision(owner, attributes={"name": "Alice"})
    request = owner.request_merge(account_id="acct-0001",
                                  email="alice.work@corp.example", now=2000)
    state = owner.snapshot()
    assert set(state) == {"version", "domains", "mappings", "accounts",
                          "merges", "stats"}
    pristine = copy.deepcopy(state)

    state["domains"].clear()
    state["accounts"].clear()
    state["stats"]["accounts"] = 99
    assert owner.resolve_tenant(email="alice@corp.example") == "t1"
    assert owner.get_account("acct-0001").attributes == {
        "displayName": "Alice"}
    assert owner.stats()["accounts"] == 1

    clone = with_domain()
    clone.restore(pristine)
    assert clone.resolve_tenant(email="ALICE@corp.example") == "t1"
    assert clone.get_account("acct-0001").attributes == {
        "displayName": "Alice"}
    assert clone.stats()["accounts"] == 1
    merged = clone.confirm_merge(request_id=request.request_id, now=5600)
    assert merged.merged_from == ("alice.work@corp.example",)


def test_stats_track_resolve_provision_merge_and_rejection():
    owner = with_domain()
    assert owner.resolve_tenant(email="alice@corp.example") == "t1"
    provision(owner)
    with pytest.raises(jit.JitProvisionError):
        provision(owner, email="x@nowhere.example")
    request = owner.request_merge(account_id="acct-0001",
                                  email="alice.work@corp.example", now=2000)
    owner.confirm_merge(request_id=request.request_id, now=5600)

    assert owner.stats() == {"domains": 1, "resolved": 2, "provisioned": 1,
                             "merge_requests": 1, "merged": 1,
                             "rate_limited": 0, "rejected": 1,
                             "accounts": 1}
