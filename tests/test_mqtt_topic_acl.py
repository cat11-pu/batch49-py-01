import threading

import pytest

import mqtt_topic_acl as mq


def acl(**kwargs):
    return mq.MqttTopicAcl(**kwargs)


def test_normalize_topic_rejects_wildcards_and_empty_levels():
    a = acl()
    assert a.normalize_topic("factory/line1/temp") == "factory/line1/temp"
    assert a.normalize_topic("$SYS/broker/uptime") == "$SYS/broker/uptime"
    bad_topics = ("", "a//b", "/a", "a/", "a/+/b", "a/#", "a b",
                  "x/" + "/".join(["y"] * 10))
    for bad in bad_topics:
        with pytest.raises(mq.MqttAclError) as exc:
            a.normalize_topic(bad)
        assert exc.value.code == "MQTT_TOPIC_INVALID"


def test_normalize_filter_validates_wildcard_placement():
    a = acl()
    assert a.normalize_filter("factory/+/temp") == "factory/+/temp"
    assert a.normalize_filter("factory/#") == "factory/#"
    assert a.normalize_filter("#") == "#"
    bad_filters = ("factory/#/temp", "factory+", "factory/+temp",
                   "factory//temp", "/factory/#", "", "$share//factory/#")
    for bad in bad_filters:
        with pytest.raises(mq.MqttAclError) as exc:
            a.normalize_filter(bad)
        assert exc.value.code == "MQTT_FILTER_INVALID"


def test_normalize_filter_handles_shared_subscriptions():
    a = acl()
    assert a.normalize_filter("$share/g1/factory/+/temp") == "$share/g1/factory/+/temp"
    assert a.normalize_filter("$share/g-2/#") == "$share/g-2/#"
    for bad in ("$share/g1/$share/g2/a", "$share/g1", "$share/.../a"):
        with pytest.raises(mq.MqttAclError) as exc:
            a.normalize_filter(bad)
        assert exc.value.code == "MQTT_FILTER_INVALID"


def test_add_rule_is_idempotent_and_capped():
    a = acl(max_rules=2)
    with pytest.raises(mq.MqttAclError) as exc:
        a.add_rule("svc-a", "plant/#", effect="maybe")
    assert exc.value.code == "MQTT_RULE_INVALID"
    first = a.add_rule("svc-a", "plant/#", effect="allow", priority=10)
    assert first["created"] is True
    assert first["effect"] == "allow"
    same = a.add_rule("svc-a", "plant/#", effect="allow", priority=10)
    assert same["created"] is False
    assert same["id"] == first["id"]
    assert len(a.rules()) == 1
    a.add_rule("*", "public/#", effect="allow")
    with pytest.raises(mq.MqttAclError) as exc:
        a.add_rule("svc-b", "x/#")
    assert exc.value.code == "MQTT_CAPACITY_EXCEEDED"


def test_authorize_prefers_higher_priority_rule():
    a = acl()
    allow = a.add_rule("*", "plant/#", effect="allow", priority=10)
    a.add_rule("*", "plant/secret", effect="deny", priority=5)
    out = a.authorize("svc-a", "plant/secret", action="publish")
    assert out["allowed"] is True
    assert out["reason"] == "matched"
    assert out["rule"] == allow["id"]


def test_authorize_prefers_specific_deny_at_equal_priority():
    a = acl()
    a.add_rule("*", "plant/#", effect="allow", priority=0)
    a.add_rule("*", "plant/secret", effect="deny", priority=0)
    assert a.authorize("svc-a", "plant/secret")["allowed"] is False
    assert a.authorize("svc-a", "plant/line1")["allowed"] is True


def test_authorize_exact_principal_beats_wildcard_and_defaults_to_deny():
    a = acl()
    deny = a.add_rule("*", "plant/#", effect="deny", priority=0)
    a.add_rule("svc-a", "plant/#", effect="allow", priority=0)
    assert a.authorize("svc-a", "plant/line1")["allowed"] is True
    other = a.authorize("svc-b", "plant/line1")
    assert other["allowed"] is False
    assert other["rule"] == deny["id"]
    none = a.authorize("svc-b", "other/x")
    assert none["allowed"] is False
    assert none["reason"] == "default"
    assert none["rule"] is None


def test_subscribe_is_idempotent_and_respects_deny_and_capacity():
    a = acl(max_subscriptions=2)
    a.add_rule("svc-a", "plant/#", effect="allow")
    first = a.subscribe("svc-a", "plant/+/temp", qos=1, now=0)
    assert first["created"] is True
    assert first["principal"] == "svc-a"
    assert first["filter"] == "plant/+/temp"
    assert first["qos"] == 1
    again = a.subscribe("svc-a", "plant/+/temp", qos=2, now=1)
    assert again["created"] is False
    assert again["qos"] == 1
    assert again["id"] == first["id"]
    a.subscribe("svc-a", "plant/#", qos=0, now=2)
    with pytest.raises(mq.MqttAclError) as exc:
        a.subscribe("svc-a", "plant/line1/#", qos=0)
    assert exc.value.code == "MQTT_CAPACITY_EXCEEDED"
    with pytest.raises(mq.MqttAclError) as exc:
        a.subscribe("svc-b", "plant/#", qos=0)
    assert exc.value.code == "MQTT_SUBSCRIBE_DENIED"


def test_publish_delivers_to_matching_filters_with_qos_downgrade():
    a = acl()
    a.add_rule("*", "#", effect="allow", priority=1)
    a.subscribe("svc-a", "plant/+/temp", qos=0, now=0)
    a.subscribe("svc-b", "plant/#", qos=2, now=0)
    a.subscribe("svc-c", "plant/+/humidity", qos=2, now=0)
    out = a.publish("svc-pub", "plant/line1/temp", b"21.5", qos=1, now=10)
    assert out["seq"] == 1
    assert out["delivered"] == 2
    assert out["retained"] is False
    msgs_a = a.drain("svc-a")
    assert len(msgs_a) == 1
    assert msgs_a[0]["topic"] == "plant/line1/temp"
    assert msgs_a[0]["payload"] == b"21.5"
    assert msgs_a[0]["qos"] == 0
    assert msgs_a[0]["seq"] == 1
    msgs_b = a.drain("svc-b")
    assert msgs_b[0]["qos"] == 1
    assert a.drain("svc-c") == []


def test_publish_denied_does_not_advance_sequence():
    a = acl()
    a.add_rule("svc-pub", "plant/#", effect="allow")
    a.add_rule("svc-pub", "plant/secret/#", effect="deny", priority=5)
    with pytest.raises(mq.MqttAclError) as exc:
        a.publish("svc-pub", "plant/secret/code", b"x", qos=1)
    assert exc.value.code == "MQTT_PUBLISH_DENIED"
    out = a.publish("svc-pub", "plant/line1/temp", b"y", qos=0)
    assert out["seq"] == 1
    assert a.stats()["denied_publish"] == 1
    assert a.stats()["publishes"] == 1


def test_queue_cap_drops_oldest_and_drain_is_ordered():
    a = acl(max_queued=2)
    a.add_rule("*", "#", effect="allow", priority=1)
    a.subscribe("svc-a", "plant/#", qos=2)
    for index in range(4):
        a.publish("svc-pub", "plant/l%d" % index, b"p%d" % index,
                  qos=2, now=10 + index)
    msgs = a.drain("svc-a")
    assert [m["seq"] for m in msgs] == [3, 4]
    assert [m["topic"] for m in msgs] == ["plant/l2", "plant/l3"]
    assert a.stats()["dropped"] == 2
    assert a.drain("svc-a") == []


def test_retained_replay_returns_latest_per_topic_sorted():
    a = acl()
    a.add_rule("*", "#", effect="allow", priority=1)
    a.subscribe("svc-a", "plant/#", qos=2)
    a.publish("svc-pub", "plant/b/temp", b"1", qos=1, retain=True, now=10)
    a.publish("svc-pub", "plant/a/temp", b"2", qos=2, retain=True, now=11)
    a.publish("svc-pub", "plant/b/temp", b"3", qos=0, retain=True, now=12)
    a.drain("svc-a")
    replayed = a.replay("svc-a", now=20)
    assert [m["topic"] for m in replayed] == ["plant/a/temp", "plant/b/temp"]
    assert [m["payload"] for m in replayed] == [b"2", b"3"]
    assert all(m["retained"] is True for m in replayed)
    by_topic = {m["topic"]: m for m in replayed}
    assert by_topic["plant/a/temp"]["qos"] == 2
    assert by_topic["plant/b/temp"]["qos"] == 0
    assert a.stats()["replays"] == 1


def test_replay_filters_topics_denied_for_read():
    a = acl()
    a.add_rule("svc-pub", "plant/#", effect="allow", priority=10)
    a.add_rule("svc-a", "plant/#", effect="allow", priority=1)
    a.add_rule("svc-a", "plant/secret/#", effect="deny", priority=9)
    a.subscribe("svc-a", "plant/#", qos=0, now=0)
    a.publish("svc-pub", "plant/line1/temp", b"ok", qos=0,
              retain=True, now=10)
    a.publish("svc-pub", "plant/secret/token", b"no", qos=0,
              retain=True, now=11)
    a.drain("svc-a")
    replayed = a.replay("svc-a", now=20)
    assert [m["topic"] for m in replayed] == ["plant/line1/temp"]


def test_revoke_and_snapshot_restore():
    a = acl()
    a.add_rule("svc-a", "plant/#", effect="allow", priority=1)
    a.add_rule("svc-b", "plant/#", effect="allow", priority=1)
    a.add_rule("svc-pub", "plant/#", effect="allow", priority=1)
    a.subscribe("svc-a", "plant/#", qos=1)
    a.subscribe("svc-b", "plant/#", qos=1)
    a.publish("svc-pub", "plant/l1", b"x", qos=1, now=0)
    snap = a.snapshot()
    out = a.revoke("svc-b")
    assert out["rules"] == 1
    assert out["subscriptions"] == 1
    assert out["queued"] == 1
    assert a.authorize("svc-b", "plant/l1")["allowed"] is False
    assert [s["principal"] for s in a.subscriptions()] == ["svc-a"]
    assert a.stats()["revocations"] == 1
    a.restore(snap)
    assert len(a.rules()) == 3
    assert len(a.subscriptions()) == 2
    assert a.authorize("svc-b", "plant/l1")["allowed"] is True
    with pytest.raises(mq.MqttAclError) as exc:
        a.restore({"rules": "x"})
    assert exc.value.code == "MQTT_SNAPSHOT_INVALID"
    assert len(a.rules()) == 3
    assert len(a.subscriptions()) == 2


def test_concurrent_publish_keeps_unique_monotonic_sequence():
    a = acl(max_queued=64)
    a.add_rule("*", "#", effect="allow", priority=1)
    a.subscribe("svc-a", "plant/#", qos=2)
    seqs = []
    lock = threading.Lock()

    def worker(index):
        out = a.publish("svc-pub", "plant/l%d" % index, b"p", qos=1,
                        now=100 + index)
        with lock:
            seqs.append(out["seq"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(seqs) == list(range(1, 9))
    msgs = a.drain("svc-a")
    assert [m["seq"] for m in msgs] == list(range(1, 9))
