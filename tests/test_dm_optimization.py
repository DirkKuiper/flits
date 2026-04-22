from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np

from flits.analysis.dm_optimization import dm_trial_grid
from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


DM_CONST = 1 / (2.41 * 10 ** -4)
UPSTREAM_DMPHASE_AVAILABLE = importlib.util.find_spec("DM_phase") is not None


def _synthetic_dm_session(true_dm: float = 50.0, *, num_channels: int = 8, noise_std: float = 0.0) -> BurstSession:
    freqs = np.linspace(1100.0, 1000.0, num_channels)
    tsamp = 1e-3
    num_time_bins = 256
    aligned_bin = 120

    time = np.arange(num_time_bins, dtype=float)
    pulse = np.exp(-0.5 * ((time - aligned_bin) / 2.5) ** 2)
    reffreq = float(np.max(freqs))
    time_shift = DM_CONST * true_dm * (reffreq ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / tsamp).astype(int)
    rng = np.random.default_rng(12345)

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))
    if noise_std > 0:
        data += rng.normal(0.0, noise_std, size=data.shape)

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


def _synthetic_complex_dm_session(true_dm: float = 50.0, *, num_channels: int = 24, noise_std: float = 0.0) -> BurstSession:
    freqs = np.linspace(1100.0, 1000.0, num_channels)
    tsamp = 1e-3
    num_time_bins = 320

    time = np.arange(num_time_bins, dtype=float)
    pulse = np.exp(-0.5 * ((time - 110) / 2.5) ** 2) + 0.8 * np.exp(-0.5 * ((time - 150) / 3.0) ** 2)
    reffreq = float(np.max(freqs))
    time_shift = DM_CONST * true_dm * (reffreq ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / tsamp).astype(int)
    rng = np.random.default_rng(24680)

    data = np.zeros((freqs.size, num_time_bins), dtype=float)
    for chan, shift in enumerate(bin_shift):
        data[chan, :] = np.roll(pulse, -int(shift))
    if noise_std > 0:
        data += rng.normal(0.0, noise_std, size=data.shape)

    metadata = FilterbankMetadata(
        source_path=Path("synthetic_complex_dm.fil"),
        source_name="synthetic_complex_dm",
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
    session = BurstSession(
        config=config,
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=num_time_bins,
        event_start=100,
        event_end=160,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )
    session.add_peak_ms(session.bin_to_ms(110))
    session.add_peak_ms(session.bin_to_ms(150))
    return session


def _run_upstream_dm_phase(
    waterfall: np.ndarray,
    trial_dms: np.ndarray,
    tsamp_sec: float,
    freqs_mhz: np.ndarray,
) -> tuple[float, float]:
    import DM_phase

    original_arange = DM_phase.np.arange

    def _compat_arange(start, *args, **kwargs):
        values = [start, *args]
        normalized: list[object] = []
        for value in values:
            if isinstance(value, np.ndarray):
                array = np.asarray(value)
                if array.size == 1:
                    normalized.append(int(array.reshape(-1)[0]))
                    continue
            normalized.append(value)
        return original_arange(*normalized, **kwargs)

    DM_phase.np.arange = _compat_arange
    try:
        return DM_phase.get_dm(
            waterfall,
            trial_dms,
            tsamp_sec,
            freqs_mhz,
            no_plots=True,
        )
    finally:
        DM_phase.np.arange = original_arange


class DmOptimizationTest(unittest.TestCase):
    def _compare_with_upstream_dmphase(
        self,
        session: BurstSession,
        *,
        center_dm: float,
        half_range: float,
        step: float,
        sampled_tolerance: float | None = None,
        refined_tolerance: float | None = None,
        peak_index_tolerance: int = 1,
    ) -> None:
        trial_dms, _ = dm_trial_grid(center_dm, half_range, step)
        result = session.optimize_dm(center_dm=center_dm, half_range=half_range, step=step, metric="dm_phase")
        grid = session._reduced_analysis_grid()
        waterfall = np.asarray(grid.masked[grid.spec_lo : grid.spec_hi + 1, :], dtype=float)
        freqs = np.asarray(grid.freqs_mhz[grid.spec_lo : grid.spec_hi + 1], dtype=float)
        valid_rows = np.isfinite(waterfall).all(axis=1)
        waterfall = waterfall[valid_rows]
        freqs = freqs[valid_rows]
        order = np.argsort(freqs)
        waterfall = waterfall[order]
        freqs = freqs[order]

        upstream_dm, _ = _run_upstream_dm_phase(
            waterfall,
            trial_dms,
            grid.effective_tsamp_ms / 1000.0,
            freqs,
        )
        sampled_tolerance = step if sampled_tolerance is None else float(sampled_tolerance)
        refined_tolerance = step if refined_tolerance is None else float(refined_tolerance)
        self.assertLessEqual(abs(result.sampled_best_dm - upstream_dm), sampled_tolerance)
        self.assertLessEqual(abs(result.best_dm - upstream_dm), refined_tolerance)
        upstream_index = int(np.argmin(np.abs(trial_dms - upstream_dm)))
        flits_index = int(np.nanargmax(result.snr))
        self.assertLessEqual(abs(flits_index - upstream_index), peak_index_tolerance)

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

    def test_optimize_dm_keeps_quadratic_fit_width_as_metadata_only_under_integer_bin_dedispersion(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)

        result = session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)

        self.assertEqual(result.fit_status, "quadratic_peak_fit")
        self.assertIsNone(result.best_dm_uncertainty)
        self.assertGreater(result.best_dm, float(np.min(result.trial_dms)))
        self.assertLess(result.best_dm, float(np.max(result.trial_dms)))
        detail = result.uncertainty_details["best_dm"]
        self.assertEqual(detail.classification, "heuristic_local_fit")
        self.assertFalse(detail.publishable)
        self.assertIsNotNone(detail.value)
        self.assertGreater(detail.value or 0.0, 0.0)
        self.assertIn("integer_bin_dedispersion", detail.warning_flags)

    def test_optimize_dm_falls_back_when_peak_hits_sweep_edge(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)

        result = session.optimize_dm(center_dm=47.0, half_range=2.0, step=1.0)

        self.assertEqual(result.fit_status, "peak_on_sweep_edge")
        self.assertEqual(result.best_dm, result.sampled_best_dm)
        self.assertIsNone(result.best_dm_uncertainty)
        self.assertEqual(result.uncertainty_details["best_dm"].classification, "heuristic_local_fit")

    def test_dm_optimization_clears_on_resolution_data_and_selection_changes(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0)
        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)

        session.set_time_factor(2)
        self.assertIsNone(session.dm_optimization)

        session.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        session.set_freq_factor(2)
        self.assertIsNone(session.dm_optimization)

        current = _synthetic_dm_session(true_dm=50.0)
        current.optimize_dm(center_dm=50.0, half_range=4.0, step=0.5)
        current.add_peak_ms(current.bin_to_ms(120))
        self.assertIsNotNone(current.dm_optimization)

        current.add_region_ms(current.bin_to_ms(116), current.bin_to_ms(124))
        self.assertIsNotNone(current.dm_optimization)

        current.set_dm(51.0)
        self.assertIsNone(current.dm_optimization)

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

    def test_optimize_dm_uses_reduced_resolution_in_provenance(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24)
        session.set_time_factor(4)
        session.set_freq_factor(3)

        result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5)

        self.assertIsNotNone(result.provenance)
        self.assertEqual(result.provenance.tsamp_ms, session.tsamp_ms * 4)
        self.assertEqual(result.provenance.freqres_mhz, abs(session.freqres) * 3)

    def test_optimize_dm_reports_metric_and_underdedispersed_residual_slope(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24)
        session.set_dm(25.0)

        result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5)

        self.assertEqual(result.snr_metric, "integrated_event_snr")
        self.assertIsNotNone(result.settings)
        self.assertEqual(result.settings.metric, "integrated_event_snr")
        self.assertIsNotNone(result.provenance)
        self.assertEqual(result.provenance.algorithm_name, "dm_trial_sweep")
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
        self.assertIsNotNone(result.residual_rms_applied_ms)
        self.assertIsNotNone(result.residual_rms_best_ms)
        self.assertIsNotNone(result.residual_slope_applied_ms_per_mhz)
        self.assertIsNotNone(result.residual_slope_best_ms_per_mhz)
        self.assertLess(result.residual_rms_best_ms or 0.0, result.residual_rms_applied_ms or 0.0)

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

    def test_optimize_dm_supports_multiple_metrics(self) -> None:
        metrics = ("integrated_event_snr", "dm_phase")
        for metric in metrics:
            with self.subTest(metric=metric):
                session = _synthetic_dm_session(true_dm=50.0, num_channels=24)
                if metric == "dm_phase":
                    session = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
                result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5, metric=metric)

                self.assertEqual(result.snr_metric, metric)
                self.assertIsNotNone(result.settings)
                self.assertEqual(result.settings.metric, metric)
                self.assertLess(abs(result.best_dm - 50.0), 2.5)
                self.assertTrue(np.isfinite(result.best_sn))

    def test_optimize_dm_reports_component_results_for_complex_burst(self) -> None:
        session = _synthetic_complex_dm_session(true_dm=50.0, noise_std=0.03)

        result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5, metric="dm_phase")

        self.assertEqual(len(result.component_results), 2)
        for component in result.component_results:
            self.assertEqual(component.metric, "dm_phase")
            self.assertEqual(component.trial_dms.size, result.trial_dms.size)
            self.assertEqual(component.metric_values.size, result.snr.size)
            self.assertLess(abs(component.best_dm - 50.0), 5.0)
            self.assertEqual(len(component.event_window_ms), 2)

    def test_dm_phase_uses_reduced_resolution_and_masked_state(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
        session.set_time_factor(4)
        session.set_freq_factor(2)
        session.mask_channel_freq(float(session.freqs[2]))
        session.mask_channel_freq(float(session.freqs[5]))

        result = session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5, metric="dm_phase")

        self.assertEqual(result.snr_metric, "dm_phase")
        self.assertTrue(np.isfinite(result.best_sn))
        self.assertIsNotNone(result.provenance)
        self.assertEqual(result.provenance.tsamp_ms, session.tsamp_ms * 4)
        self.assertEqual(result.provenance.freqres_mhz, abs(session.freqres) * 2)
        self.assertGreaterEqual(result.best_dm, float(np.min(result.trial_dms)))
        self.assertLessEqual(result.best_dm, float(np.max(result.trial_dms)))

    def test_dm_phase_rejects_when_too_few_usable_channels_remain(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
        for freq in session.freqs[1:]:
            session.mask_channel_freq(float(freq))

        with self.assertRaisesRegex(ValueError, "Unable to compute DM sweep"):
            session.optimize_dm(center_dm=50.0, half_range=8.0, step=0.5, metric="dm_phase")

    @unittest.skipUnless(UPSTREAM_DMPHASE_AVAILABLE, "DM_phase package is not installed")
    def test_dm_phase_matches_upstream_on_single_burst(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
        self._compare_with_upstream_dmphase(session, center_dm=50.0, half_range=8.0, step=0.5)

    @unittest.skipUnless(UPSTREAM_DMPHASE_AVAILABLE, "DM_phase package is not installed")
    def test_dm_phase_matches_upstream_on_complex_burst(self) -> None:
        session = _synthetic_complex_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
        self._compare_with_upstream_dmphase(session, center_dm=50.0, half_range=8.0, step=0.5)

    @unittest.skipUnless(UPSTREAM_DMPHASE_AVAILABLE, "DM_phase package is not installed")
    def test_dm_phase_matches_upstream_with_masking(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
        session.mask_channel_freq(float(session.freqs[3]))
        session.mask_channel_freq(float(session.freqs[7]))
        self._compare_with_upstream_dmphase(session, center_dm=50.0, half_range=8.0, step=0.5)

    @unittest.skipUnless(UPSTREAM_DMPHASE_AVAILABLE, "DM_phase package is not installed")
    def test_dm_phase_matches_upstream_at_reduced_resolution(self) -> None:
        session = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
        session.set_time_factor(4)
        session.set_freq_factor(2)
        self._compare_with_upstream_dmphase(
            session,
            center_dm=50.0,
            half_range=8.0,
            step=0.5,
            sampled_tolerance=1.5,
            refined_tolerance=1.5,
            peak_index_tolerance=3,
        )


if __name__ == "__main__":
    unittest.main()
