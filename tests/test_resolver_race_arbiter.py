import copy
import threading

import pytest

import resolver_race_arbiter as rr

NOW = 5000


def answer(rcode=0, *records, latency=10):
    return {"rcode": rcode, "answers": tuple(records),
            "truncated": False, "latency_ms": latency}


def make(pages, **kwargs):
    calls = []

    def responder(upstream, query, transport):
        calls.append((upstream, query, transport))
        return dict(pages[(upstream, transport)])

    arbiter = rr.ResolverRaceArbiter(responder, **kwargs)
    return arbiter, calls


def two_upstream(pages_a, pages_b, **kwargs):
    arbiter, calls = make({("a", "udp"): pages_a[0], ("b", "udp"): pages_b[0]},
                          **kwargs)
    assert arbiter.set_upstreams(["a", "b"]) == 2
    return arbiter, calls


def test_set_upstreams_registers_in_order():
    arbiter, calls = make({("a", "udp"): answer(0, "1.1.1.1")})
    assert arbiter.set_upstreams(["a", "b"]) == 2
    assert arbiter.upstreams() == ("a", "b")
    assert arbiter.health() == {
        "a": {"upstream": "a", "state": "ok", "inflight": 0,
              "failures": 0, "cooldown_until": None},
        "b": {"upstream": "b", "state": "ok", "inflight": 0,
              "failures": 0, "cooldown_until": None},
    }


def test_set_upstreams_validates_input_and_limit():
    arbiter, calls = make({})
    with pytest.raises(rr.ResolveError) as exc:
        arbiter.set_upstreams([])
    assert exc.value.code == "RESOLVE_UPSTREAM_INVALID"
    with pytest.raises(rr.ResolveError) as exc:
        arbiter.set_upstreams(["a", "   "])
    assert exc.value.code == "RESOLVE_UPSTREAM_INVALID"
    with pytest.raises(rr.ResolveError) as exc:
        arbiter.set_upstreams(["a", 7])
    assert exc.value.code == "RESOLVE_UPSTREAM_INVALID"
    assert arbiter.upstreams() == ()

    limited, _ = make({}, max_upstreams=2)
    with pytest.raises(rr.ResolveError) as exc:
        limited.set_upstreams(["a", "b", "c"])
    assert exc.value.code == "RESOLVE_UPSTREAM_LIMIT"
    assert limited.upstreams() == ()


def test_first_arriving_answer_wins():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=30)],
                                 [answer(0, "1.1.1.1", latency=5)])
    result = arbiter.resolve("example.test", now=NOW)
    assert result == {"query": "example.test", "status": "ok",
                      "answers": ("1.1.1.1",), "upstream": "b",
                      "rcode": 0, "transport": "udp"}
    assert len(calls) == 2
    assert arbiter.stats()["resolved"] == 1


def test_losing_responses_are_cancelled():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=30)],
                                 [answer(0, "1.1.1.1", latency=5)])
    arbiter.resolve("example.test", now=NOW)
    assert arbiter.stats()["cancelled"] == 1
    assert arbiter.stats()["cache_hits"] == 0


def test_nxdomain_is_reported_when_it_comes_first():
    arbiter, calls = two_upstream([answer(3, latency=4)],
                                 [answer(3, latency=9)])
    result = arbiter.resolve("missing.test", now=NOW)
    assert result["status"] == "nxdomain"
    assert result["rcode"] == 3
    assert result["answers"] == ()
    assert result["upstream"] == "a"
    assert arbiter.stats()["nxdomain"] == 1


def test_servfail_is_classified():
    arbiter, calls = two_upstream([answer(2, latency=3)],
                                 [answer(0, "1.1.1.1", latency=20)])
    result = arbiter.resolve("flaky.test", now=NOW)
    assert result["status"] == "servfail"
    assert result["rcode"] == 2
    assert result["upstream"] == "a"
    assert arbiter.stats()["servfail"] == 1
    assert arbiter.health("a")["failures"] == 1


def test_repeated_servfail_puts_upstream_in_cooldown():
    arbiter, calls = two_upstream([answer(2, latency=3)],
                                 [answer(0, "1.1.1.1", latency=20)],
                                 penalty_threshold=2, cooldown_seconds=10)
    arbiter.resolve("q1.test", now=NOW)
    arbiter.resolve("q2.test", now=NOW)
    status = arbiter.health("a")
    assert status["state"] == "cooldown"
    assert status["failures"] == 2
    assert status["cooldown_until"] == NOW + 10
    assert arbiter.health("b")["state"] == "ok"
    assert arbiter.stats()["penalized"] == 1


def test_equal_latency_prefers_registration_order():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=7)],
                                 [answer(0, "1.1.1.1", latency=7)])
    result = arbiter.resolve("tie.test", now=NOW)
    assert result["upstream"] == "a"
    assert result["answers"] == ("9.9.9.9",)


def test_tcp_retry_requires_a_known_query():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9")],
                                 [answer(0, "1.1.1.1")])
    with pytest.raises(rr.ResolveError) as exc:
        arbiter.tcp_retry("ghost.test", "a", now=NOW)
    assert exc.value.code == "RESOLVE_QUERY_UNKNOWN"


def test_tcp_retry_requires_a_registered_upstream():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9")],
                                 [answer(0, "1.1.1.1")])
    arbiter.resolve("example.test", now=NOW)
    with pytest.raises(rr.ResolveError) as exc:
        arbiter.tcp_retry("example.test", "zzz", now=NOW)
    assert exc.value.code == "RESOLVE_UPSTREAM_UNKNOWN"


def test_tcp_retry_uses_tcp_transport():
    pages = {("a", "udp"): answer(0, "9.9.9.9", latency=4),
             ("b", "udp"): answer(0, "1.1.1.1", latency=20),
             ("b", "tcp"): answer(0, "1.1.1.1", "2.2.2.2", latency=25)}
    arbiter, calls = make(pages)
    arbiter.set_upstreams(["a", "b"])
    arbiter.resolve("example.test", now=NOW)
    result = arbiter.tcp_retry("example.test", "b", now=NOW)
    assert result == {"query": "example.test", "status": "ok",
                      "answers": ("1.1.1.1", "2.2.2.2"), "upstream": "b",
                      "rcode": 0, "transport": "tcp"}
    assert calls[-1] == ("b", "example.test", "tcp")
    assert arbiter.stats()["tcp_retries"] == 1


def test_resolve_is_idempotent_for_a_known_query():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=4)],
                                 [answer(0, "1.1.1.1", latency=20)])
    first = arbiter.resolve("example.test", now=NOW)
    second = arbiter.resolve("example.test", now=NOW + 1)
    assert second == first
    assert len(calls) == 2
    assert arbiter.stats()["cache_hits"] == 1


def test_late_answer_is_dropped_and_result_is_unchanged():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=4)],
                                 [answer(0, "1.1.1.1", latency=20)])
    first = arbiter.resolve("example.test", now=NOW)
    assert arbiter.deliver_late("example.test", "b",
                                answer(0, "6.6.6.6", latency=99), now=NOW + 3) is False
    assert arbiter.result_of("example.test") == first
    assert arbiter.deliver_late("ghost.test", "b",
                                answer(0, "6.6.6.6"), now=NOW) is False
    assert arbiter.result_of("ghost.test") is None
    assert arbiter.stats()["late_dropped"] == 2


def test_inflight_slots_are_released_after_each_resolve():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=4)],
                                 [answer(0, "1.1.1.1", latency=20)])
    failures = []

    def worker(index):
        try:
            arbiter.resolve(f"q{index}.test", now=NOW)
        except Exception as exc:  # pragma: no cover - surfaced via failures
            failures.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert arbiter.stats()["resolved"] == 8
    assert arbiter.health()["a"]["inflight"] == 0
    assert arbiter.health()["b"]["inflight"] == 0


def test_snapshot_is_detached_and_restore_roundtrips():
    arbiter, calls = two_upstream([answer(0, "9.9.9.9", latency=4)],
                                 [answer(0, "1.1.1.1", latency=20)])
    arbiter.resolve("example.test", now=NOW)
    state = arbiter.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "upstreams", "results", "counters"}

    state["results"].clear()
    assert arbiter.result_of("example.test")["answers"] == ("9.9.9.9",)

    clone, _ = make({})
    clone.restore(pristine)
    assert clone.upstreams() == ("a", "b")
    assert clone.result_of("example.test")["status"] == "ok"
    assert clone.stats() == arbiter.stats()

    for bad in (None, {},
                {"version": 2, "upstreams": [], "results": {}, "counters": {}},
                {"version": 1, "upstreams": {}, "results": {}, "counters": {}},
                {"version": 1, "upstreams": [], "results": {},
                 "counters": {"resolved": 1}}):
        with pytest.raises(rr.ResolveError) as exc:
            clone.restore(bad)
        assert exc.value.code == "RESOLVE_SNAPSHOT_INVALID"
    assert clone.upstreams() == ("a", "b")
