import copy
import threading

import pytest

import codec_capability_matcher as cm


def matcher(**kwargs):
    return cm.CodecCapabilityMatcher(**kwargs)


def cap(codec="h264", profile="high", level=41, width=1920, height=1080,
        fps=60, packaging="annexb"):
    return {"codec": codec, "profile": profile, "level": level,
            "width": width, "height": height, "fps": fps,
            "packaging": packaging}


def test_register_reports_revision_and_sorted_ids():
    m = matcher()
    out = m.register_endpoint("rx-b", [cap()])
    assert out["endpoint_id"] == "rx-b"
    assert out["caps"] == 1
    assert out["revision"] == 1

    second = m.register_endpoint("rx-b", [cap(level=40, fps=30)])
    assert second["revision"] == 2

    m.register_endpoint("tx-a", [cap()])
    assert m.endpoints() == ["rx-b", "tx-a"]
    assert m.stats() == {"endpoints": 2, "registrations": 3, "matches": 0,
                         "rejections": 0, "negotiations": 0, "cache_hits": 0}


def test_register_rejects_invalid_capability():
    m = matcher()
    missing_key = {key: value for key, value in cap().items() if key != "fps"}
    bad_entries = [
        missing_key,
        cap(codec="vp9"),
        cap(profile="ultra"),
        cap(level=99),
        cap(width=0),
        cap(packaging="raw"),
        [],
        "h264",
    ]
    for entry in bad_entries:
        caps = entry if isinstance(entry, list) else [entry]
        with pytest.raises(cm.CodecError) as exc:
            m.register_endpoint("bad", caps)
        assert exc.value.code == "CODEC_CAP_INVALID"
    assert m.endpoints() == []


def test_register_capacity_and_endpoint_id_validation():
    m = matcher(max_caps_per_endpoint=2)
    m.register_endpoint("ok", [cap(), cap(level=40, fps=30)])

    three = [cap(), cap(level=40, fps=30),
             cap(level=51, width=3840, height=2160, fps=30)]
    with pytest.raises(cm.CodecError) as full:
        m.register_endpoint("ok", three)
    assert full.value.code == "CODEC_CAPACITY_EXCEEDED"

    with pytest.raises(cm.CodecError) as empty:
        m.register_endpoint("", [cap()])
    assert empty.value.code == "CODEC_ARGUMENT_INVALID"

    with pytest.raises(cm.CodecError) as not_text:
        m.register_endpoint(7, [cap()])
    assert not_text.value.code == "CODEC_ARGUMENT_INVALID"
    assert m.endpoints() == ["ok"]


def test_match_identical_capability():
    m = matcher()
    m.register_endpoint("tx", [cap()])
    m.register_endpoint("rx", [cap()])

    out = m.match("tx", "rx")
    assert out["status"] == "matched"
    assert out["reasons"] == []
    assert out["selected"]["profile"] == "high"
    assert out["selected"]["level"] == 41
    assert out["selected"]["required_level"] == 41
    assert m.stats()["matches"] == 1


def test_match_reports_level_too_low():
    m = matcher()
    m.register_endpoint("tx", [cap(level=41, fps=60)])
    m.register_endpoint("rx", [cap(level=40, fps=60)])

    out = m.match("tx", "rx")
    assert out["status"] == "rejected"
    assert out["selected"] is None
    assert out["reasons"] == ["LEVEL_TOO_LOW"]
    assert m.stats()["rejections"] == 1


def test_match_reports_codec_and_packaging_mismatch():
    m = matcher()
    m.register_endpoint("tx", [cap(codec="h264")])
    m.register_endpoint("rx-hevc", [cap(codec="hevc")])
    m.register_endpoint("rx-avcc", [cap(packaging="avcc")])

    codec_out = m.match("tx", "rx-hevc")
    assert codec_out["status"] == "rejected"
    assert codec_out["reasons"] == ["CODEC_MISMATCH"]

    pack_out = m.match("tx", "rx-avcc")
    assert pack_out["status"] == "rejected"
    assert pack_out["reasons"] == ["PACKAGING_MISMATCH"]


def test_match_and_chain_reject_unknown_endpoint():
    m = matcher()
    m.register_endpoint("tx", [cap()])

    calls = [
        lambda: m.match("tx", "ghost"),
        lambda: m.match("ghost", "tx"),
        lambda: m.fallback_chain("ghost", "tx"),
        lambda: m.explain("tx", "ghost"),
    ]
    for call in calls:
        with pytest.raises(cm.CodecError) as exc:
            call()
        assert exc.value.code == "CODEC_ENDPOINT_MISSING"


def test_explain_reports_required_level_and_reasons():
    m = matcher()
    m.register_endpoint("tx-hd", [cap(level=41, fps=60)])
    m.register_endpoint("tx-uhd", [cap(level=51, width=3840, height=2160, fps=30)])
    m.register_endpoint("rx", [cap(level=41, fps=60)])

    hd = m.explain("tx-hd", "rx")
    assert hd["required_level"] == 41
    assert hd["receiver_max_level"] == 41
    assert hd["reasons"] == []

    uhd = m.explain("tx-uhd", "rx")
    assert uhd["required_level"] == 51
    assert uhd["receiver_max_level"] == 41
    assert "LEVEL_TOO_LOW" in uhd["reasons"]


def test_fallback_chain_orders_profiles_then_levels():
    m = matcher()
    m.register_endpoint("tx", [
        cap(profile="main", level=40, fps=30),
        cap(profile="high", level=41, fps=60),
        cap(profile="high", level=40, fps=30),
    ])
    m.register_endpoint("rx", [
        cap(profile="high", level=40, fps=30),
        cap(profile="main", level=41, fps=60),
    ])

    chain = m.fallback_chain("tx", "rx")
    assert [(item["profile"], item["level"], item["usable"]) for item in chain] == [
        ("high", 41, False),
        ("high", 40, True),
        ("main", 40, True),
    ]


def test_negotiate_picks_first_usable_candidate():
    m = matcher()
    m.register_endpoint("tx", [
        cap(profile="main", level=40, fps=30),
        cap(profile="high", level=41, fps=60),
        cap(profile="high", level=40, fps=30),
    ])
    m.register_endpoint("rx", [
        cap(profile="high", level=40, fps=30),
        cap(profile="main", level=41, fps=60),
    ])

    out = m.negotiate("sess-1", "tx", "rx")
    assert out["status"] == "matched"
    assert out["selected"]["profile"] == "high"
    assert out["selected"]["level"] == 40
    assert out["selected"]["required_level"] == 40
    assert out["generation"] == 1
    assert out["cached"] is False


def test_negotiate_caches_unchanged_endpoints():
    m = matcher()
    m.register_endpoint("tx", [cap()])
    m.register_endpoint("rx", [cap()])

    first = m.negotiate("sess", "tx", "rx")
    second = m.negotiate("sess", "tx", "rx")
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["generation"] == 1
    assert second["selected"] == first["selected"]
    assert m.stats()["cache_hits"] == 1


def test_negotiate_recomputes_after_reregistration():
    m = matcher()
    m.register_endpoint("tx", [cap(profile="high", level=41, fps=60),
                               cap(profile="main", level=40, fps=30)])
    m.register_endpoint("rx", [cap(profile="high", level=40, fps=30),
                               cap(profile="main", level=40, fps=30)])

    first = m.negotiate("sess", "tx", "rx")
    assert (first["selected"]["profile"], first["selected"]["level"]) == ("high", 40)

    m.register_endpoint("rx", [cap(profile="main", level=40, fps=30)])
    second = m.negotiate("sess", "tx", "rx")
    assert (second["selected"]["profile"], second["selected"]["level"]) == ("main", 40)
    assert second["generation"] == 2


def test_session_result_and_keyframe_generation():
    m = matcher()
    m.register_endpoint("tx", [cap()])
    m.register_endpoint("rx", [cap()])

    out = m.negotiate("sess", "tx", "rx")
    pinned = m.session_result("sess")
    assert pinned["generation"] == 1
    assert pinned["selected"] == out["selected"]

    again = m.mark_keyframe("sess")
    assert again["generation"] == 1
    assert again["selected"] == out["selected"]

    with pytest.raises(cm.CodecError) as exc:
        m.session_result("ghost")
    assert exc.value.code == "CODEC_SESSION_MISSING"


def test_snapshot_is_detached_and_restore_roundtrips():
    m = matcher()
    m.register_endpoint("tx", [cap(profile="high", level=41, fps=60),
                               cap(profile="main", level=40, fps=30)])
    m.register_endpoint("rx", [cap()])
    m.negotiate("sess", "tx", "rx")

    state = m.snapshot()
    pristine = copy.deepcopy(state)
    m.register_endpoint("rx", [cap(profile="main", level=40, fps=30)])
    assert m.match("tx", "rx")["selected"]["profile"] == "main"

    clone = matcher()
    clone.restore(pristine)
    assert clone.endpoints() == ["rx", "tx"]
    assert clone.match("tx", "rx")["selected"]["profile"] == "high"
    assert clone.session_result("sess")["selected"]["profile"] == "high"
    clone.register_endpoint("rx", [cap(profile="main", level=40, fps=30)])
    assert clone.negotiate("sess-2", "tx", "rx")["selected"]["profile"] == "main"

    for bad in (None, {}, [], "state"):
        with pytest.raises(cm.CodecError) as exc:
            clone.restore(bad)
        assert exc.value.code == "CODEC_SNAPSHOT_INVALID"
    assert clone.match("tx", "rx")["selected"]["profile"] == "main"


def test_concurrent_registrations_keep_every_endpoint():
    m = matcher()
    ids = [f"ep{index:02d}" for index in range(6)]
    errors = []
    barrier = threading.Barrier(len(ids))

    def worker(endpoint_id):
        try:
            barrier.wait(timeout=5)
            m.register_endpoint(endpoint_id, [cap()])
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(endpoint_id,))
               for endpoint_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert m.endpoints() == ids
    assert m.stats()["endpoints"] == 6
    assert m.stats()["registrations"] == 6
