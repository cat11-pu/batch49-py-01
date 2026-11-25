import pytest

import sbom_runtime_reconciler as sr


def reconciler(**kwargs):
    return sr.SbomRuntimeReconciler(**kwargs)


def component(name, version):
    return sr.Component(name=name, version=version)


def loaded(soname, version, path, pid=101, service="api"):
    return sr.LoadedLibrary(
        soname=soname, version=version, path=path, pid=pid, service=service
    )


def test_normalize_key_and_version():
    instance = reconciler()
    assert instance.normalize_key("/usr/lib/x86_64-linux-gnu/libcrypto.so.3") == "libcrypto"
    assert instance.normalize_key("  LibCrypto.SO  ") == "libcrypto"
    assert instance.normalize_key("zlib") == "zlib"
    assert instance.normalize_version("v3.0.13+deb12u1") == "3.0.13"
    assert instance.normalize_version(" 3.0.13 ") == "3.0.13"
    assert instance.normalize_version(None) == ""


def test_load_counts_and_empty_summary():
    instance = reconciler()
    assert instance.load_sbom([component("libpng", "1.6.40")]) == 1
    assert instance.load_runtime(
        [loaded("libpng.so.1", "1.6.40", "/usr/lib/libpng.so.1")]
    ) == 1
    assert instance.findings() == []
    assert instance.summary() == {
        "sbom_components": 1,
        "runtime_libraries": 1,
        "undeclared_runtime": 0,
        "unused_declaration": 0,
        "version_mismatch": 0,
        "equivalence_groups": 0,
    }


def test_clean_match_yields_no_findings():
    instance = reconciler()
    instance.load_sbom([component("libcrypto", "3.0.13")])
    instance.load_runtime(
        [loaded("libcrypto.so.3", "3.0.13+deb12u1", "/usr/lib/libcrypto.so.3")]
    )
    assert instance.reconcile() == []
    assert instance.findings() == []
    assert instance.summary()["version_mismatch"] == 0


def test_undeclared_runtime_library_is_reported():
    instance = reconciler()
    instance.load_sbom([])
    instance.load_runtime(
        [loaded("libfoo.so.1", "1.0", "/usr/lib/libfoo.so.1", pid=77, service="worker")]
    )
    findings = instance.reconcile()
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "undeclared_runtime"
    assert finding.component == "libfoo"
    assert finding.evidence == ("/usr/lib/libfoo.so.1",)
    assert finding.services == ("worker",)
    assert instance.evidence_for("libfoo") == ["/usr/lib/libfoo.so.1"]


def test_unused_declaration_is_reported():
    instance = reconciler()
    instance.load_sbom([component("libpng", "1.6.40")])
    instance.load_runtime([])
    findings = instance.reconcile()
    assert [(item.kind, item.component, item.evidence) for item in findings] == [
        ("unused_declaration", "libpng", ("libpng@1.6.40",))
    ]


def test_version_mismatch_reports_both_versions():
    instance = reconciler()
    instance.load_sbom([component("libpng", "1.6.40")])
    instance.load_runtime(
        [loaded("libpng.so.1", "1.6.41", "/usr/lib/libpng.so.1", pid=9)]
    )
    findings = instance.reconcile()
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "version_mismatch"
    assert finding.component == "libpng"
    assert "1.6.40" in finding.detail
    assert "1.6.41" in finding.detail
    assert finding.evidence == ("/usr/lib/libpng.so.1",)
    assert finding.pids == (9,)


def test_findings_are_sorted_by_kind_then_component():
    instance = reconciler()
    instance.load_sbom([component("zzz", "1.0"), component("alpha", "2.0")])
    instance.load_runtime([loaded("libmid.so", "1.0", "/lib/libmid.so", pid=1)])
    findings = instance.reconcile()
    assert [(item.kind, item.component) for item in findings] == [
        ("undeclared_runtime", "libmid"),
        ("unused_declaration", "alpha"),
        ("unused_declaration", "zzz"),
    ]
    assert [item.component for item in instance.findings("unused_declaration")] == [
        "alpha",
        "zzz",
    ]
    assert instance.findings("version_mismatch") == []


def test_reconcile_is_idempotent():
    instance = reconciler()
    instance.load_sbom([component("libpng", "1.6.40")])
    instance.load_runtime([loaded("libmid.so", "1.0", "/lib/libmid.so", pid=1)])
    first = instance.reconcile()
    second = instance.reconcile()
    assert [(item.kind, item.component) for item in second] == [
        (item.kind, item.component) for item in first
    ]
    assert len(instance.findings()) == 2
    assert instance.summary()["unused_declaration"] == 1
    assert instance.summary()["undeclared_runtime"] == 1


def test_equivalence_group_matches_alternative_names():
    instance = reconciler()
    instance.load_sbom([component("openssl", "3.0.13")])
    instance.load_runtime(
        [loaded("libssl.so.3", "3.0.13", "/usr/lib/libssl.so.3", pid=11)]
    )
    instance.add_equivalence_group(["openssl", "libssl"])
    assert instance.reconcile() == []
    assert instance.summary()["equivalence_groups"] == 1


def test_equivalence_group_registration_is_idempotent():
    instance = reconciler()
    instance.add_equivalence_group(["openssl", "libssl"])
    instance.add_equivalence_group(["libssl", "openssl"])
    assert instance.summary()["equivalence_groups"] == 1


def test_runtime_capacity_limit_rejects_oversized_load():
    instance = reconciler(max_libraries=2)
    instance.load_runtime([loaded("a.so", "1.0", "/lib/a.so", pid=1)])
    with pytest.raises(sr.ReconcilerError) as exc:
        instance.load_runtime(
            [
                loaded("b.so", "1.0", "/lib/b.so", pid=2),
                loaded("c.so", "1.0", "/lib/c.so", pid=3),
            ]
        )
    assert exc.value.code == "RUNTIME_CAPACITY_EXCEEDED"
    assert instance.summary()["runtime_libraries"] == 1


def test_snapshot_detached_and_restore_reproduces_findings():
    instance = reconciler()
    instance.load_sbom([component("libpng", "1.6.40")])
    instance.load_runtime([loaded("libmid.so", "1.0", "/lib/libmid.so", pid=1)])
    instance.reconcile()
    state = instance.snapshot()
    assert isinstance(state, dict) and state

    instance.reset()
    assert instance.findings() == []

    clone = reconciler()
    clone.restore(state)
    assert [(item.kind, item.component) for item in clone.findings()] == [
        ("undeclared_runtime", "libmid"),
        ("unused_declaration", "libpng"),
    ]
    assert clone.summary()["sbom_components"] == 1
    assert clone.summary()["runtime_libraries"] == 1


def test_restore_rejects_malformed_state_without_touching_live_data():
    instance = reconciler()
    instance.load_sbom([component("libpng", "1.6.40")])
    instance.reconcile()
    for bad in (None, {}, {"components": "nope"}):
        with pytest.raises(sr.ReconcilerError) as exc:
            instance.restore(bad)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert instance.summary()["sbom_components"] == 1
    assert [item.component for item in instance.findings()] == ["libpng"]


def test_reset_clears_all_state():
    instance = reconciler()
    instance.load_sbom([component("libpng", "1.6.40")])
    instance.load_runtime([loaded("libmid.so", "1.0", "/lib/libmid.so", pid=1)])
    instance.reconcile()
    instance.reset()
    assert instance.findings() == []
    assert instance.reconcile() == []
    assert instance.summary() == {
        "sbom_components": 0,
        "runtime_libraries": 0,
        "undeclared_runtime": 0,
        "unused_declaration": 0,
        "version_mismatch": 0,
        "equivalence_groups": 0,
    }


def test_same_library_in_two_processes_counts_once():
    instance = reconciler()
    instance.load_sbom([])
    instance.load_runtime(
        [
            loaded("libfoo.so.1", "1.0", "/opt/app/libfoo.so.1", pid=101, service="api"),
            loaded("libfoo.so.1", "1.0", "/usr/lib/libfoo.so.1", pid=202, service="worker"),
        ]
    )
    findings = instance.reconcile()
    assert len(findings) == 1
    assert findings[0].component == "libfoo"
    assert findings[0].services == ("api", "worker")
    assert instance.service_index() == {"api": [101], "worker": [202]}
