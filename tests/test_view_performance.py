"""Files with thousands of channels: views and measurements reuse the reduced grid and send what can be drawn."""

from __future__ import annotations

import json
import warnings

import numpy as np
import pytest
from conftest import write_dispersed_filterbank

import flits.session as session_module
from flits.session import BurstSession, _jsonable_array, _reduce_masked_crop, heatmap_for_display
from flits.signal import block_reduce_mean


def _old_jsonable_array(values, digits=4):
    rounded = np.round(values, digits)
    if rounded.ndim == 1:
        return [float(value) if np.isfinite(value) else None for value in rounded]
    return [[float(value) if np.isfinite(value) else None for value in row] for row in rounded]


def _old_masked_crop(crop, mask):
    arr = np.array(crop, copy=True)
    if not np.issubdtype(arr.dtype, np.floating):
        arr = arr.astype(np.float32, copy=False)
    if mask is not None and mask.any():
        arr[mask, :] = np.nan
    return arr


@pytest.fixture
def many_channels(tmp_path):
    return write_dispersed_filterbank(
        tmp_path / "many.fil",
        nchan=2048,
        foff_mhz=-0.125,
        duration_s=2.0,
        burst_time_s=1.0,
        tsamp_s=2.56e-4,
        baseline=100.0,
        noise_std=8.0,
        burst_amp=10.0,
        nbits=8,
    )


# ---------------------------------------------------------------- the reduced grid


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize(("tfac", "ffac"), [(1, 1), (8, 1), (7, 1), (4, 2), (3, 5)])
def test_reduction_matches_the_masked_block_mean_bit_for_bit(dtype, tfac, ffac):
    rng = np.random.default_rng(7)
    data = rng.normal(10.0, 2.0, size=(40, 1003)).astype(dtype)
    data[5, 17] = np.nan  # a missing sample in an unmasked channel
    crop = data[:, 3:998]
    mask = np.zeros(40, dtype=bool)
    mask[[2, 9, 10, 33]] = True

    reduced, offsets = _reduce_masked_crop(crop, mask, tfac, ffac)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        expected = block_reduce_mean(_old_masked_crop(crop, mask), tfac=tfac, ffac=ffac)
    np.testing.assert_array_equal(reduced, expected)
    assert offsets.shape == (expected.shape[0],)
    assert np.isfinite(offsets).all()


def test_display_offset_is_each_channels_mean():
    rng = np.random.default_rng(8)
    crop = rng.normal(0.0, 1.0, size=(6, 64)).astype(np.float32) + np.arange(6, dtype=np.float32)[:, None] * 10
    mask = np.zeros(6, dtype=bool)
    mask[4] = True

    _, offsets = _reduce_masked_crop(crop, mask, 4, 1)

    expected = crop.mean(axis=1)
    np.testing.assert_allclose(np.delete(offsets, 4), np.delete(expected, 4), rtol=1e-6)
    assert offsets[4] == 0.0  # a masked channel is NaN in the grid; nothing to subtract


def test_views_reuse_the_reduced_grid_until_it_changes(many_channels, monkeypatch):
    session = BurstSession.from_file(str(many_channels.path), dm=many_channels.dm)
    calls = []
    original = session_module._reduce_masked_crop

    def counting(*args, **kwargs):
        calls.append(args[2:])
        return original(*args, **kwargs)

    monkeypatch.setattr(session_module, "_reduce_masked_crop", counting)

    session.get_view()
    session.set_event_ms(990.0, 1010.0)
    session.add_offpulse_ms(100.0, 800.0)
    session.get_view()
    session.compute_properties()
    assert len(calls) == 1

    session.mask_channel_freq(float(session.freqs[100]))
    session.get_view()
    session.set_crop_ms(200.0, 1800.0)
    session.get_view()
    session.set_time_factor(session.time_factor * 2)
    session.get_view()
    session.set_dm(many_channels.dm + 1.0)
    session.get_view()
    assert len(calls) == 5


def test_cached_grids_are_read_only(many_channels):
    session = BurstSession.from_file(str(many_channels.path), dm=many_channels.dm)
    grid = session._reduced_analysis_grid()
    with pytest.raises(ValueError, match="read-only"):
        grid.masked[0, 0] = 1.0


def test_a_new_data_array_is_never_served_from_the_old_cache(many_channels):
    session = BurstSession.from_file(str(many_channels.path), dm=many_channels.dm)
    before = session._reduced_analysis_grid().masked.copy()
    session.data = session.data * 2.0

    after = session._reduced_analysis_grid().masked
    np.testing.assert_allclose(after, 2.0 * before, rtol=1e-6)


# ---------------------------------------------------------------- what the view sends


def test_a_large_dynamic_spectrum_is_drawn_from_a_coarser_copy(many_channels):
    session = BurstSession.from_file(str(many_channels.path), dm=many_channels.dm)

    view = session.get_view()

    heatmap = view["plot"]["heatmap"]
    assert view["meta"]["view_shape"] == [2048, 976]
    assert view["meta"]["heatmap_shape"] == [1024, 976]
    assert len(heatmap["z"]) == len(heatmap["y_mhz"]) == 1024
    # Profiles and measurements keep the analysis resolution.
    assert len(view["plot"]["spectrum"]["x"]) == 2048
    assert len(view["plot"]["time_profile"]["y"]) == 976
    # Pairs of 0.125 MHz channels, at their mean frequency.
    assert heatmap["y_mhz"][0] == pytest.approx(1500.0 - 0.0625)
    assert heatmap["y_mhz"][1] - heatmap["y_mhz"][0] == pytest.approx(-0.25)


def test_heatmap_averages_blocks_and_skips_masked_cells():
    display = np.arange(24, dtype=float).reshape(6, 4)
    display[1, :] = np.nan

    z, freqs, times = heatmap_for_display(
        display, np.arange(6, dtype=float), np.arange(4, dtype=float) * 10, max_rows=2, max_columns=3
    )

    # Rows in threes (the masked row left out), columns in twos.
    np.testing.assert_allclose(
        z,
        [
            [(0 + 1 + 8 + 9) / 4, (2 + 3 + 10 + 11) / 4],
            [(12 + 13 + 16 + 17 + 20 + 21) / 6, (14 + 15 + 18 + 19 + 22 + 23) / 6],
        ],
    )
    np.testing.assert_allclose(freqs, [1.0, 4.0])
    np.testing.assert_allclose(times, [0.0, 20.0])


def test_a_small_heatmap_is_sent_unchanged():
    display = np.ones((8, 5))
    z, _freqs, _times = heatmap_for_display(display, np.arange(8.0), np.arange(5.0))
    assert z is display


@pytest.mark.parametrize(
    "values",
    [
        np.array([1.234567, np.nan, -np.inf, 2.0], dtype=np.float32),
        np.array([[1.0, np.nan], [3.333333, 4.0], [np.inf, 6.0]]),
        np.arange(5),
        np.zeros((0, 3)),
    ],
)
def test_json_conversion_is_unchanged(values):
    assert json.dumps(_jsonable_array(values, digits=3)) == json.dumps(_old_jsonable_array(values, digits=3))
