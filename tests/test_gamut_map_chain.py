import threading

import pytest

import gamut_map_chain as gm


def chain(**kwargs):
    return gm.GamutMapChain(**kwargs)


def test_params_defaults_and_target_whitelist():
    converter = chain()
    assert converter.params() == {"target": "srgb", "bit_depth": 8,
                                  "dither_strength": 0.5, "block_size": 64,
                                  "max_pixels": 1000000}
    converter.set_target("p3")
    assert converter.params()["target"] == "p3"
    with pytest.raises(gm.GamutError) as exc:
        converter.set_target("rec2020")
    assert exc.value.code == "GAMUT_UNKNOWN_TARGET"
    assert converter.params()["target"] == "p3"


def test_set_target_rejects_bad_bit_depth():
    converter = chain()
    for depth in (7, 12):
        with pytest.raises(gm.GamutError) as exc:
            converter.set_target("srgb", depth)
        assert exc.value.code == "GAMUT_INVALID_DEPTH"
    assert converter.params()["bit_depth"] == 8


def test_constructor_rejects_bad_block_size():
    for kwargs in ({"block_size": 0}, {"block_size": 5000}, {"max_pixels": 0}):
        with pytest.raises(gm.GamutError) as exc:
            chain(**kwargs)
        assert exc.value.code == "GAMUT_INVALID_BLOCK"


def test_decode_srgb_reference_values():
    converter = chain()
    assert converter.decode(0.0) == 0.0
    assert converter.decode(1.0) == 1.0
    assert converter.decode(0.5) == pytest.approx(0.214041, abs=1e-6)


def test_encode_roundtrips_decode():
    converter = chain()
    for value in (0.05, 0.35, 0.8):
        assert converter.encode(converter.decode(value)) == pytest.approx(value, abs=1e-9)


def test_linear_transfer_is_passthrough():
    converter = chain()
    assert converter.decode(0.42, "linear") == 0.42
    assert converter.encode(0.42, "linear") == 0.42
    with pytest.raises(gm.GamutError) as exc:
        converter.decode(0.42, "pq")
    assert exc.value.code == "GAMUT_UNKNOWN_TRANSFER"


def test_compress_chroma_brings_p3_green_to_srgb_boundary():
    converter = chain()
    out = converter.compress_chroma((0.0, 1.0, 0.0))
    assert len(out) == 3
    assert all(0.0 <= value <= 1.0 for value in out)
    assert min(out) == pytest.approx(0.0, abs=1e-6)
    luminance = 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    assert luminance == pytest.approx(0.691721, abs=1e-4)


def test_compress_chroma_keeps_luminance_for_highlight():
    converter = chain()
    out = converter.compress_chroma((1.0, 0.0, 0.5))
    assert max(out) == pytest.approx(1.0, abs=1e-6)
    assert all(0.0 <= value <= 1.0 for value in out)
    luminance = 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    assert luminance == pytest.approx(0.661354, abs=1e-4)


def test_quantize_without_dither_rounds_to_levels():
    converter = chain(dither_strength=0.0)
    assert converter.quantize(0.5) == pytest.approx(128 / 255)
    assert converter.quantize(0.2) == pytest.approx(51 / 255)
    assert converter.quantize(1.5) == 1.0
    assert converter.quantize(-0.3) == 0.0


def test_quantize_dither_depends_on_pixel_and_frame():
    converter = chain()
    assert converter.quantize(0.5, x=0, y=0) == pytest.approx(127 / 255)
    assert converter.quantize(0.5, x=1, y=0) == pytest.approx(128 / 255)
    assert converter.quantize(0.5, x=0, y=0, frame=3) == pytest.approx(128 / 255)


def test_quantize_honours_bit_depth():
    converter = chain(bit_depth=10, dither_strength=0.0)
    assert converter.quantize(0.5) == pytest.approx(512 / 1023)
    assert converter.quantize(1.0) == 1.0


def test_convert_counts_out_of_gamut_pixels():
    converter = chain()
    pixels = [(0.0, 1.0, 0.0), (0.2, 0.3, 0.4), (0.0, 0.0, 0.0)]
    out = converter.convert(pixels)
    assert len(out) == 3
    assert all(len(row) == 3 for row in out)
    assert all(0.0 <= value <= 1.0 for row in out for value in row)
    assert converter.out_of_gamut() == 1
    assert converter.stats()["converted"] == 3


def test_convert_enforces_batch_limit():
    converter = chain(max_pixels=4)
    with pytest.raises(gm.GamutError) as exc:
        converter.convert([(0.1, 0.2, 0.3)] * 5)
    assert exc.value.code == "GAMUT_BATCH_LIMIT"
    assert converter.stats()["converted"] == 0


def test_snapshot_roundtrip_and_malformed_rejection():
    converter = chain()
    converter.convert([(0.0, 1.0, 0.0)])
    state = converter.snapshot()
    clone = chain()
    clone.restore(state)
    assert clone.params() == converter.params()
    assert clone.out_of_gamut() == 1
    assert clone.stats() == converter.stats()
    bad_states = [None, {}, dict(state, version=2),
                  {key: value for key, value in state.items() if key != "counters"},
                  dict(state, target="rec2020")]
    for bad in bad_states:
        with pytest.raises(gm.GamutError) as exc:
            converter.restore(bad)
        assert exc.value.code == "GAMUT_SNAPSHOT_INVALID"
    assert converter.out_of_gamut() == 1


def test_concurrent_target_switch_keeps_params_whole():
    converter = chain()
    errors = []
    barrier = threading.Barrier(3)

    def writer(target):
        try:
            barrier.wait(timeout=5)
            for _ in range(100):
                converter.set_target(target)
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(repr(exc))

    def reader():
        try:
            barrier.wait(timeout=5)
            for _ in range(200):
                params = converter.params()
                assert params["target"] in {"srgb", "p3"}
                assert params["bit_depth"] == 8
                assert params["block_size"] == 64
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=("srgb",)),
               threading.Thread(target=writer, args=("p3",)),
               threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
