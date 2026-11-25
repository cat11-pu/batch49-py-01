import copy
import threading
import time

import pytest

import dns_cache_guard as dg


def positive(query, *, ttl=60, value="192.0.2.10"):
    return {
        "txid": query["txid"],
        "question": {"name": query["name"], "qtype": query["qtype"]},
        "rcode": "NOERROR",
        "answers": [{"name": query["name"], "qtype": query["qtype"],
                     "ttl": ttl, "value": value}],
    }


def guard(responder=None, *, upstreams=("u1", "u2"), **kwargs):
    calls = []

    def fetcher(upstream, query):
        calls.append((upstream, dict(query)))
        if responder is None:
            return positive(query)
        return responder(upstream, query)

    g = dg.DnsCacheGuard(fetcher, **kwargs)
    g.set_upstreams(list(upstreams))
    return g, calls


def test_positive_answer_is_cached_and_served_without_refetch():
    g, calls = guard()
    out = g.resolve("www.example.com", "A", now=100)
    assert out["rcode"] == "NOERROR"
    assert out["cached"] is False
    assert out["stale"] is False
    assert out["negative"] is False
    assert out["source"] == "u1"
    assert out["ttl"] == 60
    assert out["expires_at"] == 160
    assert out["answers"] == ({"name": "www.example.com", "qtype": "A",
                               "ttl": 60, "value": "192.0.2.10"},)
    assert len(calls) == 1
    assert calls[0][0] == "u1"
    assert calls[0][1]["name"] == "www.example.com"

    hit = g.resolve("www.example.com", "A", now=101)
    assert hit["cached"] is True
    assert hit["expires_at"] == 160
    assert len(calls) == 1
    assert g.lookup("www.example.com", "A", now=159)["rcode"] == "NOERROR"
    assert g.lookup("www.example.com", "A", now=160) is None
    assert g.stats()["hits"] == 1
    assert g.stats()["misses"] == 1


def test_ttl_is_clamped_at_both_bounds():
    def responder(upstream, query):
        ttl = 9999 if query["name"] == "big.example.com" else 1
        return positive(query, ttl=ttl)

    g, _ = guard(responder)
    big = g.resolve("big.example.com", "A", now=1000)
    assert big["ttl"] == 300
    assert big["expires_at"] == 1300
    small = g.resolve("small.example.com", "A", now=1000)
    assert small["ttl"] == 30
    assert small["expires_at"] == 1030


def test_zero_ttl_is_raised_to_minimum_instead_of_skipping_cache():
    g, _ = guard(lambda upstream, query: positive(query, ttl=0))
    out = g.resolve("zero.example.com", "A", now=10)
    assert out["ttl"] == 30
    assert out["expires_at"] == 40
    assert g.lookup("zero.example.com", "A", now=39) is not None
    assert g.lookup("zero.example.com", "A", now=40) is None


def test_nxdomain_is_negatively_cached_with_its_own_cap():
    g, _ = guard(lambda upstream, query: {
        "txid": query["txid"],
        "question": {"name": query["name"], "qtype": query["qtype"]},
        "rcode": "NXDOMAIN",
        "answers": [],
        "negative_ttl": 999,
    })
    out = g.resolve("gone.example.com", "A", now=500)
    assert out["rcode"] == "NXDOMAIN"
    assert out["negative"] is True
    assert out["answers"] == ()
    assert out["ttl"] == 60
    assert out["expires_at"] == 560
    assert g.lookup("gone.example.com", "A", now=559) is not None
    assert g.lookup("gone.example.com", "A", now=560) is None
    assert g.purge(now=560) == 1
    assert g.purge(now=560) == 0


def test_query_name_and_type_are_normalized():
    g, calls = guard()
    out = g.resolve("API.Example.COM.", "a", now=10)
    assert calls[0][1]["name"] == "api.example.com"
    assert calls[0][1]["qtype"] == "A"
    assert out["name"] == "api.example.com"
    assert out["qtype"] == "A"
    hit = g.resolve("api.example.com", "A", now=11)
    assert hit["cached"] is True
    assert len(calls) == 1


def test_different_query_types_use_separate_cache_entries():
    def responder(upstream, query):
        value = "2001:db8::1" if query["qtype"] == "AAAA" else "192.0.2.7"
        return positive(query, value=value)

    g, calls = guard(responder)
    first = g.resolve("dual.example.com", "A", now=20)
    second = g.resolve("dual.example.com", "AAAA", now=20)
    assert first["answers"][0]["value"] == "192.0.2.7"
    assert second["answers"][0]["value"] == "2001:db8::1"
    assert len(calls) == 2
    assert g.lookup("dual.example.com", "A", now=20)["qtype"] == "A"
    assert g.lookup("dual.example.com", "AAAA", now=20)["qtype"] == "AAAA"


def test_txid_mismatch_is_rejected_and_next_upstream_is_tried():
    def responder(upstream, query):
        response = positive(query)
        if upstream == "u1":
            response["txid"] = (query["txid"] + 1) & 0xFFFF
        return response

    g, calls = guard(responder)
    out = g.resolve("a.example.com", "A", now=1)
    assert out["source"] == "u2"
    assert len(calls) == 2
    assert g.stats()["poisoned"] == 1
    assert g.stats()["failures"] == 1


def test_question_mismatch_is_rejected():
    def responder(upstream, query):
        response = positive(query)
        if upstream == "u1":
            response["question"] = {"name": "other.example.com",
                                    "qtype": query["qtype"]}
        return response

    g, calls = guard(responder)
    out = g.resolve("a.example.com", "A", now=1)
    assert out["source"] == "u2"
    assert len(calls) == 2
    assert g.stats()["poisoned"] == 1


def test_answer_outside_bailiwick_is_rejected():
    def responder(upstream, query):
        response = positive(query)
        if upstream == "u1":
            response["answers"].append({"name": "evil.example.net", "qtype": "A",
                                        "ttl": 60, "value": "203.0.113.9"})
        return response

    g, calls = guard(responder)
    out = g.resolve("a.example.com", "A", now=1)
    assert out["source"] == "u2"
    assert out["answers"] == ({"name": "a.example.com", "qtype": "A",
                               "ttl": 60, "value": "192.0.2.10"},)
    assert len(calls) == 2
    assert g.stats()["poisoned"] == 1


def test_cname_chain_is_accepted_and_each_ttl_is_clamped():
    def responder(upstream, query):
        return {
            "txid": query["txid"],
            "question": {"name": query["name"], "qtype": query["qtype"]},
            "rcode": "NOERROR",
            "answers": [
                {"name": "www.example.com", "qtype": "CNAME", "ttl": 20,
                 "value": "cdn.example.net"},
                {"name": "cdn.example.net", "qtype": "A", "ttl": 400,
                 "value": "192.0.2.55"},
            ],
        }

    g, _ = guard(responder)
    out = g.resolve("www.example.com", "A", now=50)
    assert len(out["answers"]) == 2
    assert [(row["qtype"], row["ttl"], row["value"]) for row in out["answers"]] == [
        ("CNAME", 30, "cdn.example.net"),
        ("A", 300, "192.0.2.55"),
    ]


def test_upstream_failures_quarantine_and_recover_after_cooldown():
    g, _ = guard()
    assert g.active_upstream(now=0) == "u1"
    first = g.note_failure("u1", now=0)
    assert first["failures"] == 1
    assert first["quarantined_until"] is None
    assert g.active_upstream(now=0) == "u1"
    second = g.note_failure("u1", now=1)
    assert second["failures"] == 2
    assert second["quarantined_until"] == 31
    assert g.active_upstream(now=1) == "u2"
    assert g.active_upstream(now=30) == "u2"
    assert g.active_upstream(now=31) == "u1"
    g.note_success("u1", now=2)
    assert g.active_upstream(now=2) == "u1"
    assert g.note_failure("u1", now=3)["failures"] == 1


def test_all_upstreams_failing_reports_attempts_then_no_upstream():
    def boom(upstream, query):
        raise RuntimeError("upstream down")

    g = dg.DnsCacheGuard(boom, failure_threshold=2, cooldown_seconds=30)
    g.set_upstreams(["u1", "u2"])
    with pytest.raises(dg.DnsCacheError) as exc:
        g.resolve("x.example.com", "A", now=0)
    assert exc.value.code == "DNS_RESOLVE_FAILED"
    assert [item["upstream"] for item in exc.value.detail["attempts"]] == ["u1", "u2"]
    assert g.stats()["failures"] == 2
    with pytest.raises(dg.DnsCacheError) as exc:
        g.resolve("y.example.com", "A", now=0)
    assert exc.value.code == "DNS_RESOLVE_FAILED"
    with pytest.raises(dg.DnsCacheError) as exc:
        g.resolve("z.example.com", "A", now=0)
    assert exc.value.code == "DNS_NO_UPSTREAM"


def test_cache_capacity_evicts_least_recently_used_entry():
    g, _ = guard(max_entries=2)
    g.resolve("a.example.com", "A", now=100)
    g.resolve("b.example.com", "A", now=100)
    assert g.lookup("a.example.com", "A", now=100) is not None
    g.resolve("c.example.com", "A", now=100)
    assert g.lookup("b.example.com", "A", now=100) is None
    assert g.lookup("a.example.com", "A", now=100) is not None
    assert g.lookup("c.example.com", "A", now=100) is not None
    assert g.stats()["evictions"] == 1


def test_concurrent_identical_queries_fetch_only_once():
    calls = 0
    calls_lock = threading.Lock()

    def fetcher(upstream, query):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return positive(query)

    g = dg.DnsCacheGuard(fetcher)
    g.set_upstreams(["u1", "u2"])
    barrier = threading.Barrier(8)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(g.resolve("same.example.com", "A", now=100))
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert calls == 1
    assert len(results) == 8
    assert all(row["rcode"] == "NOERROR" for row in results)


def test_snapshot_restore_keeps_cache_and_upstream_health():
    def no_fetch(upstream, query):
        raise AssertionError("fetcher must not run after restore")

    g, _ = guard()
    g.resolve("a.example.com", "A", now=100)
    g.note_failure("u1", now=100)
    g.note_failure("u1", now=101)
    state = g.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "cache", "upstreams", "health", "stats"}
    state["cache"].clear()
    state["stats"]["hits"] = 999
    assert g.lookup("a.example.com", "A", now=100) is not None

    clone = dg.DnsCacheGuard(no_fetch)
    clone.restore(pristine)
    assert clone.lookup("a.example.com", "A", now=100)["rcode"] == "NOERROR"
    assert clone.active_upstream(now=105) == "u2"

    for bad in (None, {}, {"version": 2}, {"version": 1, "cache": [],
                "upstreams": [], "health": {}, "stats": {}}):
        with pytest.raises(dg.DnsCacheError) as exc:
            clone.restore(bad)
        assert exc.value.code == "DNS_SNAPSHOT_INVALID"
    assert clone.lookup("a.example.com", "A", now=100) is not None
