"""Localization statistics on large waterfalls: threaded and with fewer temporaries, with the same values."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from flits.analysis import localization


def test_threaded_row_medians_equal_numpy(monkeypatch):
    rng = np.random.default_rng(9)
    values = rng.normal(size=(64, 70_000)).astype(np.float32)
    monkeypatch.setattr(localization, "_median_threads", lambda: 4)

    result = localization._row_median(values, None, overwrite=False)

    np.testing.assert_array_equal(result, np.median(values, axis=1))
    np.testing.assert_array_equal(
        values, rng.__class__(np.random.PCG64(9)).normal(size=(64, 70_000)).astype(np.float32)
    )


def _plain_normalize(masked, exclude):
    """The z-scores as plain NumPy expressions, the way localization always defined them."""
    reference = masked
    if exclude is not None:
        keep = np.ones(masked.shape[1], dtype=bool)
        keep[max(0, exclude[0]) : max(0, exclude[1])] = False
        if keep.sum() >= 16:
            reference = masked[:, keep]
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        center = np.nanmedian(reference, axis=1, keepdims=True)
        scale = 1.4826 * np.nanmedian(np.abs(reference - center), axis=1, keepdims=True)
        unusable = ~(np.isfinite(scale) & (scale > 0))
        if unusable.any():
            scale = np.where(unusable, np.nanstd(reference, axis=1, keepdims=True), scale)
        valid = np.isfinite(scale) & (scale > 0)
        z = np.full(masked.shape, np.nan, dtype=np.float32)
        np.subtract(masked, center.astype(np.float32), out=z, where=valid)
        np.divide(z, scale.astype(np.float32), out=z, where=valid)
    return z


def _awkward_waterfall():
    rng = np.random.default_rng(11)
    x = rng.normal(0, 1, (96, 24_000)).astype(np.float32)
    x[3] = np.nan  # masked channel
    x[5, 100:300] = np.nan  # a gap
    x[7] = np.round(x[7] * 0.2)  # quantized: zero MAD, standard deviation instead
    x[9] = 2.0  # constant: no usable scale
    x[11, 50] = np.inf
    return x


@pytest.mark.parametrize("layout", ["C", "row_slice", "F"])
@pytest.mark.parametrize("exclude", [None, (12_000, 12_300), (500, 400), (0, 23_990)])
def test_localization_statistics_equal_the_plain_expressions(monkeypatch, layout, exclude):
    monkeypatch.setattr(localization, "_median_threads", lambda: 4)
    base = _awkward_waterfall()
    masked = {"C": base, "row_slice": base[:, 1000:23_000], "F": np.asfortranarray(base)}[layout]

    z = localization._normalize_channels(masked, exclude)

    np.testing.assert_array_equal(z, _plain_normalize(masked, exclude))
    band = z[20:60]
    with np.errstate(invalid="ignore"):
        summed = np.nansum(band, axis=0)
        counts = np.isfinite(band).sum(axis=0).astype(float)
    plain = np.where(counts > 0, summed / np.sqrt(np.maximum(counts, 1.0)), 0.0)
    center, scale = localization._robust_stats(plain)
    profile = localization._band_profile_sn(z, 20, 59, None)
    np.testing.assert_array_equal(profile, (plain - center) / scale)
