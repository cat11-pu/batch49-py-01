import copy
import threading

import pytest

import scan_scope_gate as sg

T0 = 1000


def gate(**kwargs):
    return sg.ScanScopeGate(**kwargs)


def scoped_gate(budget=100, **kwargs):
    g = gate(tenant_budget=budget, **kwargs)
    g.grant_scope("t1", cidrs=["10.0.0.0/24"], domains=["example.com"], ports=[443])
    return g


def test_scope_starts_empty_and_grant_normalizes_rules():
    g = gate()
    assert g.scope_of("t1") == {
        "tenant": "t1",
        "version": 0,
        "allow": {"cidrs": (), "domains": (), "ports": ()},
        "deny": {"cidrs": (), "domains": (), "ports": ()},
    }
    assert g.grant_scope("t1", cidrs=["10.0.0.0/24"],
                         domains=["Example.COM."], ports=[443, "80-81"]) == 1
    scope = g.scope_of("t1")
    assert scope["version"] == 1
    assert scope["allow"]["cidrs"] == ("10.0.0.0/24",)
    assert scope["allow"]["domains"] == (".example.com",)
    assert scope["allow"]["ports"] == ("80-81", "443")
    assert scope["deny"] == {"cidrs": (), "domains": (), "ports": ()}


def test_scope_version_moves_only_on_effective_change():
    g = gate()
    assert g.grant_scope("t1", cidrs=["10.0.0.0/24"]) == 1
    assert g.grant_scope("t1", cidrs=["10.0.0.0/24"]) == 1
    assert g.deny_scope("t1", domains=["bad.example.com"]) == 2
    assert g.deny_scope("t1", domains=["bad.example.com"]) == 2
    assert g.revoke_scope("t1", domains=["missing.example.com"]) == 2
    assert g.revoke_scope("t1", domains=["bad.example.com"], effect="deny") == 3
    assert g.scope_of("t1")["deny"] == {"cidrs": (), "domains": (), "ports": ()}


def test_authorize_allows_in_scope_targets_and_meters_quota():
    g = scoped_gate(budget=10)
    out = g.authorize("t1", ["10.0.0.7:443", "example.com:443"],
                      request_id="r-1", now=T0)
    assert out["request_id"] == "r-1"
    assert out["tenant"] == "t1"
    assert out["scope_version"] == 1
    assert out["allowed"] == ("10.0.0.7:443", "example.com:443")
    assert out["blocked"] == ()
    assert out["consumed"] == 2
    assert out["remaining"] == 8
    assert g.remaining("t1", now=T0) == 8
    assert g.stats()["authorized"] == 2


def test_deny_rules_override_allow_rules():
    g = gate()
    g.grant_scope("t1", cidrs=["10.0.0.0/24"],
                  domains=["example.com"], ports=[443])
    g.deny_scope("t1", cidrs=["10.0.0.128/25"],
                 domains=["internal.example.com"], ports=[444])
    out = g.authorize("t1", ["10.0.0.200:443", "x.internal.example.com:443",
                             "10.0.0.7:443", "10.0.0.7:444"], now=T0)
    assert out["allowed"] == ("10.0.0.7:443",)
    codes = {item["target"]: item["code"] for item in out["blocked"]}
    assert codes == {
        "10.0.0.200:443": "SCAN_TARGET_DENIED",
        "x.internal.example.com:443": "SCAN_TARGET_DENIED",
        "10.0.0.7:444": "SCAN_PORT_DENIED",
    }
    assert out["consumed"] == 1


def test_domain_rules_match_suffix_without_partial_labels():
    g = gate()
    g.grant_scope("t1", domains=["example.com"], ports=[443])
    out = g.authorize("t1", ["example.com:443", "API.Example.COM.:443",
                             "notexample.com:443", "example.org:443"], now=T0)
    assert out["allowed"] == ("example.com:443", "api.example.com:443")
    codes = {item["target"]: item["code"] for item in out["blocked"]}
    assert codes == {
        "notexample.com:443": "SCAN_TARGET_OUT_OF_SCOPE",
        "example.org:443": "SCAN_TARGET_OUT_OF_SCOPE",
    }


def test_port_intervals_and_port_denials():
    g = gate()
    g.grant_scope("t1", cidrs=["10.0.0.0/24"], ports=["443-445", "8000-8100"])
    g.deny_scope("t1", ports=[444])
    out = g.authorize("t1", ["10.0.0.1:443", "10.0.0.1:444",
                             "10.0.0.1:8050", "10.0.0.1:8443"], now=T0)
    assert out["allowed"] == ("10.0.0.1:443", "10.0.0.1:8050")
    codes = {item["target"]: item["code"] for item in out["blocked"]}
    assert codes == {
        "10.0.0.1:444": "SCAN_PORT_DENIED",
        "10.0.0.1:8443": "SCAN_PORT_NOT_ALLOWED",
    }


def test_invalid_targets_are_blocked_individually():
    g = gate()
    g.grant_scope("t1", cidrs=["10.0.0.0/24"], ports=[443])
    targets = ["10.0.0.1", "10.0.0.1:0", "10.0.0.1:70000", "bad host:443",
               "10.0.0.1:8o", ":443", "10.0.0.1:443"]
    out = g.authorize("t1", targets, now=T0)
    assert out["allowed"] == ("10.0.0.1:443",)
    assert [item["code"] for item in out["blocked"]] == ["SCAN_TARGET_INVALID"] * 6
    assert out["consumed"] == 1


def test_tenant_without_scope_gets_no_allowance():
    g = gate()
    out = g.authorize("ghost", ["10.0.0.1:80"], now=T0)
    assert out["allowed"] == ()
    assert out["blocked"] == ({"target": "10.0.0.1:80",
                               "code": "SCAN_SCOPE_MISSING"},)
    assert out["consumed"] == 0
    assert out["remaining"] == 100
    assert g.denials()[0]["code"] == "SCAN_SCOPE_MISSING"
    assert g.stats()["blocked"] == 1


def test_quota_exhaustion_is_atomic_for_the_whole_request():
    g = scoped_gate(budget=5)
    first = g.authorize("t1", ["10.0.0.1:443", "10.0.0.2:443",
                               "10.0.0.3:443"], request_id="q1", now=T0)
    assert first["remaining"] == 2
    with pytest.raises(sg.ScanScopeError) as exc:
        g.authorize("t1", ["10.0.0.4:443", "10.0.0.5:443", "10.0.0.6:443"],
                    request_id="q2", now=T0)
    assert exc.value.code == "SCAN_QUOTA_EXHAUSTED"
    assert g.remaining("t1", now=T0) == 2
    assert g.denials() == ()
    assert g.stats()["quota_exhausted"] == 1
    last = g.authorize("t1", ["10.0.0.4:443", "10.0.0.5:443"],
                       request_id="q3", now=T0)
    assert last["consumed"] == 2
    assert last["remaining"] == 0


def test_request_id_replay_is_idempotent_and_conflicts_are_refused():
    g = scoped_gate(budget=10)
    targets = ["10.0.0.1:443", "10.0.0.2:443"]
    first = g.authorize("t1", targets, request_id="dup", now=T0)
    second = g.authorize("t1", targets, request_id="dup", now=T0 + 5)
    assert second == first
    assert g.remaining("t1", now=T0) == 8
    assert g.stats()["authorized"] == 2
    assert g.stats()["replayed"] == 1
    with pytest.raises(sg.ScanScopeError) as exc:
        g.authorize("t1", ["10.0.0.3:443"], request_id="dup", now=T0)
    assert exc.value.code == "SCAN_REQUEST_CONFLICT"
    assert g.remaining("t1", now=T0) == 8


def test_release_returns_quota_only_once():
    g = scoped_gate(budget=10)
    g.authorize("t1", ["10.0.0.1:443", "10.0.0.2:443"],
                request_id="rel", now=T0)
    assert g.release("rel") == 2
    assert g.remaining("t1", now=T0) == 10
    assert g.release("rel") == 0
    assert g.release("missing") == 0
    assert g.stats()["released"] == 2


def test_request_batch_and_empty_request_limits():
    g = scoped_gate(budget=10, max_targets_per_request=2)
    with pytest.raises(sg.ScanScopeError) as exc:
        g.authorize("t1", ["10.0.0.1:443", "10.0.0.2:443", "10.0.0.3:443"],
                    now=T0)
    assert exc.value.code == "SCAN_BATCH_TOO_LARGE"
    assert g.remaining("t1", now=T0) == 10
    with pytest.raises(sg.ScanScopeError) as exc:
        g.authorize("t1", [], now=T0)
    assert exc.value.code == "SCAN_BAD_REQUEST"


def test_rule_capacity_limit_does_not_leave_partial_rules():
    g = gate(max_rules_per_tenant=2)
    with pytest.raises(sg.ScanScopeError) as exc:
        g.grant_scope("t1", cidrs=["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"])
    assert exc.value.code == "SCAN_RULE_LIMIT"
    assert g.scope_of("t1")["version"] == 0
    assert g.grant_scope("t1", cidrs=["10.0.0.0/24"]) == 1
    with pytest.raises(sg.ScanScopeError) as exc:
        g.grant_scope("t1", cidrs=["10.0.1.0/24", "10.0.2.0/24"])
    assert exc.value.code == "SCAN_RULE_LIMIT"
    assert g.scope_of("t1")["allow"]["cidrs"] == ("10.0.0.0/24",)


def test_concurrent_authorizations_never_overspend_quota():
    g = scoped_gate(budget=10)
    barrier = threading.Barrier(20)
    accepted = []
    refused = []

    def worker(index):
        try:
            barrier.wait(timeout=5)
            out = g.authorize("t1", [f"10.0.0.{index}:443"],
                              request_id=f"c-{index}", now=T0)
            accepted.append(out)
        except sg.ScanScopeError as exc:
            refused.append(exc.code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 21)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 10
    assert refused == ["SCAN_QUOTA_EXHAUSTED"] * 10
    assert g.remaining("t1", now=T0) == 0
    assert g.stats()["authorized"] == 10


def test_snapshot_restore_keeps_scope_quota_denials_and_replay():
    g = scoped_gate(budget=10)
    g.authorize("t1", ["10.0.0.1:443", "10.0.0.2:443"],
                request_id="s-1", now=T0)
    g.authorize("t1", ["10.0.0.3:443"], request_id="s-2", now=T0)
    g.authorize("t1", ["10.0.0.3:22"], request_id="s-3", now=T0)

    state = g.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "tenants", "denials", "requests",
                             "counters", "stats"}
    state["tenants"].clear()
    state["stats"]["authorized"] = 0
    assert g.remaining("t1", now=T0) == 7
    assert len(g.denials()) == 1

    clone = gate(tenant_budget=10)
    clone.restore(pristine)
    assert clone.remaining("t1", now=T0) == 7
    replay = clone.authorize("t1", ["10.0.0.1:443", "10.0.0.2:443"],
                             request_id="s-1", now=T0)
    assert replay["consumed"] == 2
    assert clone.remaining("t1", now=T0) == 7
    assert clone.stats()["authorized"] == 3
    assert clone.denials()[0]["code"] == "SCAN_PORT_NOT_ALLOWED"

    for bad in (None, {}, {"version": 2}, {"version": 1, "tenants": [],
                "denials": [], "requests": {}, "counters": {}, "stats": {}}):
        with pytest.raises(sg.ScanScopeError) as exc:
            clone.restore(bad)
        assert exc.value.code == "SCAN_SNAPSHOT_INVALID"
    assert clone.remaining("t1", now=T0) == 7
