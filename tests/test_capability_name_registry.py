import threading

import pytest

import capability_name_registry as cnr


def registry(**kwargs):
    return cnr.CapabilityNameRegistry(**kwargs)


def test_register_assigns_bit_and_bumps_version():
    reg = registry()
    assert reg.version() == 1
    reg.register("read", bit=0)
    assert reg.version() == 2
    assert reg.table() == {"version": 2, "names": {"read": 0},
                           "aliases": {}, "bits": {0: "read"}}
    reg.register("write", bit=3, aliases=("w", "modify"))
    assert reg.table()["bits"] == {0: "read", 3: "write"}
    assert reg.table()["aliases"] == {"w": "write", "modify": "write"}
    assert reg.version() == 3


def test_duplicate_name_or_bit_is_refused():
    reg = registry()
    reg.register("read", bit=0)
    reg.register("write", bit=1)
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.register("read", bit=5)
    assert exc.value.code == "NAME_TAKEN"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.register("exec", bit=1)
    assert exc.value.code == "BIT_TAKEN"
    assert reg.table()["names"] == {"read": 0, "write": 1}
    assert reg.version() == 3


def test_bit_range_and_type_validation():
    reg = registry()
    for bad in (-1, 64, 100):
        with pytest.raises(cnr.CapRegistryError) as exc:
            reg.register("cap", bit=bad)
        assert exc.value.code == "BIT_RANGE"
    for bad in ("5", 1.5, None):
        with pytest.raises(cnr.CapRegistryError) as exc:
            reg.register("cap", bit=bad)
        assert exc.value.code == "INVALID_REQUEST"
    assert reg.table()["names"] == {}
    assert reg.version() == 1


def test_name_and_alias_validation():
    reg = registry(max_name_length=8, max_aliases=2)
    for bad in ("", "   ", "has space", "a\tb"):
        with pytest.raises(cnr.CapRegistryError) as exc:
            reg.register(bad, bit=0)
        assert exc.value.code == "INVALID_REQUEST"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.register("x" * 9, bit=0)
    assert exc.value.code == "NAME_LIMIT"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.register("cap", bit=0, aliases=("a", "b", "c"))
    assert exc.value.code == "ALIAS_LIMIT"

    reg.register("cap", bit=0, aliases=("a",))
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.register("other", bit=1, aliases=("a",))
    assert exc.value.code == "ALIAS_TAKEN"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.register("other", bit=1, aliases=("cap",))
    assert exc.value.code == "ALIAS_TAKEN"
    assert reg.table()["names"] == {"cap": 0}
    assert reg.version() == 2


def test_encode_ors_bits_and_accepts_aliases():
    reg = registry()
    reg.register("read", bit=0, aliases=("r",))
    reg.register("write", bit=3, aliases=("w",))
    reg.register("exec", bit=7)
    assert reg.encode(["read", "write"]) == (1 << 0) | (1 << 3)
    assert reg.encode(["write", "read"]) == (1 << 0) | (1 << 3)
    assert reg.encode(["w", "r"]) == reg.encode(["read", "write"])
    assert reg.encode(["read", "read"]) == 1
    assert reg.encode([]) == 0


def test_encode_reports_all_unknown_names_without_partial_encoding():
    reg = registry()
    reg.register("read", bit=0)
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.encode(["read", "zeta", "alpha"])
    assert exc.value.code == "UNKNOWN_NAME"
    assert exc.value.detail == ("alpha", "zeta")
    assert reg.encode(["read"]) == 1
    assert reg.table() == {"version": 2, "names": {"read": 0},
                           "aliases": {}, "bits": {0: "read"}}


def test_decode_orders_by_bit_and_marks_unknown_bits():
    reg = registry()
    reg.register("write", bit=3)
    reg.register("read", bit=0)
    mask = (1 << 0) | (1 << 3) | (1 << 5)
    assert reg.decode(mask) == ("read", "write", "unknown:5")
    assert reg.decode(1 << 63) == ("unknown:63",)
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.decode(1 << 64)
    assert exc.value.code == "MASK_RANGE"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.decode(-1)
    assert exc.value.code == "MASK_RANGE"


def test_decode_then_encode_roundtrips_original_mask():
    reg = registry()
    reg.register("read", bit=0, aliases=("r",))
    reg.register("write", bit=3)
    reg.register("exec", bit=7)
    mask = (1 << 0) | (1 << 7)
    assert reg.encode(reg.decode(mask)) == mask
    assert reg.decode(reg.encode(["r", "exec"])) == ("read", "exec")
    assert reg.encode(reg.decode(0)) == 0


def test_set_ops_name_level_matches_bit_level():
    reg = registry()
    reg.register("read", bit=0, aliases=("r",))
    reg.register("write", bit=1)
    reg.register("exec", bit=2)
    assert reg.set_ops("or", ["read"], ["write", "exec"]) == (
        "read", "write", "exec")
    assert reg.set_ops("and", ["read", "write"], ["write", "exec"]) == (
        "write",)
    assert reg.set_ops("xor", ["read", "write"], ["write", "exec"]) == (
        "read", "exec")
    assert reg.set_ops("diff", ["read", "write", "exec"], ["write"]) == (
        "read", "exec")
    assert reg.set_ops("or", ["r"], ["exec"]) == ("read", "exec")


def test_set_ops_rejects_unknown_operation_and_names():
    reg = registry()
    reg.register("read", bit=0)
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.set_ops("nand", ["read"], ["read"])
    assert exc.value.code == "INVALID_OP"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.set_ops("or", ["read"], ["ghost"])
    assert exc.value.code == "UNKNOWN_NAME"
    assert exc.value.detail == ("ghost",)


def test_alias_resolves_to_canonical_everywhere():
    reg = registry()
    reg.register("read", bit=0, aliases=("r", "ro"))
    assert reg.resolve("r") == "read"
    assert reg.resolve("read") == "read"
    assert reg.aliases("read") == ("r", "ro")
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.resolve("ghost")
    assert exc.value.code == "NAME_UNKNOWN"
    assert reg.decode(reg.encode(["ro"])) == ("read",)
    assert reg.encode(["r", "ro", "read"]) == 1


def test_bit_sixty_three_is_unsigned():
    reg = registry()
    reg.register("b63", bit=63)
    reg.register("b0", bit=0)
    assert reg.encode(["b63"]) == 1 << 63
    assert reg.decode(1 << 63) == ("b63",)
    assert reg.encode(["b0", "b63"]) == (1 << 63) | 1
    assert reg.set_ops("diff", ["b0", "b63"], ["b0"]) == ("b63",)
    assert reg.table()["bits"][63] == "b63"


def test_snapshot_restore_roundtrips_and_version_is_monotonic():
    reg = registry()
    reg.register("read", bit=0, aliases=("r",))
    reg.register("write", bit=1)
    state = reg.snapshot()
    assert set(state) == {"schema", "version", "names", "aliases"}
    assert state["schema"] == 1
    assert state["version"] == 3

    clone = registry()
    assert clone.version() == 1
    clone.restore(state)
    assert clone.version() == 3
    assert clone.table() == reg.table()
    assert clone.encode(["r"]) == 1
    assert clone.decode(3) == ("read", "write")
    clone.register("exec", bit=2)
    assert clone.version() == 4


def test_restore_rejects_bad_snapshots_without_touching_live():
    reg = registry()
    reg.register("read", bit=0)
    before = reg.table()
    bad_states = [
        None,
        {},
        {"schema": 1, "version": 0, "names": {}, "aliases": {}},
        {"schema": 1, "version": 1, "names": {"x": 64}, "aliases": {}},
        {"schema": 1, "version": 1, "names": {"x": 1, "y": 1}, "aliases": {}},
        {"schema": 1, "version": 1, "names": {"x": 1},
         "aliases": {"a": "missing"}},
    ]
    for bad in bad_states:
        with pytest.raises(cnr.CapRegistryError) as exc:
            reg.restore(bad)
        assert exc.value.code == "SNAPSHOT_INVALID"
    with pytest.raises(cnr.CapRegistryError) as exc:
        reg.restore({"schema": 2, "version": 1, "names": {}, "aliases": {}})
    assert exc.value.code == "SNAPSHOT_VERSION_UNSUPPORTED"
    assert reg.table() == before


def test_concurrent_registration_keeps_tables_consistent():
    reg = registry()
    errors = []
    barrier = threading.Barrier(8)

    def writer(base):
        try:
            barrier.wait(timeout=5)
            for offset in range(5):
                bit = base * 5 + offset
                reg.register(f"cap{bit:02d}", bit=bit,
                             aliases=(f"a{bit:02d}",))
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    def reader():
        try:
            barrier.wait(timeout=5)
            for _ in range(200):
                view = reg.table()
                for name, bit in view["names"].items():
                    if view["bits"].get(bit) != name:
                        errors.append(f"half update {name}")
                for alias, canonical in view["aliases"].items():
                    if canonical not in view["names"]:
                        errors.append(f"dangling alias {alias}")
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(index,))
               for index in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(reg.table()["names"]) == 20
    assert len(reg.table()["aliases"]) == 20
    assert reg.version() == 21
