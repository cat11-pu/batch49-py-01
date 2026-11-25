import pytest

import grpc_metadata_gate as gg

METHOD = "/orders.OrderService/Get"


def verifier(token, audience):
    if token == "good-token" and audience == "orders":
        return {"sub": "svc-a", "role": "user"}
    if token == "guest-token" and audience == "orders":
        return {"sub": "svc-guest", "role": "guest"}
    return None


def make_gate(**kwargs):
    params = dict(max_pairs=8, max_methods=4)
    params.update(kwargs)
    g = gg.GrpcMetadataGate(verifier, **params)
    g.register_method(METHOD, audiences=("orders",), roles=("user", "admin"))
    return g


def meta(token="good-token", timeout="1500m", **extra):
    pairs = [("authorization", "Bearer " + token), ("grpc-timeout", timeout)]
    pairs.extend(extra.items())
    return pairs


def test_sanitize_normalizes_keys_and_merges_repeated_values():
    g = make_gate()
    info = g.sanitize([("X-Request-ID", "r1"), ("X-Trace", "a"), ("x-trace", "b")])
    assert info["pairs"] == {"x-request-id": "r1", "x-trace": ("a", "b")}
    assert info["binary"] == {}
    assert info["stripped"] == []
    assert info["count"] == 3


def test_sanitize_refuses_control_characters_and_bad_keys():
    g = make_gate()
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.sanitize([("x-a", "line\r\nInjected: 1")])
    assert exc.value.code == "GRPC_METADATA_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.sanitize([("x-a", "nul\x00byte")])
    assert exc.value.code == "GRPC_METADATA_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.sanitize([("bad key", "1")])
    assert exc.value.code == "GRPC_METADATA_INVALID"


def test_sanitize_refuses_duplicate_single_valued_keys():
    g = make_gate()
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.sanitize([("authorization", "Bearer a"), ("Authorization", "Bearer b")])
    assert exc.value.code == "GRPC_METADATA_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.sanitize([("x-request-id", "a"), ("x-request-id", "b")])
    assert exc.value.code == "GRPC_METADATA_INVALID"
    info = g.sanitize([("x-multi", "a"), ("x-multi", "b")])
    assert info["pairs"]["x-multi"] == ("a", "b")


def test_sanitize_enforces_byte_limits_on_keys_values_and_pair_count():
    small = make_gate(max_pairs=3, max_key_bytes=8, max_value_bytes=8)
    with pytest.raises(gg.GrpcMetadataError) as exc:
        small.sanitize([("x-abcdefghij", "1")])
    assert exc.value.code == "GRPC_METADATA_LIMIT"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        small.sanitize([("x-a", "123456789")])
    assert exc.value.code == "GRPC_METADATA_LIMIT"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        small.sanitize([("x-a", "1"), ("x-b", "2"), ("x-c", "3"), ("x-d", "4")])
    assert exc.value.code == "GRPC_METADATA_LIMIT"
    assert small.sanitize([("x-a", "12345678")])["pairs"] == {"x-a": "12345678"}


def test_sanitize_strips_pseudo_headers_and_grpc_prefixed_keys():
    g = make_gate()
    info = g.sanitize([(":authority", "api"), ("grpc-encoding", "gzip"),
                       ("grpc-timeout", "5S"), ("x-ok", "1")])
    assert info["stripped"] == [":authority", "grpc-encoding"]
    assert info["pairs"] == {"grpc-timeout": "5S", "x-ok": "1"}
    assert info["count"] == 4


def test_register_method_validates_shape_and_duplicates():
    g = gg.GrpcMetadataGate(verifier, max_methods=2)
    out = g.register_method(METHOD, audiences=("orders",), roles=("user",),
                            max_deadline_ms=1000)
    assert out["method"] == METHOD
    assert out["audiences"] == ("orders",)
    assert out["roles"] == ("user",)
    assert out["max_deadline_ms"] == 1000
    assert g.methods() == [METHOD]
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.register_method(METHOD, audiences=("orders",))
    assert exc.value.code == "GRPC_METHOD_EXISTS"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.register_method("pkg.Svc/Get", audiences=("pkg",))
    assert exc.value.code == "GRPC_METHOD_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.register_method("/pkg/Svc/Extra", audiences=("pkg",))
    assert exc.value.code == "GRPC_METHOD_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.register_method("/pkg/Svc/Get", audiences=())
    assert exc.value.code == "GRPC_METHOD_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.register_method("/pkg/Svc/Other", audiences=("pkg",), roles=())
    assert exc.value.code == "GRPC_METHOD_INVALID"


def test_authorize_returns_claims_deadline_and_outbound():
    g = make_gate()
    out = g.authorize(METHOD, meta(**{"x-request-id": "req-1"}), peer="edge-1", now=10)
    assert out["method"] == METHOD
    assert out["subject"] == "svc-a"
    assert out["audience"] == "orders"
    assert out["role"] == "user"
    assert out["deadline_ms"] == 1500
    assert out["clamped"] is False
    assert out["stripped"] == []
    assert out["outbound"] == {"x-request-id": "req-1"}
    assert out["peer"] == "edge-1"


def test_authorize_clamps_deadline_to_method_policy():
    g = make_gate()
    out = g.authorize(METHOD, meta(timeout="99S"), peer="edge-1")
    assert out["deadline_ms"] == 30000
    assert out["clamped"] is True


def test_authorize_refuses_missing_or_malformed_deadline():
    g = make_gate()
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize(METHOD, [("authorization", "Bearer good-token")], peer="p")
    assert exc.value.code == "GRPC_DEADLINE_MISSING"
    for value in ("12x", "0S", "-5S"):
        with pytest.raises(gg.GrpcMetadataError) as exc:
            g.authorize(METHOD, meta(timeout=value), peer="p")
        assert exc.value.code == "GRPC_DEADLINE_INVALID"


def test_authorize_requires_a_bearer_credential():
    g = make_gate()
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize(METHOD, [("grpc-timeout", "5S")], peer="p")
    assert exc.value.code == "GRPC_UNAUTHENTICATED"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize(METHOD, [("authorization", "Basic abc"), ("grpc-timeout", "5S")], peer="p")
    assert exc.value.code == "GRPC_UNAUTHENTICATED"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize(METHOD, [("authorization", "Bearer "), ("grpc-timeout", "5S")], peer="p")
    assert exc.value.code == "GRPC_UNAUTHENTICATED"


def test_authorize_refuses_bad_tokens_and_wrong_roles():
    g = make_gate()
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize(METHOD, meta(token="bad-token"), peer="p")
    assert exc.value.code == "GRPC_TOKEN_INVALID"
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize(METHOD, meta(token="guest-token"), peer="p")
    assert exc.value.code == "GRPC_ROLE_DENIED"
    assert g.stats()["denied"] == 2


def test_untrusted_peers_cannot_pass_internal_headers():
    g = make_gate()
    untrusted = g.authorize(METHOD, meta(**{"x-internal-sig": "forged"}), peer="edge-1")
    assert untrusted["stripped"] == ["x-internal-sig"]
    assert untrusted["outbound"] == {}
    assert g.stats()["spoofed"] == 1
    trusted_gate = make_gate(trusted_peers=("edge-1",))
    trusted = trusted_gate.authorize(METHOD, meta(**{"x-internal-sig": "real"}), peer="edge-1")
    assert trusted["stripped"] == []
    assert "x-internal-sig" not in trusted["outbound"]
    assert trusted_gate.stats()["spoofed"] == 0


def test_outbound_keeps_only_whitelisted_keys():
    g = make_gate(trusted_peers=("edge-1",))
    out = g.authorize(METHOD, meta(**{"traceparent": "00-abc-def-01",
                                     "x-internal-tenant": "t1",
                                     "x-tenant": "t-9",
                                     "cookie": "a=b"}), peer="edge-1")
    assert out["outbound"] == {"traceparent": "00-abc-def-01", "x-tenant": "t-9"}


def test_unknown_method_and_method_capacity():
    g = make_gate(max_methods=2)
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.authorize("/orders.OrderService/Missing", meta(), peer="p")
    assert exc.value.code == "GRPC_METHOD_UNKNOWN"
    g.register_method("/orders.OrderService/List", audiences=("orders",))
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.register_method("/orders.OrderService/Extra", audiences=("orders",))
    assert exc.value.code == "GRPC_CAPACITY_EXCEEDED"
    assert g.methods() == [METHOD, "/orders.OrderService/List"]


def test_snapshot_restore_roundtrip_and_rejection():
    g = make_gate()
    g.authorize(METHOD, meta(), peer="p")
    snap = g.snapshot()
    assert snap["stats"]["calls"] == 1
    assert g.revoke_method(METHOD) is True
    assert g.revoke_method(METHOD) is False
    assert g.methods() == []
    g.restore(snap)
    assert g.methods() == [METHOD]
    assert g.stats()["calls"] == 1
    with pytest.raises(gg.GrpcMetadataError) as exc:
        g.restore({"methods": 5})
    assert exc.value.code == "GRPC_SNAPSHOT_INVALID"
    assert g.methods() == [METHOD]
