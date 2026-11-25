import pytest

import container_escape_guard as cg


def guard(**kwargs):
    return cg.ContainerEscapeGuard(**kwargs)


def test_normalize_path_collapses_segments_and_refuses_escapes():
    g = guard()
    assert g.normalize_path("/var//log/./app/") == "/var/log/app"
    assert g.normalize_path("/") == "/"
    for bad in ("", "   ", "var/log", "/var/../../etc", "./app"):
        with pytest.raises(cg.ContainerEscapeError) as exc:
            g.normalize_path(bad)
        assert exc.value.code == "ESC_PATH_INVALID"


def test_classify_mount_identifies_control_sockets_and_host_root():
    g = guard()
    assert g.classify_mount("/var/run/docker.sock", "/run/docker.sock") == "control"
    assert g.classify_mount("/run/containerd/containerd.sock", "/run/c.sock") == "control"
    assert g.classify_mount("/", "/host") == "host_root"
    assert g.classify_mount("/proc", "/host/proc") == "pseudo_fs"
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.classify_mount("relative", "/data")
    assert exc.value.code == "ESC_PATH_INVALID"


def test_classify_mount_marks_sensitive_host_paths():
    g = guard()
    assert g.classify_mount("/etc", "/host/etc", read_only=True) == "sensitive"
    assert g.classify_mount("/root/.ssh", "/keys", read_only=True) == "sensitive"
    assert g.classify_mount("/srv/data", "/data") == "safe"
    assert g.classify_mount("/srv/data", "/proc/data") == "pseudo_fs"


def test_check_capabilities_computes_effective_and_dangerous_sets():
    g = guard()
    plain = g.check_capabilities([], [])
    assert "CAP_CHOWN" in plain["effective"]
    assert plain["dangerous"] == ()
    assert plain["allowed"] is True
    out = g.check_capabilities(["SYS_ADMIN", "net_bind_service"], ["chown"])
    assert "CAP_SYS_ADMIN" in out["effective"]
    assert "CAP_NET_BIND_SERVICE" in out["effective"]
    assert "CAP_CHOWN" not in out["effective"]
    assert list(out["effective"]) == sorted(out["effective"])
    assert out["dangerous"] == ("CAP_SYS_ADMIN",)
    assert out["allowed"] is False


def test_check_capabilities_drop_all_and_reject_unknown_or_conflicting():
    g = guard()
    out = g.check_capabilities(["net_bind_service"], ["ALL"])
    assert out["effective"] == ("CAP_NET_BIND_SERVICE",)
    assert out["allowed"] is True
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.check_capabilities(["CAP_WARP_DRIVE"], [])
    assert exc.value.code == "ESC_CAP_UNKNOWN"
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.check_capabilities(["chown"], ["CAP_CHOWN"])
    assert exc.value.code == "ESC_CAP_CONFLICT"


def test_namespace_plan_scores_single_shared_namespace():
    g = guard()
    pid = g.namespace_plan({"host_pid": True})
    assert pid["shared"] == ("pid",)
    assert pid["score"] == 3
    net = g.namespace_plan({"host_network": True})
    assert net["shared"] == ("network",)
    assert net["score"] == 2
    empty = g.namespace_plan({})
    assert empty["shared"] == ()
    assert empty["score"] == 0


def test_evaluate_allows_clean_spec_and_counts_it():
    g = guard()
    spec = {"id": "web-1", "image": "nginx:1.27", "mounts": [
        {"source": "/srv/data", "target": "/data", "read_only": True},
    ], "cap_drop": ["ALL"], "cap_add": ["net_bind_service"]}
    out = g.evaluate(spec, now=10)
    assert out["key"] == "web-1"
    assert out["verdict"] == "allow"
    assert out["findings"] == []
    assert out["quarantined"] is False
    assert out["until"] == 0
    assert g.stats()["allowed"] == 1
    assert g.stats()["evaluations"] == 1
    assert g.findings() == []


def test_evaluate_denies_host_root_mount():
    g = guard()
    out = g.evaluate({"id": "c1", "mounts": [
        {"source": "/", "target": "/host", "read_only": False},
    ]})
    assert out["verdict"] == "deny"
    assert out["findings"][0]["code"] == "ESC_MOUNT_HOST_ROOT"
    assert out["findings"][0]["severity"] == "critical"
    assert out["findings"][0]["target"] == "/host"
    assert "detail" in out["findings"][0]
    assert g.stats()["denied"] == 1


def test_evaluate_denies_dangerous_capability():
    g = guard()
    out = g.evaluate({"id": "c2", "cap_add": ["SYS_PTRACE"]})
    assert out["verdict"] == "deny"
    cap_findings = [f for f in out["findings"] if f["code"] == "ESC_CAP_DANGEROUS"]
    assert len(cap_findings) == 1
    assert cap_findings[0]["severity"] == "critical"
    assert cap_findings[0]["target"] == ""
    assert "detail" in cap_findings[0]


def test_evaluate_reviews_sensitive_mount_without_denying():
    g = guard()
    out = g.evaluate({"id": "c3", "mounts": [
        {"source": "/etc/passwd", "target": "/etc/passwd", "read_only": True},
    ]})
    assert out["verdict"] == "review"
    assert out["findings"][0]["code"] == "ESC_MOUNT_SENSITIVE"
    assert out["findings"][0]["severity"] == "medium"
    assert g.stats()["reviewed"] == 1
    assert g.stats()["denied"] == 0


def test_evaluate_enforces_mount_count_capacity():
    g = guard(max_mounts=2)
    spec = {"id": "c4", "mounts": [
        {"source": "/srv/a", "target": "/a"},
        {"source": "/srv/b", "target": "/b"},
        {"source": "/srv/c", "target": "/c"},
    ]}
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.evaluate(spec)
    assert exc.value.code == "ESC_CAPACITY_EXCEEDED"
    assert exc.value.detail["mounts"] == 3
    assert g.stats()["evaluations"] == 0


def test_findings_are_deterministic_and_ordered_by_severity():
    g = guard()
    spec = {"id": "c5", "host_pid": True, "mounts": [
        {"source": "/var/run/docker.sock", "target": "/s.sock"},
        {"source": "/etc/shadow", "target": "/shadow", "read_only": True},
    ]}
    out = g.evaluate(spec, now=1)
    codes = [f["code"] for f in out["findings"]]
    assert codes == ["ESC_HOST_PID", "ESC_MOUNT_CONTROL_SOCKET", "ESC_MOUNT_SENSITIVE"]
    assert out["verdict"] == "deny"
    again = g.evaluate(spec, now=2)
    assert again["findings"] == out["findings"]
    assert [f["code"] for f in g.findings()] == codes


def test_repeated_denials_quarantine_key_until_release():
    g = guard(quarantine_after=2, quarantine_ttl=100)
    spec = {"id": "c6", "mounts": [{"source": "/", "target": "/host"}]}
    first = g.evaluate(spec, now=0)
    assert first["verdict"] == "deny"
    assert first["quarantined"] is False
    second = g.evaluate(spec, now=1)
    assert second["verdict"] == "deny"
    assert second["quarantined"] is True
    third = g.evaluate(spec, now=2)
    assert third["verdict"] == "quarantined"
    assert third["findings"] == []
    assert third["until"] == 101
    assert g.stats()["quarantined_hits"] == 1
    assert g.quarantine_state("c6", now=2)["quarantined"] is True
    assert g.release("c6") is True
    assert g.release("c6") is False
    after = g.evaluate({"id": "c6"}, now=3)
    assert after["verdict"] == "allow"


def test_quarantine_expires_at_the_boundary():
    g = guard(quarantine_after=1, quarantine_ttl=50)
    spec = {"id": "c7", "mounts": [{"source": "/", "target": "/host"}]}
    assert g.evaluate(spec, now=100)["quarantined"] is True
    assert g.evaluate(spec, now=149)["verdict"] == "quarantined"
    out = g.evaluate(spec, now=150)
    assert out["verdict"] == "deny"
    assert out["quarantined"] is True


def test_profiles_merge_defaults_and_snapshot_restores_state():
    g = guard(max_profiles=1)
    created = g.register_profile("baseline", {
        "cap_drop": ["ALL"],
        "mounts": [{"source": "/srv/app", "target": "/app", "read_only": True}],
    })
    assert created["name"] == "baseline"
    assert len(created["fingerprint"]) == 16
    same = g.register_profile("baseline", {
        "cap_drop": ["ALL"],
        "mounts": [{"source": "/srv/app", "target": "/app", "read_only": True}],
    })
    assert same["fingerprint"] == created["fingerprint"]
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.register_profile("second", {})
    assert exc.value.code == "ESC_CAPACITY_EXCEEDED"
    out = g.evaluate({"id": "c8", "profile": "baseline"})
    assert out["verdict"] == "allow"
    snap = g.snapshot()
    g.evaluate({"id": "c9", "mounts": [{"source": "/", "target": "/host"}]}, now=1)
    assert g.stats()["evaluations"] == 2
    g.restore(snap)
    assert g.stats()["evaluations"] == 1
    assert [p["name"] for p in g.profiles()] == ["baseline"]
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.restore({"profiles": 3})
    assert exc.value.code == "ESC_SNAPSHOT_INVALID"
    assert g.stats()["evaluations"] == 1
    with pytest.raises(cg.ContainerEscapeError) as exc:
        g.evaluate({"id": "c10", "profile": "missing"})
    assert exc.value.code == "ESC_PROFILE_UNKNOWN"
