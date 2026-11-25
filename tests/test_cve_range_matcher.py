import copy
import threading

import pytest

import cve_range_matcher as cm

CVE = "CVE-2026-40001"


def matcher(**kwargs):
    return cm.CveRangeMatcher(**kwargs)


def add(instance, ranges, *, source="osv", cve=CVE, revision=1):
    return instance.add_source_ranges(cve, source, ranges, revision=revision)


def test_add_source_ranges_counts_and_tracks_sources():
    instance = matcher()
    assert add(instance, [{"introduced": "1.0", "fixed": "1.5"}], source="osv") == 1
    assert add(
        instance,
        [
            {"introduced": "1.2", "fixed": "1.9"},
            {"introduced": "2.0", "fixed": None},
        ],
        source="nvd",
    ) == 2
    assert instance.sources_for(CVE) == ["nvd", "osv"]
    assert instance.sources_for("CVE-1999-0002") == []


def test_merge_ranges_unions_overlapping_intervals():
    instance = matcher()
    add(instance, [{"introduced": "1.0", "fixed": "1.5"}], source="osv")
    add(instance, [{"introduced": "1.2", "fixed": "1.9"}], source="nvd")
    assert instance.merge_ranges(CVE) == [("0:1.0", "0:1.9")]


def test_merge_ranges_keeps_gaps_and_joins_touching_bounds():
    instance = matcher()
    add(
        instance,
        [
            {"introduced": "1.0", "fixed": "1.5"},
            {"introduced": "1.5", "fixed": "2.0"},
            {"introduced": "3.0", "fixed": None},
        ],
    )
    assert instance.merge_ranges(CVE) == [("0:1.0", "0:2.0"), ("0:3.0", None)]


def test_evaluate_inside_range_is_affected():
    instance = matcher()
    add(instance, [{"introduced": "1.0", "fixed": "1.5"}], source="osv")
    add(instance, [{"introduced": "3.0", "fixed": None}], source="nvd")
    verdict = instance.evaluate(CVE, "1.2.3")
    assert verdict.cve == CVE
    assert verdict.version == "1.2.3"
    assert verdict.status == "affected"
    assert verdict.reason == "IN_RANGE"
    assert verdict.sources == ("nvd", "osv")


def test_evaluate_below_introduced_is_not_affected():
    instance = matcher()
    add(instance, [{"introduced": "1.0", "fixed": "1.5"}])
    verdict = instance.evaluate(CVE, "0.9")
    assert verdict.status == "not_affected"
    assert verdict.reason == "OUT_OF_RANGE"


def test_evaluate_upper_bound_is_exclusive_and_lower_bound_inclusive():
    instance = matcher()
    add(instance, [{"introduced": "1.0", "fixed": "1.5"}])
    upper = instance.evaluate(CVE, "1.5")
    lower = instance.evaluate(CVE, "1.0")
    assert (upper.status, upper.reason) == ("not_affected", "OUT_OF_RANGE")
    assert (lower.status, lower.reason) == ("affected", "IN_RANGE")


def test_exclusion_beats_range_match():
    instance = matcher()
    add(instance, [{"introduced": "1.0", "fixed": "2.0"}])
    instance.set_exclusions(CVE, ["1.4.2", "1.0"])
    for version in ("1.4.2", "1.0"):
        verdict = instance.evaluate(CVE, version)
        assert verdict.status == "not_affected"
        assert verdict.reason == "EXCLUDED"
    assert instance.evaluate(CVE, "1.1").status == "affected"


def test_backport_exception_clears_declared_distro_version():
    instance = matcher()
    add(
        instance,
        [{"introduced": "1.0", "fixed": "2.0", "distro": "ubuntu-22.04"}],
        source="ubuntu",
    )
    instance.add_backport_exception(
        CVE, distro="ubuntu-22.04", version="1.2.8-1", source="ubuntu"
    )
    verdict = instance.evaluate(CVE, "1.2.8-1", distro="ubuntu-22.04")
    assert verdict.status == "not_affected"
    assert verdict.reason == "BACKPORT_EXCEPTION"


def test_unknown_cve_reports_no_ranges():
    instance = matcher()
    verdict = instance.evaluate("CVE-1999-0001", "1.0")
    assert verdict.status == "unknown"
    assert verdict.reason == "NO_RANGES"
    assert verdict.sources == ()


def test_compare_versions_epoch_and_numeric_segments():
    instance = matcher()
    assert instance.compare_versions("2:0.9", "1.9.9") == 1
    assert instance.compare_versions("1.9.9", "2:0.9") == -1
    assert instance.compare_versions("1.10", "1.9") == 1
    assert instance.compare_versions("1.0-10", "1.0-9") == 1
    assert instance.compare_versions("1.0-1", "1.0") == 1
    assert instance.compare_versions("1.0", "1.0") == 0


def test_normalize_version_canonical_form_and_errors():
    instance = matcher()
    assert instance.normalize_version("1.2.3") == "0:1.2.3"
    assert instance.normalize_version("2:1.2.3-4") == "2:1.2.3-4"
    assert instance.normalize_version("  1.2.3  ") == "0:1.2.3"
    for bad in ("", "   ", "1.2 3", "1:"):
        with pytest.raises(cm.CveRangeError) as exc:
            instance.normalize_version(bad)
        assert exc.value.code == "VERSION_UNPARSEABLE"


def test_add_source_ranges_rejects_range_without_introduced():
    instance = matcher()
    with pytest.raises(cm.CveRangeError) as exc:
        add(instance, [{"fixed": "1.5"}])
    assert exc.value.code == "RANGE_MISSING_FIELD"
    assert instance.sources_for(CVE) == []
    assert instance.merge_ranges(CVE) == []


def test_cache_hits_and_revision_invalidation():
    instance = matcher(cache_size=4)
    add(instance, [{"introduced": "1.0", "fixed": "2.0"}], revision=1)
    instance.evaluate(CVE, "1.5")
    instance.evaluate(CVE, "1.5")
    stats = instance.cache_stats()
    assert stats["capacity"] == 4
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["size"] == 1

    add(instance, [{"introduced": "1.0", "fixed": "2.0"}], revision=2)
    instance.evaluate(CVE, "1.5")
    assert instance.cache_stats()["misses"] == 2

    for index in range(6):
        instance.evaluate(CVE, f"1.{index}")
    assert instance.cache_stats()["size"] <= 4


def test_concurrent_sources_merge_consistently():
    instance = matcher()
    sources = [f"src-{index:02d}" for index in range(8)]
    errors = []
    barrier = threading.Barrier(len(sources))

    def worker(source, index):
        try:
            barrier.wait(timeout=5)
            instance.add_source_ranges(
                CVE,
                source,
                [{"introduced": f"1.{index}", "fixed": f"2.{index}"}],
                revision=1,
            )
            instance.evaluate(CVE, "1.5")
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=worker, args=(source, index))
        for index, source in enumerate(sources)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert instance.sources_for(CVE) == sorted(sources)
    assert instance.merge_ranges(CVE) == [("0:1.0", "0:2.7")]


def test_snapshot_detached_and_restore_roundtrip():
    instance = matcher()
    add(instance, [{"introduced": "1.0", "fixed": "2.0"}], source="osv")
    instance.set_exclusions(CVE, ["1.5"])
    state = instance.snapshot()
    pristine = copy.deepcopy(state)
    assert isinstance(state, dict) and state

    add(instance, [{"introduced": "5.0", "fixed": "6.0"}], source="late", revision=2)

    clone = matcher()
    clone.restore(pristine)
    assert clone.sources_for(CVE) == ["osv"]
    assert clone.evaluate(CVE, "1.5").reason == "EXCLUDED"
    assert clone.evaluate(CVE, "1.1").status == "affected"

    for bad in (None, {}, {"sources": "nope"}):
        with pytest.raises(cm.CveRangeError) as exc:
            instance.restore(bad)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert instance.evaluate(CVE, "1.1").status == "affected"
