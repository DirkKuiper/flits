from __future__ import annotations

import unittest

import numpy as np

from flits.analysis.localization import localize_burst


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
    envelope[chan_lo:chan_hi + 1] = 1.0
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

    def test_result_round_trips_to_dict(self) -> None:
        data = _noise(64, 2048, seed=3)
        _add_burst(data, time_bin=1000, width_bins=10, chan_lo=10, chan_hi=50, amplitude=1.5)

        result = localize_burst(data)
        payload = result.to_dict()

        self.assertEqual(payload["status"], result.status)
        self.assertEqual(payload["event_start_bin"], result.event_start_bin)
        self.assertEqual(payload["spec_hi"], result.spec_hi)
        self.assertIsInstance(payload["offpulse_regions"], list)


if __name__ == "__main__":
    unittest.main()
