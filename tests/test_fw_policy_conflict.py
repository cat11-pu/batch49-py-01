import threading

import pytest

import fw_policy_conflict as fw


def analyzer(**kwargs):
    return fw.FirewallPolicyAnalyzer(**kwargs)


def add(g, name="r1", **overrides):
    fields = {
        "action": "allow",
        "src": "10.0.0.0/8",
        "dst": "any",
        "proto": "tcp",
        "ports": None,
        "priority": 100,
    }
    fields.update(overrides)
    return g.add_rule(name, **fields)


def test_rule_field_validation_and_capacity():
    g = analyzer(max_rules=1)
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, action="drop")
    assert exc.value.code == "FW_RULE_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, src="10.0.0.0/33")
    assert exc.value.code == "FW_ADDRESS_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, proto="sctp")
    assert exc.value.code == "FW_PROTO_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, ports="80-70")
    assert exc.value.code == "FW_PORT_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, proto="icmp", ports="80")
    assert exc.value.code == "FW_PORT_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, ports="70000")
    assert exc.value.code == "FW_PORT_INVALID"
    add(g, "r1")
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, "r1")
    assert exc.value.code == "FW_RULE_EXISTS"
    with pytest.raises(fw.FwPolicyError) as exc:
        add(g, "r2")
    assert exc.value.code == "FW_CAPACITY"


def test_query_uses_priority_then_first_match():
    g = analyzer()
    add(g, "deny-host", action="deny", src="10.1.2.3/32", ports="443", priority=10)
    add(g, "allow-net", src="10.0.0.0/8", ports="443", priority=100)
    denied = g.query("10.1.2.3", "192.0.2.1", port=443)
    assert denied["action"] == "deny"
    assert denied["rule"] == "deny-host"
    assert denied["default"] is False
    allowed = g.query("10.2.3.4", "192.0.2.1", port=443)
    assert allowed["action"] == "allow"
    assert allowed["rule"] == "allow-net"


def test_equal_priority_keeps_insertion_order():
    g = analyzer()
    add(g, "first", action="allow", ports="80", priority=50)
    add(g, "second", action="deny", ports="80", priority=50)
    out = g.query("10.0.0.1", "192.0.2.1", port=80)
    assert out["rule"] == "first"
    assert out["action"] == "allow"


def test_default_action_applies_when_nothing_matches():
    g = analyzer(default_action="deny")
    out = g.query("10.0.0.1", "192.0.2.1", port=80)
    assert out["action"] == "deny"
    assert out["rule"] is None
    assert out["default"] is True
    open_g = analyzer(default_action="allow")
    assert open_g.query("10.0.0.1", "192.0.2.1", port=80)["action"] == "allow"


def test_disabled_rules_are_skipped_and_can_be_reenabled():
    g = analyzer()
    add(g, "deny-host", action="deny", src="10.1.2.3/32", ports="443", priority=10)
    add(g, "allow-net", ports="443", priority=100)
    assert g.enable_rule("deny-host", enabled=False) is True
    assert g.query("10.1.2.3", "192.0.2.1", port=443)["rule"] == "allow-net"
    assert g.enable_rule("deny-host") is True
    assert g.query("10.1.2.3", "192.0.2.1", port=443)["rule"] == "deny-host"
    assert g.enable_rule("missing", enabled=False) is False


def test_query_rejects_inconsistent_protocol_and_port():
    g = analyzer()
    add(g, "http", ports="80")
    with pytest.raises(fw.FwPolicyError) as exc:
        g.query("10.0.0.1", "192.0.2.1")
    assert exc.value.code == "FW_QUERY_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        g.query("10.0.0.1", "192.0.2.1", proto="icmp", port=80)
    assert exc.value.code == "FW_QUERY_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        g.query("10.0.0.1", "192.0.2.1", port=70000)
    assert exc.value.code == "FW_QUERY_INVALID"
    with pytest.raises(fw.FwPolicyError) as exc:
        g.query("10.0.0.999", "192.0.2.1", port=80)
    assert exc.value.code == "FW_QUERY_INVALID"


def test_port_lists_and_ranges_match_inclusively():
    g = analyzer()
    add(g, "web", ports="80,443,8000-8080")
    for port in (80, 443, 8000, 8080):
        assert g.query("10.0.0.1", "192.0.2.1", port=port)["rule"] == "web"
    for port in (81, 7999, 8081):
        assert g.query("10.0.0.1", "192.0.2.1", port=port)["default"] is True


def test_source_and_destination_cidr_containment():
    g = analyzer()
    add(g, "internal", src="10.0.0.0/8", dst="192.168.0.0/16", ports="443")
    assert g.query("10.1.2.3", "192.168.5.5", port=443)["rule"] == "internal"
    assert g.query("10.1.2.3", "172.16.0.1", port=443)["default"] is True
    assert g.query("192.0.2.5", "192.168.5.5", port=443)["default"] is True


def test_explain_lists_rules_examined_before_the_match():
    g = analyzer()
    add(g, "deny-host", action="deny", src="10.1.2.3/32", ports="443", priority=10)
    add(g, "allow-net", ports="443", priority=100)
    out = g.explain("10.2.3.4", "192.0.2.1", port=443)
    assert out["rule"] == "allow-net"
    assert out["evaluated"] == ["deny-host", "allow-net"]
    first = g.explain("10.1.2.3", "192.0.2.1", port=443)
    assert first["rule"] == "deny-host"
    assert first["evaluated"] == ["deny-host"]


def test_full_coverage_with_different_actions_is_shadowing():
    g = analyzer()
    add(g, "allow-net", action="allow", ports="80-90", priority=10)
    add(g, "deny-sub", action="deny", src="10.1.0.0/16", ports="85", priority=20)
    items = g.conflicts()
    assert len(items) == 1
    assert items[0]["kind"] == "shadow"
    assert items[0]["earlier"] == "allow-net"
    assert items[0]["later"] == "deny-sub"


def test_full_coverage_with_same_action_is_redundant():
    g = analyzer()
    add(g, "allow-net", action="allow", ports="80-90", priority=10)
    add(g, "allow-sub", action="allow", src="10.1.0.0/16", ports="85", priority=20)
    items = g.conflicts()
    assert len(items) == 1
    assert items[0]["kind"] == "redundant"
    assert items[0]["earlier"] == "allow-net"


def test_partial_port_overlap_is_correlated():
    g = analyzer()
    add(g, "allow-range", action="allow", ports="80-90", priority=10)
    add(g, "deny-wide", action="deny", ports="85-95", priority=20)
    items = g.conflicts()
    assert len(items) == 1
    assert items[0]["kind"] == "correlated"
    assert items[0]["later"] == "deny-wide"


def test_remove_rule_and_stats():
    g = analyzer()
    add(g, "old", ports="80")
    add(g, "keep", action="deny", ports="81")
    assert g.remove_rule("old") is True
    assert g.remove_rule("old") is False
    assert g.remove_rule("missing") is False
    assert g.query("10.0.0.1", "192.0.2.1", port=80)["default"] is True
    assert g.query("10.0.0.1", "192.0.2.1", port=81)["rule"] == "keep"
    assert g.stats()["added"] == 2
    assert g.stats()["removed"] == 1


def test_snapshot_and_restore_roundtrip():
    g = analyzer()
    add(g, "allow-net", ports="80", priority=10)
    add(g, "deny-other", action="deny", ports="81", priority=20)
    snap = g.snapshot()
    assert snap["stats"]["added"] == 2
    g.remove_rule("allow-net")
    assert g.query("10.0.0.1", "192.0.2.1", port=80)["default"] is True
    g.restore(snap)
    assert g.query("10.0.0.1", "192.0.2.1", port=80)["rule"] == "allow-net"
    assert g.summary()["rules"] == 2
    with pytest.raises(fw.FwPolicyError) as exc:
        g.restore({"rules": "nope"})
    assert exc.value.code == "FW_SNAPSHOT_INVALID"
    assert g.query("10.0.0.1", "192.0.2.1", port=80)["rule"] == "allow-net"


def test_concurrent_queries_see_a_complete_rule_set():
    g = analyzer()
    add(g, "allow-net", src="10.0.0.0/8", ports="80", priority=10)
    results = []
    lock = threading.Lock()

    def worker():
        try:
            out = g.query("10.9.9.9", "192.0.2.1", port=80)
        except fw.FwPolicyError as exc:
            with lock:
                results.append(exc.code)
        else:
            with lock:
                results.append((out["action"], out["rule"]))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [("allow", "allow-net")] * 8
