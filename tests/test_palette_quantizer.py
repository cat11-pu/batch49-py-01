import threading

import pytest

import palette_quantizer as pq


def small(**kwargs):
    quantizer = pq.PaletteQuantizer(**kwargs)
    quantizer.add_color((10, 10, 10), 4)
    quantizer.add_color((100, 100, 100), 3)
    quantizer.add_color((200, 200, 200), 3)
    quantizer.build_palette()
    return quantizer


def test_add_color_rejects_bad_inputs():
    quantizer = pq.PaletteQuantizer()
    bad_calls = [((1, 2), 1, "PALETTE_INVALID_COLOR"),
                 ((1, 2, 300), 1, "PALETTE_INVALID_COLOR"),
                 ((1, 2, 3.5), 1, "PALETTE_INVALID_COLOR"),
                 ((1, 2, 3), 0, "PALETTE_INVALID_COUNT"),
                 ((1, 2, 3), -2, "PALETTE_INVALID_COUNT")]
    for rgb, count, code in bad_calls:
        with pytest.raises(pq.PaletteError) as exc:
            quantizer.add_color(rgb, count)
        assert exc.value.code == code
    assert quantizer.stats()["colors"] == 0
    assert quantizer.stats()["pixels"] == 0


def test_add_color_accumulates_same_colour():
    quantizer = pq.PaletteQuantizer()
    quantizer.add_color((10, 20, 30), 2)
    quantizer.add_color((10, 20, 30), 3)
    quantizer.add_color((200, 100, 0), 1)
    assert quantizer.histogram() == {(10, 20, 30): 5, (200, 100, 0): 1}
    assert quantizer.stats()["colors"] == 2
    assert quantizer.stats()["pixels"] == 6


def test_build_palette_uses_capacity_for_clusters():
    quantizer = pq.PaletteQuantizer(capacity=4)
    quantizer.add_color((10, 10, 10), 4)
    quantizer.add_color((100, 100, 100), 3)
    quantizer.add_color((200, 200, 200), 3)
    palette = quantizer.build_palette()
    assert len(palette) == 4
    opaque = [entry[:3] for entry in palette if entry[3] == 255]
    assert opaque == [(10, 10, 10), (100, 100, 100), (200, 200, 200)]


def test_palette_rebuild_is_stable():
    quantizer = small()
    first = quantizer.build_palette()
    second = quantizer.build_palette()
    assert first == second
    assert quantizer.palette() == first
    assert quantizer.stats()["palette_size"] == 4


def test_palette_rebuilds_after_new_colour():
    quantizer = pq.PaletteQuantizer(capacity=5)
    quantizer.add_color((10, 10, 10), 4)
    quantizer.add_color((100, 100, 100), 3)
    quantizer.add_color((200, 200, 200), 3)
    before = quantizer.build_palette()
    assert len(before) == 4
    quantizer.add_color((240, 0, 0), 5)
    after = quantizer.build_palette()
    assert len(after) == 5
    assert (240, 0, 0, 255) in after
    assert quantizer.nearest_index((239, 1, 0)) == after.index((240, 0, 0, 255))


def test_nearest_index_maps_each_palette_entry_to_itself():
    quantizer = small()
    palette = quantizer.palette()
    for index, entry in enumerate(palette):
        if index == 0:
            continue
        assert quantizer.nearest_index(entry[:3]) == index


def test_nearest_index_breaks_ties_by_lower_index():
    quantizer = small()
    grey_index = quantizer.palette().index((10, 10, 10, 255))
    assert quantizer.nearest_index((55, 55, 55)) == grey_index


def test_remap_frame_maps_uniform_image():
    quantizer = small()
    index = quantizer.palette().index((100, 100, 100, 255))
    out = quantizer.remap_frame([(100, 100, 100)] * 12, 4)
    assert out == [index] * 12


def test_remap_frame_keeps_transparent_pixels_consistent():
    quantizer = small()
    pixels = [None, (100, 100, 100), None, None, (10, 10, 10), None]
    out = quantizer.remap_frame(pixels, 3)
    assert len(out) == 6
    assert len({out[0], out[2], out[3], out[5]}) == 1
    assert out[1] != out[0]


def test_frame_pixel_budget_rejects_oversize_frame():
    quantizer = small(max_pixels_per_frame=8)
    with pytest.raises(pq.PaletteError) as exc:
        quantizer.remap_frame([(10, 10, 10)] * 9, 3)
    assert exc.value.code == "PALETTE_FRAME_LIMIT"
    assert quantizer.stats()["frames"] == 0


def test_remap_frame_rejects_non_divisible_width():
    quantizer = small()
    for width in (0, -3, 5):
        with pytest.raises(pq.PaletteError) as exc:
            quantizer.remap_frame([(10, 10, 10)] * 12, width)
        assert exc.value.code == "PALETTE_INVALID_FRAME"


def test_quantize_with_zero_strength_matches_remap():
    quantizer = pq.PaletteQuantizer(capacity=4, dither_strength=0.0)
    quantizer.add_color((10, 10, 10), 4)
    quantizer.add_color((120, 60, 20), 3)
    quantizer.add_color((240, 240, 240), 3)
    quantizer.build_palette()
    pixels = [(20 + col * 35, 30 + row * 40, 40 + col * 10)
              for row in range(4) for col in range(4)]
    assert quantizer.quantize(pixels, 4) == quantizer.remap_frame(pixels, 4)


def test_dither_is_deterministic_and_in_range():
    quantizer = pq.PaletteQuantizer(capacity=4)
    quantizer.add_color((10, 10, 10), 4)
    quantizer.add_color((120, 60, 20), 3)
    quantizer.add_color((240, 240, 240), 3)
    quantizer.build_palette()
    pixels = [(10 + col * 40, 20 + row * 40, 30 + col * 5)
              for row in range(4) for col in range(6)]
    first = quantizer.dither(pixels, 6)
    second = quantizer.dither(pixels, 6)
    assert first == second
    assert len(first) == 24
    assert all(0 <= index < len(quantizer.palette()) for index in first)


def test_snapshot_restore_roundtrip_and_malformed():
    quantizer = small(capacity=4)
    quantizer.remap_frame([(10, 10, 10)] * 4, 2)
    state = quantizer.snapshot()
    clone = pq.PaletteQuantizer(capacity=4)
    clone.restore(state)
    assert clone.histogram() == quantizer.histogram()
    assert clone.palette() == quantizer.palette()
    assert clone.stats() == quantizer.stats()
    bad_states = [None, {}, dict(state, version=9),
                  {key: value for key, value in state.items() if key != "counters"},
                  dict(state, histogram=[1, 2, 3])]
    for bad in bad_states:
        with pytest.raises(pq.PaletteError) as exc:
            quantizer.restore(bad)
        assert exc.value.code == "PALETTE_SNAPSHOT_INVALID"
    assert quantizer.stats()["pixels"] == 4


def test_concurrent_add_color_keeps_exact_totals():
    quantizer = pq.PaletteQuantizer(capacity=8)
    barrier = threading.Barrier(4)
    errors = []

    def worker(start):
        try:
            barrier.wait(timeout=5)
            for step in range(50):
                quantizer.add_color((start, (start + step) % 256, 7), 2)
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(start,))
               for start in (10, 20, 30, 40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert quantizer.stats()["colors"] == 200
    assert quantizer.stats()["pixels"] == 400
