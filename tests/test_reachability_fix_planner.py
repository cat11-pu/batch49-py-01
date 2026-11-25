import copy

import pytest

import reachability_fix_planner as rfp


def planner(**kwargs):
    return rfp.ReachabilityFixPlanner(**kwargs)


def node(reg, name, *, package="app"):
    reg.add_node(name, package=package)


def edge(reg, caller, callee):
    reg.add_edge(caller, callee)


def vuln(reg, vuln_id, symbol, *, severity="high", fixed_in=None):
    reg.add_vulnerability(vuln_id, symbol=symbol, severity=severity,
                          fixed_in=fixed_in)


def test_add_node_validation_and_duplicates():
    reg = planner()
    node(reg, "a.func", package="pkg-a")
    assert reg.stats()["nodes"] == 1

    bad_calls = [
        dict(name="", package="pkg-a"),
        dict(name="   ", package="pkg-a"),
        dict(name="b.func", package=""),
        dict(name="b.func", package="  "),
        dict(name="b.func", package="pkg-b", version=""),
    ]
    for kwargs in bad_calls:
        with pytest.raises(rfp.ReachabilityError) as exc:
            reg.add_node(**kwargs)
        assert exc.value.code == "REACH_INVALID_REQUEST"

    with pytest.raises(rfp.ReachabilityError) as exc:
        node(reg, "a.func", package="pkg-a")
    assert exc.value.code == "REACH_DUPLICATE_NODE"


def test_add_edge_validation_self_loop_and_idempotent_duplicate():
    reg = planner()
    node(reg, "a")
    node(reg, "b")
    edge(reg, "a", "b")
    edge(reg, "a", "b")
    assert reg.stats()["edges"] == 1

    with pytest.raises(rfp.ReachabilityError) as exc:
        edge(reg, "a", "a")
    assert exc.value.code == "REACH_INVALID_EDGE"
    with pytest.raises(rfp.ReachabilityError) as exc:
        edge(reg, "a", "missing")
    assert exc.value.code == "REACH_NODE_UNKNOWN"


def test_mark_entry_updates_route_and_rejects_unknown_node():
    reg = planner()
    node(reg, "entry")
    reg.mark_entry("entry", route="/api/v1")
    reg.mark_entry("entry", route="/api/v2")
    assert reg.stats()["entries"] == 1
    assert reg.snapshot()["entries"]["entry"] == "/api/v2"

    with pytest.raises(rfp.ReachabilityError) as exc:
        reg.mark_entry("missing", route="/")
    assert exc.value.code == "REACH_NODE_UNKNOWN"


def test_add_vulnerability_validation_and_duplicates():
    reg = planner()
    node(reg, "sink", package="libfoo")
    vuln(reg, "CVE-1", "sink", severity="critical", fixed_in="2.1.0")
    assert reg.stats()["vulns"] == 1

    with pytest.raises(rfp.ReachabilityError) as exc:
        vuln(reg, "CVE-1", "sink")
    assert exc.value.code == "REACH_DUPLICATE_VULN"
    with pytest.raises(rfp.ReachabilityError) as exc:
        vuln(reg, "CVE-2", "sink", severity="urgent")
    assert exc.value.code == "REACH_INVALID_SEVERITY"
    with pytest.raises(rfp.ReachabilityError) as exc:
        vuln(reg, "CVE-3", "missing")
    assert exc.value.code == "REACH_NODE_UNKNOWN"


def test_analyze_direct_edge_finding_and_priority():
    reg = planner()
    node(reg, "entry", package="web")
    node(reg, "sink", package="libfoo")
    edge(reg, "entry", "sink")
    reg.mark_entry("entry", route="/submit")
    vuln(reg, "CVE-1", "sink", severity="critical")

    findings = reg.analyze()
    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, rfp.Finding)
    assert finding.vuln_id == "CVE-1"
    assert finding.symbol == "sink"
    assert finding.package == "libfoo"
    assert finding.severity == "critical"
    assert finding.reachable is True
    assert finding.depth == 1
    assert finding.evidence == ("entry", "sink")
    assert finding.entry == "entry"
    assert finding.fixed_in is None
    assert finding.priority == 490
    assert reg.stats()["analyses"] == 1
    assert reg.stats()["reachable"] == 1
    assert reg.stats()["unreachable"] == 0


def test_analyze_multi_hop_evidence_and_fix_metadata():
    reg = planner()
    for name, package in (("entry", "web"), ("parse", "parser"),
                          ("decode", "parser"), ("sink", "libfoo")):
        node(reg, name, package=package)
    edge(reg, "entry", "parse")
    edge(reg, "parse", "decode")
    edge(reg, "decode", "sink")
    reg.mark_entry("entry")
    vuln(reg, "CVE-2", "sink", severity="high", fixed_in="2.4.1")

    finding = reg.analyze()[0]
    assert finding.reachable is True
    assert finding.depth == 3
    assert finding.evidence == ("entry", "parse", "decode", "sink")
    assert finding.fixed_in == "2.4.1"
    assert finding.priority == 370


def test_analyze_unreachable_vulnerability():
    reg = planner()
    node(reg, "entry")
    node(reg, "isolated", package="libbar")
    reg.mark_entry("entry")
    vuln(reg, "CVE-3", "isolated", severity="low")

    finding = reg.analyze()[0]
    assert finding.reachable is False
    assert finding.depth == -1
    assert finding.evidence == ()
    assert finding.entry is None
    assert finding.priority == 100
    assert reg.stats()["reachable"] == 0
    assert reg.stats()["unreachable"] == 1


def test_analyze_terminates_on_cycle_with_simple_evidence():
    reg = planner()
    for name in ("entry", "a", "b", "sink"):
        node(reg, name)
    edge(reg, "entry", "a")
    edge(reg, "a", "b")
    edge(reg, "b", "a")
    edge(reg, "b", "sink")
    reg.mark_entry("entry")
    vuln(reg, "CVE-4", "sink", severity="medium")

    finding = reg.analyze()[0]
    assert finding.depth == 3
    assert finding.evidence == ("entry", "a", "b", "sink")
    assert len(set(finding.evidence)) == len(finding.evidence)
    assert finding.priority == 270


def test_analyze_prefers_lexicographically_smallest_equal_length_path():
    reg = planner()
    for name in ("entry", "b.mid", "a.mid", "sink"):
        node(reg, name)
    edge(reg, "entry", "b.mid")
    edge(reg, "entry", "a.mid")
    edge(reg, "b.mid", "sink")
    edge(reg, "a.mid", "sink")
    reg.mark_entry("entry")
    vuln(reg, "CVE-5", "sink")

    finding = reg.analyze()[0]
    assert finding.evidence == ("entry", "a.mid", "sink")


def test_findings_are_ranked_by_priority_then_vuln_id():
    reg = planner()
    for name in ("entry", "mid", "direct-sink", "deep-sink", "isolated"):
        node(reg, name)
    edge(reg, "entry", "direct-sink")
    edge(reg, "entry", "mid")
    edge(reg, "mid", "deep-sink")
    reg.mark_entry("entry")
    vuln(reg, "V-direct", "direct-sink", severity="critical")
    vuln(reg, "V-long", "deep-sink", severity="high")
    vuln(reg, "V-iso-c", "isolated", severity="low")
    vuln(reg, "V-iso-a", "isolated", severity="low")

    findings = reg.analyze()
    assert [item.vuln_id for item in findings] == [
        "V-direct", "V-long", "V-iso-a", "V-iso-c"]
    assert [item.priority for item in findings] == [490, 380, 100, 100]


def test_plan_orders_remediation_and_applies_limit():
    reg = planner()
    for name in ("entry", "mid", "direct-sink", "deep-sink", "isolated"):
        node(reg, name)
    edge(reg, "entry", "direct-sink")
    edge(reg, "entry", "mid")
    edge(reg, "mid", "deep-sink")
    reg.mark_entry("entry")
    vuln(reg, "V-direct", "direct-sink", severity="critical")
    vuln(reg, "V-long", "deep-sink", severity="high")
    vuln(reg, "V-iso", "isolated", severity="low")

    assert reg.plan() == ("V-direct", "V-long", "V-iso")
    assert reg.plan(limit=2) == ("V-direct", "V-long")
    with pytest.raises(rfp.ReachabilityError) as exc:
        reg.plan(limit=0)
    assert exc.value.code == "REACH_INVALID_REQUEST"


def test_evidence_for_handles_unknown_and_unreachable():
    reg = planner()
    node(reg, "entry")
    node(reg, "sink")
    node(reg, "isolated")
    edge(reg, "entry", "sink")
    reg.mark_entry("entry")
    vuln(reg, "CVE-6", "sink")
    vuln(reg, "CVE-7", "isolated")

    assert reg.evidence_for("CVE-6") == ("entry", "sink")
    assert reg.evidence_for("CVE-7") == ()
    with pytest.raises(rfp.ReachabilityError) as exc:
        reg.evidence_for("CVE-404")
    assert exc.value.code == "REACH_VULN_UNKNOWN"


def test_graph_mutation_invalidates_previous_analysis():
    reg = planner()
    node(reg, "entry")
    node(reg, "isolated")
    reg.mark_entry("entry")
    vuln(reg, "CVE-8", "isolated", severity="medium")

    first = reg.analyze()[0]
    assert first.reachable is False
    assert reg.stats()["analyses"] == 1

    edge(reg, "entry", "isolated")
    second = reg.analyze()[0]
    assert second.reachable is True
    assert second.depth == 1
    assert second.evidence == ("entry", "isolated")
    assert reg.stats()["analyses"] == 2
    assert reg.stats()["reachable"] == 1
    assert reg.stats()["unreachable"] == 0

    assert reg.remove_edge("entry", "isolated") is True
    assert reg.remove_edge("entry", "isolated") is False
    assert reg.analyze()[0].reachable is False
    assert reg.stats()["analyses"] == 3

    assert reg.remove_vulnerability("CVE-8") is True
    assert reg.remove_vulnerability("CVE-8") is False
    assert reg.analyze() == ()


def test_capacity_limits_for_nodes_and_edges():
    reg = planner(max_nodes=2, max_edges=1)
    node(reg, "a")
    node(reg, "b")
    with pytest.raises(rfp.ReachabilityError) as exc:
        node(reg, "c")
    assert exc.value.code == "REACH_CAPACITY_EXCEEDED"

    edge(reg, "a", "b")
    with pytest.raises(rfp.ReachabilityError) as exc:
        edge(reg, "b", "a")
    assert exc.value.code == "REACH_CAPACITY_EXCEEDED"
    assert reg.stats()["edges"] == 1


def test_snapshot_restore_state_roundtrip_and_invalid_payload():
    reg = planner()
    node(reg, "entry", package="web")
    node(reg, "sink", package="libfoo")
    edge(reg, "entry", "sink")
    reg.mark_entry("entry", route="/submit")
    vuln(reg, "CVE-9", "sink", severity="high", fixed_in="1.9.2")
    reg.analyze()

    state = reg.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "nodes", "edges", "entries",
                             "vulnerabilities", "counters", "stats"}

    node(reg, "extra", package="pkg-x")
    edge(reg, "entry", "extra")
    state["edges"].clear()
    state["stats"]["analyses"] = 99
    assert reg.stats()["analyses"] == 1
    assert reg.stats()["nodes"] == 3

    clone = planner()
    clone.restore_state(pristine)
    assert clone.stats()["nodes"] == 2
    assert clone.stats()["edges"] == 1
    finding = clone.analyze()[0]
    assert finding.evidence == ("entry", "sink")
    assert finding.fixed_in == "1.9.2"

    bad_states = [
        None,
        {},
        {"version": 2, "nodes": {}, "edges": [], "entries": {},
         "vulnerabilities": {}, "counters": {}, "stats": {}},
        {"version": 1, "nodes": [], "edges": [], "entries": {},
         "vulnerabilities": {}, "counters": {}, "stats": {}},
        {"version": 1, "nodes": {}, "edges": "bad", "entries": {},
         "vulnerabilities": {}, "counters": {}, "stats": {}},
        {"version": 1, "nodes": {"a": {"package": "p", "version": "1"}},
         "edges": [["a", "ghost"]], "entries": {}, "vulnerabilities": {},
         "counters": {}, "stats": {}},
        {"version": 1, "nodes": {}, "edges": [], "entries": {},
         "vulnerabilities": {"V": {"symbol": "a", "severity": "urgent",
                                   "fixed_in": None}},
         "counters": {}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(rfp.ReachabilityError) as exc:
            clone.restore_state(bad)
        assert exc.value.code == "REACH_SNAPSHOT_INVALID"
    assert clone.stats()["nodes"] == 2
