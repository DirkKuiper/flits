from __future__ import annotations

import unittest
import warnings

import numpy as np

from flits.analysis.localization import _boxcar_snr, _moving_sum, _normalize_channels, _row_nanmedian, localize_burst


def _noise(nchan: int, ntime: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal((nchan, ntime))


def _add_burst(
    data: np.ndarray,
    *,
    time_bin: int,
    width_bins: float,
    chan_lo: int,
    chan_hi: int,
    amplitude: float,
) -> None:
    nchan, ntime = data.shape
    t = np.arange(ntime, dtype=float)
    pulse = np.exp(-0.5 * ((t - time_bin) / width_bins) ** 2)
    envelope = np.zeros(nchan, dtype=float)
    envelope[chan_lo : chan_hi + 1] = 1.0
    data += amplitude * envelope[:, None] * pulse[None, :]


class LocalizeBurstTest(unittest.TestCase):
    def test_band_limited_burst_is_localized_in_time_and_frequency(self) -> None:
        data = _noise(128, 4096, seed=3)
        _add_burst(data, time_bin=1500, width_bins=20, chan_lo=40, chan_hi=80, amplitude=1.2)

        result = localize_burst(data)

        self.assertEqual(result.status, "ok")
        self.assertTrue(result.band_limited)
        self.assertLessEqual(result.event_start_bin, 1500 - 20)
        self.assertGreaterEqual(result.event_end_bin, 1500 + 20)
        self.assertLess(result.event_end_bin - result.event_start_bin, 600)
        self.assertLessEqual(result.spec_lo, 40)
        self.assertGreaterEqual(result.spec_hi, 80)
        self.assertLess(result.spec_hi - result.spec_lo, 90)
        self.assertAlmostEqual(result.peak_bin, 1500, delta=15)
        self.assertGreater(result.integrated_snr, 6.0)
        self.assertTrue(result.offpulse_regions)

    def test_band_limited_burst_beats_full_band_dilution(self) -> None:
        # Weak, narrow-band burst: full-band S/N is marginal, sub-band S/N
        # is convincing. The iteration must converge on the sub-band.
        data = _noise(256, 4096, seed=11)
        _add_burst(data, time_bin=2000, width_bins=15, chan_lo=100, chan_hi=130, amplitude=1.0)

        result = localize_burst(data)

        self.assertEqual(result.status, "ok")
        self.assertTrue(result.band_limited)
        self.assertLessEqual(result.spec_lo, 100)
        self.assertGreaterEqual(result.spec_hi, 130)
        self.assertLess(result.spec_hi - result.spec_lo, 120)

    def test_full_band_burst_reports_full_band(self) -> None:
        data = _noise(64, 2048, seed=5)
        _add_burst(data, time_bin=700, width_bins=12, chan_lo=0, chan_hi=63, amplitude=1.0)

        result = localize_burst(data)

        self.assertEqual(result.status, "ok")
        self.assertFalse(result.band_limited)
        self.assertEqual(result.spec_lo, 0)
        self.assertEqual(result.spec_hi, 63)

    def test_noise_only_returns_no_detection(self) -> None:
        data = _noise(64, 2048, seed=8)

        result = localize_burst(data)

        self.assertEqual(result.status, "no_detection")
        self.assertIn("below_detection_threshold", result.warning_flags)

    def test_multi_component_burst_spans_one_event_window(self) -> None:
        data = _noise(128, 4096, seed=13)
        _add_burst(data, time_bin=1800, width_bins=10, chan_lo=20, chan_hi=100, amplitude=1.5)
        _add_burst(data, time_bin=1860, width_bins=10, chan_lo=20, chan_hi=100, amplitude=1.2)

        result = localize_burst(data)

        self.assertEqual(result.status, "ok")
        self.assertLessEqual(result.event_start_bin, 1790)
        self.assertGreaterEqual(result.event_end_bin, 1870)

    def test_masked_channels_are_ignored(self) -> None:
        data = _noise(128, 2048, seed=21)
        _add_burst(data, time_bin=900, width_bins=15, chan_lo=30, chan_hi=70, amplitude=1.2)
        data[0:10, :] = np.nan
        data[50:55, :] = np.nan

        result = localize_burst(data)

        self.assertEqual(result.status, "ok")
        self.assertLessEqual(result.spec_lo, 32)
        self.assertGreaterEqual(result.spec_hi, 68)

    def test_burst_near_edge_is_flagged(self) -> None:
        data = _noise(64, 2048, seed=34)
        _add_burst(data, time_bin=30, width_bins=10, chan_lo=0, chan_hi=63, amplitude=2.0)

        result = localize_burst(data)

        self.assertIn("event_near_edge", result.warning_flags)

    def test_all_masked_returns_unusable(self) -> None:
        data = np.full((16, 512), np.nan)

        result = localize_burst(data)

        self.assertEqual(result.status, "no_detection")
        self.assertIn("unusable_data", result.warning_flags)

    def test_search_window_ignores_stronger_event_elsewhere(self) -> None:
        data = _noise(64, 4096, seed=55)
        _add_burst(data, time_bin=1000, width_bins=12, chan_lo=0, chan_hi=63, amplitude=0.9)
        _add_burst(data, time_bin=3000, width_bins=12, chan_lo=0, chan_hi=63, amplitude=3.0)

        unconstrained = localize_burst(data)
        constrained = localize_burst(data, search_window_bins=(800, 1200))

        self.assertAlmostEqual(unconstrained.peak_bin, 3000, delta=15)
        self.assertEqual(constrained.status, "ok")
        self.assertAlmostEqual(constrained.peak_bin, 1000, delta=15)
        self.assertEqual(constrained.search_window_bins, (800, 1200))
        self.assertEqual(constrained.to_dict()["search_window_bins"], [800, 1200])

    def test_search_window_does_not_clip_event_wings(self) -> None:
        data = _noise(64, 2048, seed=56)
        _add_burst(data, time_bin=1000, width_bins=30, chan_lo=0, chan_hi=63, amplitude=1.5)

        result = localize_burst(data, search_window_bins=(990, 1010))

        self.assertLess(result.event_start_bin, 990)
        self.assertGreater(result.event_end_bin, 1010)

    def test_search_window_is_validated(self) -> None:
        data = _noise(16, 512, seed=57)

        for invalid in ((-1, 20), (20, 20), (40, 20), (0, 513)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                localize_burst(data, search_window_bins=invalid)

    def test_result_round_trips_to_dict(self) -> None:
        data = _noise(64, 2048, seed=3)
        _add_burst(data, time_bin=1000, width_bins=10, chan_lo=10, chan_hi=50, amplitude=1.5)

        result = localize_burst(data)
        payload = result.to_dict()

        self.assertEqual(payload["status"], result.status)
        self.assertEqual(payload["event_start_bin"], result.event_start_bin)
        self.assertEqual(payload["spec_hi"], result.spec_hi)
        self.assertIsNone(payload["search_window_bins"])
        self.assertIsInstance(payload["offpulse_regions"], list)


class FastStatisticsTest(unittest.TestCase):
    """The fast paths must give the numbers the direct numpy calls give."""

    def test_row_median_is_identical_to_nanmedian(self) -> None:
        rng = np.random.default_rng(0)
        for shape in ((3, 4), (64, 256), (300, 1000)):
            for dtype in (np.float32, np.float64):
                values = rng.standard_normal(shape).astype(dtype)
                values[1] = np.nan
                values[2, ::3] = np.nan
                values[0, 0] = np.inf
                with self.subTest(shape=shape, dtype=dtype), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    expected = np.nanmedian(values, axis=1, keepdims=True)
                    result = _row_nanmedian(values)
                self.assertEqual(result.dtype, expected.dtype)
                np.testing.assert_array_equal(result, expected)

    def test_moving_sum_matches_same_mode_convolution(self) -> None:
        rng = np.random.default_rng(1)
        for n in (5, 16, 17, 1001, 20000):
            values = rng.standard_normal(n)
            for width in sorted({w for w in (1, 2, 3, 8, n // 4, n // 2, n) if 1 <= w <= n}):
                with self.subTest(n=n, width=width):
                    expected = np.convolve(values, np.ones(width), mode="same")
                    np.testing.assert_allclose(_moving_sum(values, width), expected, rtol=1e-12, atol=1e-9)

    def test_boxcar_keeps_nan_local(self) -> None:
        profile = np.random.default_rng(2).standard_normal(500)
        profile[100] = np.nan
        snr = _boxcar_snr(profile, 5)
        self.assertTrue(np.isnan(snr[100]))
        self.assertTrue(np.isfinite(snr[300]))

    def test_channel_normalization_matches_direct_statistics(self) -> None:
        # Quantized channels have zero MAD and fall back to the standard
        # deviation; a column slice makes the float32 array non-contiguous,
        # where the summation order of np.nanstd matters.
        rng = np.random.default_rng(3)
        full = rng.standard_normal((48, 6000)).astype(np.float32)
        full[:16] = np.round(full[:16] * 0.3)
        full[20] = np.nan
        masked = full[:, 500:5500]
        self.assertFalse(masked.flags.c_contiguous)

        keep = np.ones(masked.shape[1], dtype=bool)
        keep[2000:2100] = False
        reference = masked[:, keep]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            center = np.nanmedian(reference, axis=1, keepdims=True)
            scale = 1.4826 * np.nanmedian(np.abs(reference - center), axis=1, keepdims=True)
            scale = np.where(np.isfinite(scale) & (scale > 0), scale, np.nanstd(reference, axis=1, keepdims=True))
        expected = (masked - center) / scale

        np.testing.assert_array_equal(_normalize_channels(masked, exclude=(2000, 2100)), expected)


if __name__ == "__main__":
    unittest.main()
