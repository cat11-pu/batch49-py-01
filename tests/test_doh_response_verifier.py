import threading

import pytest

import doh_response_verifier as dv

SPKI = "a" * 64


def verifier(**kwargs):
    return dv.DohResponseVerifier(**kwargs)


def register(g, resolver_id="r1", endpoint="https://doh1.example/dns-query"):
    return g.register_resolver(resolver_id, endpoint, spki_sha256=SPKI)


def record(name="example.com", ttl=300, data="192.0.2.10", rtype="A"):
    return {"name": name, "type": rtype, "ttl": ttl, "data": data}


def response(name="example.com", qtype="A", answers=None, status=0,
             ad=False, content_type="application/dns-json", soa_ttl=None):
    body = {
        "status": status,
        "content_type": content_type,
        "question": {"name": name, "qtype": qtype},
        "answers": [] if answers is None else answers,
        "ad": ad,
    }
    if soa_ttl is not None:
        body["soa_ttl"] = soa_ttl
    return body


def test_resolver_registration_validation_and_retirement():
    g = verifier()
    info = register(g)
    assert info["resolver_id"] == "r1"
    assert info["retired"] is False
    assert g.resolvers()[0]["endpoint"] == "https://doh1.example/dns-query"
    with pytest.raises(dv.DohVerifyError) as exc:
        register(g)
    assert exc.value.code == "DOH_RESOLVER_EXISTS"
    with pytest.raises(dv.DohVerifyError) as exc:
        g.register_resolver("r2", "http://plain.example/dns-query", spki_sha256=SPKI)
    assert exc.value.code == "DOH_RESOLVER_INVALID"
    with pytest.raises(dv.DohVerifyError) as exc:
        g.register_resolver("r3", "https://doh3.example/dns-query", spki_sha256="xyz")
    assert exc.value.code == "DOH_RESOLVER_INVALID"
    assert g.retire_resolver("r1", now=5) is True
    assert g.retire_resolver("r1", now=6) is False
    with pytest.raises(dv.DohVerifyError) as exc:
        g.retire_resolver("missing")
    assert exc.value.code == "DOH_RESOLVER_UNKNOWN"

    capped = verifier(max_resolvers=1)
    register(capped, "r1")
    with pytest.raises(dv.DohVerifyError) as exc:
        register(capped, "r2")
    assert exc.value.code == "DOH_CAPACITY"
    assert len(capped.resolvers()) == 1


def test_begin_query_normalizes_name_and_allocates_ids():
    g = verifier()
    first = g.begin_query("Example.COM.", "a", nonce="n1", now=10)
    assert first["query_id"] == "q-1"
    assert first["name"] == "example.com"
    assert first["qtype"] == "A"
    assert first["nonce"] == "n1"
    second = g.begin_query("other.example", "TXT", nonce="n2", now=11)
    assert second["query_id"] == "q-2"
    assert second["qtype"] == "TXT"


def test_begin_query_rejects_bad_qtype_nonce_and_reuse():
    g = verifier()
    with pytest.raises(dv.DohVerifyError) as exc:
        g.begin_query("example.com", "MX", nonce="n1")
    assert exc.value.code == "DOH_QUERY_INVALID"
    with pytest.raises(dv.DohVerifyError) as exc:
        g.begin_query("example.com", "A", nonce="")
    assert exc.value.code == "DOH_QUERY_INVALID"
    g.begin_query("example.com", "A", nonce="n1")
    with pytest.raises(dv.DohVerifyError) as exc:
        g.begin_query("other.example", "A", nonce="n1")
    assert exc.value.code == "DOH_NONCE_REUSED"


def test_submit_refuses_unknown_query_and_unusable_resolver():
    g = verifier()
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-9", "r1", response())
    assert exc.value.code == "DOH_QUERY_UNKNOWN"
    g.begin_query("example.com", "A", nonce="n1")
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-1", "r9", response())
    assert exc.value.code == "DOH_RESOLVER_UNKNOWN"
    register(g, "r1")
    g.retire_resolver("r1")
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-1", "r1", response())
    assert exc.value.code == "DOH_RESOLVER_RETIRED"


def test_submit_binds_response_to_the_question_and_content_type():
    g = verifier()
    register(g)
    g.begin_query("example.com", "A", nonce="n1")
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-1", "r1", response(content_type="text/plain"))
    assert exc.value.code == "DOH_CONTENT_TYPE"
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-1", "r1", response(name="evil.example"))
    assert exc.value.code == "DOH_QUESTION_MISMATCH"
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-1", "r1", response(qtype="AAAA"))
    assert exc.value.code == "DOH_QUESTION_MISMATCH"
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-1", "r1", {"status": 0, "content_type": "application/dns-json"})
    assert exc.value.code == "DOH_RESPONSE_INVALID"


def test_ttl_is_clamped_and_negative_ttl_is_refused():
    g = verifier(min_ttl=30, max_ttl=3600)
    register(g)
    g.begin_query("example.com", "A", nonce="n1")
    low = g.submit("q-1", "r1", response(answers=[record(ttl=5)]))
    assert low["ttl"] == 30
    assert low["answers"][0]["ttl"] == 30
    g.begin_query("example.com", "A", nonce="n2")
    high = g.submit("q-2", "r1", response(answers=[record(ttl=99999)]))
    assert high["ttl"] == 3600
    g.begin_query("example.com", "A", nonce="n3")
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-3", "r1", response(answers=[record(ttl=-1)]))
    assert exc.value.code == "DOH_TTL_INVALID"


def test_cname_chain_is_followed_and_depth_is_bounded():
    g = verifier(quorum=1, chain_depth=3)
    register(g)
    g.begin_query("www.example.com", "A", nonce="n1")
    chain = [
        record("www.example.com", 60, "edge.example.com", "CNAME"),
        record("edge.example.com", 60, "192.0.2.7"),
    ]
    out = g.submit("q-1", "r1", response(name="www.example.com", answers=chain))
    assert out["ttl"] == 60
    assert out["answers"][1]["data"] == "192.0.2.7"

    deep = verifier(quorum=1, chain_depth=2)
    register(deep)
    deep.begin_query("deep.example", "A", nonce="d1")
    links = [
        record("deep.example", 60, "a.example", "CNAME"),
        record("a.example", 60, "b.example", "CNAME"),
        record("b.example", 60, "c.example", "CNAME"),
        record("c.example", 60, "192.0.2.9"),
    ]
    with pytest.raises(dv.DohVerifyError) as exc:
        deep.submit("q-1", "r1", response(name="deep.example", answers=links))
    assert exc.value.code == "DOH_CHAIN_TOO_DEEP"


def test_quorum_requires_matching_answers_from_distinct_resolvers():
    g = verifier(quorum=2)
    register(g, "r1")
    register(g, "r2")
    g.begin_query("example.com", "A", nonce="n1")
    g.submit("q-1", "r1", response(answers=[record(ttl=300)]))
    g.submit("q-1", "r2", response(answers=[record(ttl=100)], ad=True))
    out = g.resolve("q-1", now=10)
    assert out["votes"] == 2
    assert out["ttl"] == 100
    assert out["negative"] is False
    assert out["answers"][0]["data"] == "192.0.2.10"
    assert g.stats()["submissions"] == 2


def test_quorum_unmet_when_too_few_valid_votes():
    g = verifier(quorum=2)
    register(g, "r1")
    register(g, "r2")
    g.begin_query("example.com", "A", nonce="n1")
    g.submit("q-1", "r1", response(answers=[record()]))
    with pytest.raises(dv.DohVerifyError) as exc:
        g.resolve("q-1", now=10)
    assert exc.value.code == "DOH_QUORUM_UNMET"


def test_conflicting_answer_groups_are_refused():
    g = verifier(quorum=2)
    register(g, "r1")
    register(g, "r2")
    g.begin_query("example.com", "A", nonce="n1")
    g.submit("q-1", "r1", response(answers=[record(data="192.0.2.1")]))
    g.submit("q-1", "r2", response(answers=[record(data="192.0.2.2")]))
    with pytest.raises(dv.DohVerifyError) as exc:
        g.resolve("q-1", now=10)
    assert exc.value.code == "DOH_CONFLICT"


def test_duplicate_submission_from_one_resolver_counts_once():
    g = verifier(quorum=2)
    register(g, "r1")
    register(g, "r2")
    g.begin_query("example.com", "A", nonce="n1")
    first = g.submit("q-1", "r1", response(answers=[record()]))
    second = g.submit("q-1", "r1", response(answers=[record()]))
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    g.submit("q-1", "r2", response(answers=[record()]))
    out = g.resolve("q-1", now=10)
    assert out["votes"] == 2


def test_negative_answers_use_soa_ttl_and_servfail_is_refused():
    g = verifier(quorum=2)
    register(g, "r1")
    register(g, "r2")
    g.begin_query("missing.example", "A", nonce="n1")
    negative = response(name="missing.example", status=3, answers=[], soa_ttl=120)
    g.submit("q-1", "r1", negative)
    g.submit("q-1", "r2", negative)
    out = g.resolve("q-1", now=10)
    assert out["negative"] is True
    assert out["ttl"] == 120
    assert out["answers"] == []
    g.begin_query("broken.example", "A", nonce="n2")
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-2", "r1", response(name="broken.example", status=2))
    assert exc.value.code == "DOH_UPSTREAM_FAILURE"


def test_cache_get_honours_ttl_and_flush_counts_entries():
    g = verifier(quorum=1)
    register(g)
    g.begin_query("example.com", "A", nonce="n1")
    g.submit("q-1", "r1", response(answers=[record(ttl=300)]))
    g.resolve("q-1", now=10)
    hit = g.cache_get("EXAMPLE.com.", "a", now=20)
    assert hit is not None
    assert hit["ttl"] == 300
    assert hit["expires_at"] == 310
    assert g.cache_get("example.com", "A", now=311) is None
    assert g.flush(now=312) == 0

    g.begin_query("other.example", "AAAA", nonce="n2")
    g.submit("q-2", "r1", response(name="other.example", qtype="AAAA", answers=[]))
    g.resolve("q-2", now=20)
    assert g.flush(now=21) == 1


def test_snapshot_and_restore_replace_queries_and_cache():
    g = verifier(quorum=1)
    register(g)
    g.begin_query("example.com", "A", nonce="n1")
    g.submit("q-1", "r1", response(answers=[record()]))
    g.resolve("q-1", now=10)
    snap = g.snapshot()
    assert snap["stats"]["resolved"] == 1
    g.begin_query("later.example", "A", nonce="n2")
    g.submit("q-2", "r1", response(name="later.example", answers=[record("later.example")]))
    g.resolve("q-2", now=20)
    g.restore(snap)
    assert g.cache_get("later.example", "A", now=21) is None
    with pytest.raises(dv.DohVerifyError) as exc:
        g.submit("q-2", "r1", response(name="later.example"))
    assert exc.value.code == "DOH_QUERY_UNKNOWN"
    assert g.cache_get("example.com", "A", now=21) is not None
    with pytest.raises(dv.DohVerifyError) as exc:
        g.restore({"queries": "nope"})
    assert exc.value.code == "DOH_SNAPSHOT_INVALID"
    assert g.stats()["resolved"] == 1


def test_concurrent_resolution_writes_cache_once():
    g = verifier(quorum=2)
    register(g, "r1")
    register(g, "r2")
    g.begin_query("example.com", "A", nonce="n1")
    g.submit("q-1", "r1", response(answers=[record()]))
    g.submit("q-1", "r2", response(answers=[record()]))
    results = []
    lock = threading.Lock()

    def worker():
        try:
            out = g.resolve("q-1", now=10)
        except dv.DohVerifyError as exc:
            with lock:
                results.append(exc.code)
        else:
            with lock:
                results.append(out["answers"][0]["data"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ["192.0.2.10"] * 8
    assert g.stats()["cache_writes"] == 1
    assert g.stats()["resolved"] == 1
    assert g.cache_get("example.com", "A", now=11) is not None
