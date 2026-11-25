import threading

import pytest

import ids_signature_engine as ids


def engine(**kwargs):
    return ids.IdsSignatureEngine(**kwargs)


def rule(sid=1001, content="alpha", **overrides):
    fields = {
        "sid": sid,
        "msg": "sig-%d" % sid,
        "content": content,
        "flow": "any",
        "proto": "any",
        "dst_port": None,
        "priority": 1,
    }
    fields.update(overrides)
    return fields


def packet(payload="alpha", **overrides):
    fields = {
        "src_ip": "10.0.0.5",
        "dst_ip": "192.0.2.9",
        "src_port": 50000,
        "dst_port": 80,
        "proto": "tcp",
        "payload": payload,
        "direction": "to_server",
    }
    fields.update(overrides)
    return fields


def test_rule_validation_duplicate_and_capacity():
    g = engine(max_rules=1)
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.add_rule({"msg": "no sid", "content": "alpha"})
    assert exc.value.code == "IDS_RULE_INVALID"
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.add_rule({"sid": 1, "content": "alpha"})
    assert exc.value.code == "IDS_RULE_INVALID"
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.add_rule({"sid": 1, "msg": "no content"})
    assert exc.value.code == "IDS_RULE_INVALID"
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.add_rule(rule(1, content=""))
    assert exc.value.code == "IDS_RULE_INVALID"
    stored = g.add_rule(rule(1))
    assert stored["sid"] == 1
    assert g.rule(1)["content"] == "alpha"
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.add_rule(rule(1))
    assert exc.value.code == "IDS_RULE_EXISTS"
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.add_rule(rule(2))
    assert exc.value.code == "IDS_CAPACITY"


def test_load_rules_is_atomic_and_leaves_previous_set_on_failure():
    g = engine()
    assert g.load_rules([rule(1), rule(2)]) == 2
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.load_rules([rule(3), {"sid": 4, "msg": "broken", "content": ""}])
    assert exc.value.code == "IDS_RULE_INVALID"
    assert g.rule(1) is not None
    assert g.rule(2) is not None
    assert g.rule(3) is None
    fired = g.inspect(packet())
    assert sorted(item["sid"] for item in fired) == [1, 2]


def test_content_match_produces_an_alert_with_metadata():
    g = engine()
    g.add_rule(rule(1001, content="GET /admin", msg="admin probe", priority=3))
    fired = g.inspect(packet("GET /admin HTTP/1.1\r\nHost: x"))
    assert len(fired) == 1
    assert fired[0]["sid"] == 1001
    assert fired[0]["msg"] == "admin probe"
    assert fired[0]["priority"] == 3
    assert fired[0]["src_ip"] == "10.0.0.5"
    assert g.stats()["alerts"] == 1


def test_nocase_controls_case_sensitivity():
    g = engine()
    g.add_rule(rule(1, content="admin", nocase=True))
    g.add_rule(rule(2, content="secret", nocase=False))
    fired = g.inspect(packet("ADMIN and SECRET"))
    assert [item["sid"] for item in fired] == [1]


def test_offset_and_depth_limit_the_scan_window():
    g = engine()
    g.add_rule(rule(1, content="evil", offset=10, depth=4))
    g.add_rule(rule(2, content="evil", offset=10, depth=3))
    fired = g.inspect(packet("0123456789evilrest"))
    assert [item["sid"] for item in fired] == [1]


def test_all_contents_must_match():
    g = engine()
    spec = rule(1)
    del spec["content"]
    spec["contents"] = ["select", "union"]
    g.add_rule(spec)
    assert g.inspect(packet("select * from t")) == []
    fired = g.inspect(packet("select 1 union select 2"))
    assert [item["sid"] for item in fired] == [1]


def test_flow_and_destination_port_constrain_matching():
    g = engine()
    g.add_rule(rule(1, content="probe", flow="to_server", dst_port=80))
    assert g.inspect(packet("probe", direction="from_server")) == []
    assert g.inspect(packet("probe", dst_port=8080)) == []
    fired = g.inspect(packet("probe", dst_port=80))
    assert [item["sid"] for item in fired] == [1]


def test_alerts_are_ordered_by_priority_then_sid():
    g = engine()
    g.add_rule(rule(200, content="alpha", priority=2))
    g.add_rule(rule(100, content="alpha", priority=1))
    fired = g.inspect(packet("alpha"))
    assert [item["sid"] for item in fired] == [100, 200]


def test_repeated_alerts_coalesce_inside_the_window():
    g = engine(suppress_window=30)
    g.add_rule(rule(1))
    first = g.inspect(packet(), now=0)
    assert first[0]["count"] == 1
    second = g.inspect(packet(), now=5)
    assert second[0]["count"] == 2
    assert len(g.alerts()) == 1
    assert g.alerts()[0]["count"] == 2
    g.inspect(packet(), now=100)
    assert len(g.alerts()) == 2
    assert g.stats()["coalesced"] == 1


def test_threshold_rate_limits_further_alerts():
    g = engine()
    g.add_rule(rule(1, threshold={"count": 2, "seconds": 60}))
    assert len(g.inspect(packet(), now=0)) == 1
    assert len(g.inspect(packet(), now=1)) == 1
    assert g.inspect(packet(), now=2) == []
    assert g.stats()["rate_limited"] == 1
    assert len(g.inspect(packet(), now=62)) == 1


def test_suppression_expires_after_ttl():
    g = engine()
    g.add_rule(rule(1))
    info = g.suppress(1, ttl=10, now=0)
    assert info["expires_at"] == 10
    assert g.inspect(packet(), now=5) == []
    assert g.stats()["suppressed"] == 1
    assert len(g.inspect(packet(), now=11)) == 1
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.suppress(99, ttl=10, now=0)
    assert exc.value.code == "IDS_RULE_UNKNOWN"


def test_alert_capacity_drops_the_oldest_entries():
    g = engine(max_alerts=2)
    g.add_rule(rule(1, content="alpha"))
    g.add_rule(rule(2, content="beta"))
    g.add_rule(rule(3, content="gamma"))
    g.inspect(packet("alpha beta gamma"))
    remaining = g.alerts()
    assert [item["sid"] for item in remaining] == [2, 3]
    assert g.stats()["alerts_dropped"] == 1


def test_removing_a_rule_stops_matching():
    g = engine()
    g.add_rule(rule(1))
    assert g.remove_rule(1) is True
    assert g.remove_rule(1) is False
    assert g.rule(1) is None
    assert g.inspect(packet()) == []
    assert g.stats()["removed"] == 1


def test_snapshot_and_restore_roundtrip():
    g = engine()
    g.add_rule(rule(1))
    g.inspect(packet(), now=0)
    g.suppress(1, ttl=50, now=0)
    snap = g.snapshot()
    assert snap["stats"]["alerts"] == 1
    g.remove_rule(1)
    assert g.rule(1) is None
    g.restore(snap)
    assert g.rule(1) is not None
    assert len(g.alerts()) == 1
    assert g.inspect(packet(), now=5) == []
    with pytest.raises(ids.IdsSignatureError) as exc:
        g.restore({"rules": "nope"})
    assert exc.value.code == "IDS_SNAPSHOT_INVALID"
    assert g.rule(1) is not None


def test_concurrent_reload_and_inspect_see_one_complete_ruleset():
    g = engine()
    set_a = [rule(1, content="alpha")]
    set_b = [rule(2, content="beta")]
    g.load_rules(set_a)
    payload = packet("alpha beta")
    results = []
    lock = threading.Lock()

    def inspect_worker():
        try:
            fired = g.inspect(payload, now=10)
        except ids.IdsSignatureError as exc:
            with lock:
                results.append(exc.code)
        else:
            with lock:
                results.append([item["sid"] for item in fired])

    def reload_worker():
        g.load_rules(set_b)
        g.load_rules(set_a)

    threads = []
    for index in range(8):
        target = inspect_worker if index % 2 == 0 else reload_worker
        threads.append(threading.Thread(target=target))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 4
    for fired in results:
        assert len(fired) == 1
        assert fired[0] in (1, 2)
