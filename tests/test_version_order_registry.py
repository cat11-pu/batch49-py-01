import pytest

import version_order_registry as vo


def registry(**kwargs):
    return vo.VersionOrderRegistry(**kwargs)


def test_register_ecosystem_extends_registry():
    reg = registry()
    assert reg.ecosystems() == ["debian", "pep440", "semver"]
    reg.register_ecosystem(
        "calver", lambda raw: tuple(int(part) for part in raw.split("."))
    )
    assert reg.ecosystems() == ["calver", "debian", "pep440", "semver"]
    assert reg.compare("calver", "2026.1.2", "2026.1.10") == -1
    assert reg.parse("calver", "not-a-date").valid is False


def test_parse_valid_version_exposes_fields():
    reg = registry()
    parsed = reg.parse("pep440", "1.2.3")
    assert parsed.ecosystem == "pep440"
    assert parsed.raw == "1.2.3"
    assert parsed.valid is True
    assert isinstance(parsed.key, tuple)


def test_parse_invalid_returns_valid_false():
    reg = registry()
    for ecosystem, raw in (("pep440", "banana"), ("semver", "1.2"), ("debian", "")):
        parsed = reg.parse(ecosystem, raw)
        assert parsed.valid is False
        assert parsed.key == ()
        assert parsed.raw == raw


def test_compare_pep440_prerelease_precedes_release():
    reg = registry()
    assert reg.compare("pep440", "1.2.3rc1", "1.2.3") == -1
    assert reg.compare("pep440", "1.2.3.dev1", "1.2.3rc1") == -1
    assert reg.compare("pep440", "1.2.3", "1.2.3rc1") == 1


def test_compare_pep440_post_release_follows_release():
    reg = registry()
    assert reg.compare("pep440", "1.2.3.post1", "1.2.3") == 1
    assert reg.compare("pep440", "1.2.3", "1.2.3.post2") == -1
    assert reg.compare("pep440", "1.2.3", "1.2.3.0") == 0


def test_compare_semver_numeric_ordering():
    reg = registry()
    assert reg.compare("semver", "1.10.0", "1.9.0") == 1
    assert reg.compare("semver", "1.0.0-alpha", "1.0.0") == -1
    assert reg.compare("semver", "1.0.0-alpha.2", "1.0.0-alpha.10") == -1
    assert reg.compare("semver", "0.9.0", "1.0.0") == -1


def test_compare_debian_epoch_revision_and_tilde():
    reg = registry()
    assert reg.compare("debian", "2:0.1", "1.99") == 1
    assert reg.compare("debian", "1.0-2", "1.0-1") == 1
    assert reg.compare("debian", "1.0~rc1", "1.0") == -1


def test_compare_rejects_unknown_ecosystem():
    reg = registry()
    with pytest.raises(vo.VersionOrderError) as exc:
        reg.parse("pypi", "1.0")
    assert exc.value.code == "ECOSYSTEM_UNKNOWN"
    with pytest.raises(vo.VersionOrderError) as exc:
        reg.compare("pypi", "1.0", "2.0")
    assert exc.value.code == "ECOSYSTEM_UNKNOWN"


def test_sort_versions_is_input_order_independent():
    reg = registry()
    raws = ["1.10.0", "1.2.0", "0.9.0", "1.2.0-alpha"]
    ordered = ["0.9.0", "1.2.0-alpha", "1.2.0", "1.10.0"]
    assert reg.sort_versions("semver", raws) == ordered
    assert reg.sort_versions("semver", list(reversed(raws))) == ordered


def test_dedupe_drops_equivalent_release_forms():
    reg = registry()
    assert reg.dedupe("pep440", ["1.2.3", "1.2.3.0", "1.2.4", "1.2.3"]) == [
        "1.2.3",
        "1.2.4",
    ]
    assert reg.dedupe("semver", ["2.0.0", "2.0.0", "1.0.0"]) == ["2.0.0", "1.0.0"]


def test_compare_cross_requires_declared_bridge():
    reg = registry()
    with pytest.raises(vo.VersionOrderError) as exc:
        reg.compare_cross("semver", "1.2.3", "pep440", "1.2.3")
    assert exc.value.code == "BRIDGE_MISSING"

    reg.declare_bridge("semver", "pep440", translator=lambda raw: raw)
    assert reg.compare_cross("semver", "1.2.3", "pep440", "1.2.3") == 0
    assert reg.compare_cross("semver", "1.2.4", "pep440", "1.2.3") == 1


def test_cache_hits_on_repeated_parse():
    reg = registry()
    reg.parse("pep440", "1.2.3")
    reg.parse("pep440", "1.2.3")
    stats = reg.cache_stats()
    assert stats["capacity"] == 64
    assert (stats["hits"], stats["misses"], stats["size"]) == (1, 1, 1)


def test_cache_capacity_evicts_oldest_entry():
    reg = registry(cache_capacity=2)
    reg.parse("pep440", "1.0")
    reg.parse("pep440", "2.0")
    reg.parse("pep440", "3.0")
    stats = reg.cache_stats()
    assert stats["capacity"] == 2
    assert stats["size"] == 2
    before = stats["misses"]
    reg.parse("pep440", "1.0")
    assert reg.cache_stats()["misses"] == before + 1


def test_clear_cache_is_scoped_to_ecosystem():
    reg = registry()
    reg.parse("pep440", "1.0")
    reg.parse("semver", "1.0.0")
    assert reg.clear_cache("semver") == 1
    assert reg.cache_stats()["size"] == 1
    reg.parse("semver", "1.0.0")
    assert reg.cache_stats()["misses"] == 3
    reg.parse("pep440", "1.0")
    assert reg.cache_stats()["hits"] == 1


def test_snapshot_restore_roundtrip_and_invalid_state():
    reg = registry(cache_capacity=8)
    reg.register_ecosystem("calver", lambda raw: tuple(int(part) for part in raw.split(".")))
    reg.declare_bridge("semver", "pep440", translator=lambda raw: raw)
    state = reg.snapshot()
    assert isinstance(state, dict) and state

    reg.register_ecosystem("extra", lambda raw: (int(raw),))
    clone = registry(cache_capacity=16)
    clone.restore(state)
    assert clone.ecosystems() == ["calver", "debian", "pep440", "semver"]
    assert clone.cache_stats()["capacity"] == 8
    assert clone.compare_cross("semver", "1.2.3", "pep440", "1.2.3") == 0

    for bad in (None, {}, {"ecosystems": "nope"}):
        with pytest.raises(vo.VersionOrderError) as exc:
            reg.restore(bad)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert reg.compare("calver", "2026.1.2", "2026.1.10") == -1
