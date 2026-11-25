import pytest

import hop_auth_forwarder as hf

NOW = "2026-11-25T11:00:00+00:00"

HOPS = [
    {"name": "h1", "scheme": "Basic", "credential_ref": "ref-a"},
    {"name": "h2", "scheme": "Bearer", "credential_ref": "ref-b"},
]


def chain_two():
    fwd = hf.HopAuthForwarder()
    fwd.register_credential("ref-a", "secret-a")
    fwd.register_credential("ref-b", "secret-b")
    fwd.set_chain(HOPS)
    return fwd


def test_set_chain_reports_hops_in_order():
    fwd = chain_two()
    status = fwd.hop_status()
    assert [hop["name"] for hop in status] == ["h1", "h2"]
    assert [hop["scheme"] for hop in status] == ["Basic", "Bearer"]
    assert [hop["active"] for hop in status] == [True, False]
    assert all(hop["has_credential"] for hop in status)
    assert all(hop["status"] == "ready" for hop in status)


def test_set_chain_rejects_malformed_chains():
    fwd = hf.HopAuthForwarder()
    bad_chains = [
        None,
        [],
        [{"name": "h1", "scheme": "Basic"}],
        [{"name": "h1", "scheme": "Basic", "credential_ref": "r1"},
         {"name": "h1", "scheme": "Bearer", "credential_ref": "r2"}],
        "h1,h2",
    ]
    for bad in bad_chains:
        with pytest.raises(hf.HopAuthError) as exc:
            fwd.set_chain(bad)
        assert exc.value.code == "HOP_INVALID_REQUEST"


def test_forward_injects_next_hop_credential():
    fwd = chain_two()
    out = fwd.forward({"method": "GET", "target": "http://target.test/a",
                       "headers": {"X-Trace": "t-1"}, "body": "hello"})
    assert out["hop"] == "h1"
    assert out["status"] is None
    assert out["attempt"] == 1
    assert out["headers"]["Proxy-Authorization"] == "Basic secret-a"
    assert out["headers"]["X-Trace"] == "t-1"


def test_forward_without_chain_is_refused():
    fwd = hf.HopAuthForwarder()
    with pytest.raises(hf.HopAuthError) as exc:
        fwd.forward({"method": "GET", "target": "http://target.test/",
                     "headers": {}, "body": ""})
    assert exc.value.code == "HOP_CHAIN_EXHAUSTED"


def test_forward_rejects_unresolved_credential_reference():
    fwd = hf.HopAuthForwarder()
    fwd.set_chain(HOPS)
    with pytest.raises(hf.HopAuthError) as exc:
        fwd.forward({"method": "GET", "target": "http://target.test/",
                     "headers": {}, "body": ""})
    assert exc.value.code == "HOP_CREDENTIAL_MISSING"


def test_forward_connect_tunnel_gets_no_proxy_authorization():
    fwd = chain_two()
    out = fwd.forward({"method": "CONNECT", "target": "target.test:443",
                       "headers": {"Host": "target.test"}, "body": ""})
    assert out["headers"] == {"Host": "target.test"}


def test_forward_does_not_mutate_the_request():
    fwd = chain_two()
    request = {"method": "GET", "target": "http://target.test/",
               "headers": {"Proxy-Authorization": "Basic client-owned",
                           "X-Trace": "t-9"},
               "body": "payload"}
    before = {"method": request["method"], "target": request["target"],
              "headers": dict(request["headers"]), "body": request["body"]}
    out = fwd.forward(request)
    assert request == before
    assert out["headers"]["X-Trace"] == "t-9"


def test_authenticate_hop_counts_attempts_per_hop():
    fwd = chain_two()
    first = fwd.authenticate_hop("h1", now=NOW)
    second = fwd.authenticate_hop("h1", now=NOW)
    other = fwd.authenticate_hop("h2", now=NOW)
    assert first["attempts"] == 1
    assert second["attempts"] == 2
    assert other["attempts"] == 1
    assert fwd.stats()["auth_attempts"] == 3
    with pytest.raises(hf.HopAuthError) as exc:
        fwd.authenticate_hop("ghost")
    assert exc.value.code == "HOP_UNKNOWN_HOP"


def test_first_407_requests_one_auth_retry():
    fwd = chain_two()
    decision = fwd.mark_response("h1", 407, now=NOW)
    assert decision == {"action": "retry_auth", "hop": "h1", "attempts": 1}
    assert fwd.stats()["retries"] == 1
    assert fwd.hop_status()[0]["status"] == "ready"


def test_second_consecutive_407_moves_to_backup_hop():
    fwd = chain_two()
    fwd.mark_response("h1", 407, now=NOW)
    decision = fwd.mark_response("h1", 407, now=NOW)
    assert decision["action"] == "failover"
    assert decision["hop"] == "h1"
    assert decision["active"] == "h2"
    assert fwd.stats()["failovers"] == 1
    assert fwd.forward({"method": "GET", "target": "http://target.test/",
                        "headers": {}, "body": ""})["hop"] == "h2"


def test_explicit_failover_on_last_hop_is_refused():
    fwd = hf.HopAuthForwarder()
    fwd.register_credential("ref-a", "secret-a")
    fwd.set_chain([HOPS[0]])
    with pytest.raises(hf.HopAuthError) as exc:
        fwd.failover("h1")
    assert exc.value.code == "HOP_CHAIN_EXHAUSTED"


def test_hop_status_after_failover_marks_original_hop():
    fwd = chain_two()
    fwd.mark_response("h1", 407, now=NOW)
    fwd.mark_response("h1", 407, now=NOW)
    status = fwd.hop_status()
    assert [hop["name"] for hop in status] == ["h1", "h2"]
    assert [hop["status"] for hop in status] == ["failed", "ready"]
    assert [hop["active"] for hop in status] == [False, True]


def test_stats_track_forward_and_auth_outcomes():
    fwd = chain_two()
    fwd.forward({"method": "GET", "target": "http://target.test/",
                 "headers": {}, "body": ""})
    fwd.mark_response("h1", 407, now=NOW)
    fwd.mark_response("h1", 407, now=NOW)
    assert fwd.stats() == {"hops": 2, "forwards": 1, "auth_attempts": 0,
                           "auth_failures": 2, "failovers": 1, "retries": 1}


def test_snapshot_holds_references_not_secrets():
    fwd = chain_two()
    state = fwd.snapshot()
    assert state["version"] == 1
    assert "secret-a" not in repr(state)
    assert "ref-a" in repr(state)
    clone = hf.HopAuthForwarder()
    clone.register_credential("ref-a", "secret-a")
    clone.register_credential("ref-b", "secret-b")
    clone.restore(state)
    out = clone.forward({"method": "GET", "target": "http://target.test/",
                         "headers": {}, "body": ""})
    assert out["headers"]["Proxy-Authorization"] == "Basic secret-a"
    bare = hf.HopAuthForwarder()
    bare.restore(state)
    with pytest.raises(hf.HopAuthError) as exc:
        bare.forward({"method": "GET", "target": "http://target.test/",
                      "headers": {}, "body": ""})
    assert exc.value.code == "HOP_CREDENTIAL_MISSING"


def test_restore_rejects_malformed_state_without_touching_chain():
    fwd = chain_two()
    for bad in (None, {}, {"version": 2}, {"version": 1, "chain": "h1"}):
        with pytest.raises(hf.HopAuthError) as exc:
            fwd.restore(bad)
        assert exc.value.code == "HOP_SNAPSHOT_INVALID"
    out = fwd.forward({"method": "GET", "target": "http://target.test/",
                       "headers": {}, "body": ""})
    assert out["hop"] == "h1"
