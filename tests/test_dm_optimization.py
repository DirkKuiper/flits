from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


DM_CONST = 1 / (2.41 * 10 ** -4)


def _synthetic_dm_session(true_dm: float = 50.0, *, num_channels: int = 8) -> BurstSession:
    freqs = np.linspace(1100.0, 1000.0, num_channels)
    tsamp = 1e-3
    num_time_bins = 256
    aligned_bin = 120

    time = np.arange(num_time_bins, dtype=float)
    pulse = np.exp(-0.5 * ((time - aligned_bin) / 2.5) ** 2)
    reffreq = float(np.max(freqs))
    time_shift = DM_CONST * true_dm * (reffreq ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / tsamp).astype(int)

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))

    metadata = FilterbankMetadata(
        source_path=Path("synthetic_dm.fil"),
        source_name="synthetic_dm",
        tsamp=tsamp,
        freqres=float(abs(freqs[1] - freqs[0])),
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=float(abs(freqs[1] - freqs[0]) * freqs.size),
        npol=1,
        freqs_mhz=freqs,
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="synthetic",
    )
    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=10.0)
    return BurstSession(
        config=config,
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=num_time_bins,
        event_start=aligned_bin - 8,
        event_end=aligned_bin + 8,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )


class DmOptimizationTest(unittest.TestCase):
    def test_optimize_dm_recovers_bracketed_peak_without_mutating_session(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)
        original_data = session.data.copy()
        original_dm = session.dm

        result = session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)

        self.assertGreaterEqual(result.best_dm, 46.0)
        self.assertLessEqual(result.best_dm, 54.0)
        self.assertLess(abs(result.best_dm - 50.0), 2.0)
        self.assertEqual(original_dm, session.dm)
        np.testing.assert_allclose(session.data, original_data)
        self.assertIs(session.dm_optimization, result)

    def test_optimize_dm_reports_quadratic_uncertainty_for_interior_peak(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)

        result = session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)

        self.assertEqual(result.fit_status, "quadratic_peak_fit")
        self.assertIsNotNone(result.best_dm_uncertainty)
        self.assertGreater(result.best_dm_uncertainty or 0.0, 0.0)
        self.assertGreater(result.best_dm, float(np.min(result.trial_dms)))
        self.assertLess(result.best_dm, float(np.max(result.trial_dms)))

    def test_optimize_dm_falls_back_when_peak_hits_sweep_edge(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)

        result = session.optimize_dm(center_dm=47.0, half_range=2.0, step=1.0)

        self.assertEqual(result.fit_status, "peak_on_sweep_edge")
        self.assertEqual(result.best_dm, result.sampled_best_dm)
        self.assertIsNone(result.best_dm_uncertainty)

    def test_dm_optimization_survives_non_scoring_changes_and_clears_on_data_or_selection_changes(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)

        session.set_time_factor(2)
        self.assertIsNotNone(session.dm_optimization)

        session.set_freq_factor(2)
        self.assertIsNotNone(session.dm_optimization)

        session.add_peak_ms(session.bin_to_ms(120))
        self.assertIsNotNone(session.dm_optimization)

        session.add_region_ms(session.bin_to_ms(116), session.bin_to_ms(124))
        self.assertIsNotNone(session.dm_optimization)

        session.set_dm(51.0)
        self.assertIsNone(session.dm_optimization)

        for action in (
            lambda current: current.set_crop_ms(10.0, 180.0),
            lambda current: current.set_event_ms(118.0, 130.0),
            lambda current: current.mask_channel_freq(float(current.freqs[0])),
            lambda current: current.set_spectral_extent_freq(float(current.freqs[1]), float(current.freqs[-2])),
        ):
            current = _synthetic_dm_session(true_dm=50.0)
            current.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
            action(current)
            self.assertIsNone(current.dm_optimization)

    def test_optimize_dm_reports_metric_and_underdedispersed_residual_slope(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24)
        session.set_dm(25.0)

        result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5)

        self.assertEqual(result.snr_metric, "integrated_event_snr")
        self.assertEqual(result.applied_dm, 25.0)
        self.assertEqual(result.residual_status, "ok")
        self.assertGreaterEqual(result.subband_freqs_mhz.size, 3)
        self.assertEqual(result.subband_freqs_mhz.size, result.arrival_times_applied_ms.size)
        self.assertEqual(result.subband_freqs_mhz.size, result.arrival_times_best_ms.size)
        self.assertEqual(result.subband_freqs_mhz.size, result.residuals_applied_ms.size)
        self.assertEqual(result.subband_freqs_mhz.size, result.residuals_best_ms.size)
        slope_under = float(np.polyfit(result.subband_freqs_mhz, result.residuals_applied_ms, 1)[0])
        slope_best = float(np.polyfit(result.subband_freqs_mhz, result.residuals_best_ms, 1)[0])
        self.assertLess(slope_under, 0.0)
        self.assertLess(abs(slope_best), abs(slope_under))

    def test_optimize_dm_reports_overdedispersed_residual_slope(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24)
        session.set_dm(75.0)

        result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5)

        self.assertEqual(result.residual_status, "ok")
        slope_over = float(np.polyfit(result.subband_freqs_mhz, result.residuals_applied_ms, 1)[0])
        self.assertGreater(slope_over, 0.0)

    def test_optimize_dm_marks_residuals_unavailable_when_band_is_too_narrow(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=10)

        result = session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)

        self.assertEqual(result.residual_status, "insufficient_active_channels")
        self.assertEqual(result.subband_freqs_mhz.size, 0)
        self.assertEqual(result.arrival_times_applied_ms.size, 0)
        self.assertEqual(result.arrival_times_best_ms.size, 0)
        self.assertEqual(result.residuals_applied_ms.size, 0)
        self.assertEqual(result.residuals_best_ms.size, 0)


if __name__ == "__main__":
    unittest.main()
