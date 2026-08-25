from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from flits.analysis.drift import DriftAnalysisInputs, run_drift_analysis
from flits.analysis.drift.core import (
    DM_DELAY_MS_MHZ2,
    dm_equivalent_of_slope,
    dm_slope_sensitivity,
    drift_dm_sensitivity,
    time_frequency_slope,
)
from flits.models import (
    AnalysisSessionSnapshot,
    DriftAnalysisResult,
    DriftAnalysisSettings,
    FilterbankMetadata,
)
from flits.session import BurstSession
from flits.settings import ObservationConfig

TSAMP_MS = 0.1
N_TIME = 256
N_CHAN = 192
FREQ_STEP_MHZ = 1.0
BASE_FREQ_MHZ = 1400.0
EVENT_START = 48
EVENT_END = 208


def _drifting_burst(
    drift_mhz_per_ms: float,
    *,
    sigma_t_ms: float = 1.0,
    sigma_f_mhz: float = 20.0,
    descending: bool = True,
    noise: float = 0.0,
    seed: int = 1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A burst whose emission centroid moves at exactly `drift_mhz_per_ms`.

    Building the burst from a covariance matrix rather than a rotation angle
    makes the injected truth the same quantity the fit reports: for a bivariate
    Gaussian the conditional mean has slope ``Sigma_{f,t} / Sigma_{t,t}``.
    """
    correlation = drift_mhz_per_ms * sigma_t_ms / sigma_f_mhz
    if abs(correlation) >= 1.0:
        raise ValueError("the requested drift is steeper than the burst extent allows")
    covariance = np.array(
        [
            [sigma_t_ms**2, correlation * sigma_t_ms * sigma_f_mhz],
            [correlation * sigma_t_ms * sigma_f_mhz, sigma_f_mhz**2],
        ]
    )
    inverse = np.linalg.inv(covariance)

    time_axis_ms = (np.arange(N_TIME, dtype=float) - N_TIME / 2) * TSAMP_MS
    offsets = np.arange(N_CHAN, dtype=float) * FREQ_STEP_MHZ
    freqs_mhz = BASE_FREQ_MHZ + (offsets[::-1] if descending else offsets)

    time_grid, freq_grid = np.meshgrid(time_axis_ms, freqs_mhz - freqs_mhz.mean(), indexing="xy")
    quadratic = (
        inverse[0, 0] * time_grid**2 + 2.0 * inverse[0, 1] * time_grid * freq_grid + inverse[1, 1] * freq_grid**2
    )
    data = 10.0 * np.exp(-0.5 * quadratic)
    if noise:
        data = data + np.random.default_rng(seed).standard_normal(data.shape) * noise
    return data, time_axis_ms, freqs_mhz


def _offpulse_bins() -> np.ndarray:
    return np.concatenate([np.arange(0, EVENT_START), np.arange(EVENT_END, N_TIME)])


def _inputs(
    data: np.ndarray,
    time_axis_ms: np.ndarray,
    freqs_mhz: np.ndarray,
    **overrides: object,
) -> DriftAnalysisInputs:
    payload: dict[str, object] = {
        "waterfall": data,
        "time_axis_ms": time_axis_ms,
        "freqs_mhz": freqs_mhz,
        "event_rel_start": EVENT_START,
        "event_rel_end": EVENT_END,
        "spec_lo": 0,
        "spec_hi": data.shape[0] - 1,
        "offpulse_bins": _offpulse_bins(),
        "dm_pc_cm3": 500.0,
    }
    payload.update(overrides)
    return DriftAnalysisInputs(**payload)  # type: ignore[arg-type]


class DriftAcfEstimatorTest(unittest.TestCase):
    def test_recovers_injected_drift_without_noise(self) -> None:
        for truth in (-12.0, -3.0, 0.0, 5.0, 15.0):
            with self.subTest(drift=truth):
                data, time_axis_ms, freqs_mhz = _drifting_burst(truth)
                result = run_drift_analysis(
                    _inputs(data, time_axis_ms, freqs_mhz),
                    DriftAnalysisSettings(monte_carlo_trials=0),
                )
                self.assertEqual(result.status, "ok")
                assert result.drift_rate_mhz_per_ms is not None
                self.assertAlmostEqual(result.drift_rate_mhz_per_ms, truth, places=3)

    def test_recovers_burst_extent_from_the_autocorrelation_width(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-8.0, sigma_t_ms=0.8, sigma_f_mhz=25.0)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        assert result.acf_sigma_time_ms is not None
        assert result.acf_sigma_freq_mhz is not None
        self.assertAlmostEqual(result.acf_sigma_time_ms, 0.8, places=2)
        self.assertAlmostEqual(result.acf_sigma_freq_mhz, 25.0, places=1)

    def test_descending_and_ascending_frequency_axes_agree(self) -> None:
        descending, time_axis_ms, freqs_desc = _drifting_burst(-9.0, descending=True)
        ascending, _, freqs_asc = _drifting_burst(-9.0, descending=False)
        settings = DriftAnalysisSettings(monte_carlo_trials=0)

        first = run_drift_analysis(_inputs(descending, time_axis_ms, freqs_desc), settings)
        second = run_drift_analysis(_inputs(ascending, time_axis_ms, freqs_asc), settings)
        assert first.drift_rate_mhz_per_ms is not None
        assert second.drift_rate_mhz_per_ms is not None
        self.assertAlmostEqual(first.drift_rate_mhz_per_ms, second.drift_rate_mhz_per_ms, places=3)
        self.assertLess(first.drift_rate_mhz_per_ms, 0.0)

    def test_masked_channels_do_not_bias_the_drift_rate(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-12.0)
        masked = data.copy()
        masked[::7, :] = np.nan
        settings = DriftAnalysisSettings(monte_carlo_trials=0)

        clean = run_drift_analysis(_inputs(data, time_axis_ms, freqs_mhz), settings)
        gappy = run_drift_analysis(_inputs(masked, time_axis_ms, freqs_mhz), settings)
        assert clean.drift_rate_mhz_per_ms is not None
        assert gappy.drift_rate_mhz_per_ms is not None
        self.assertAlmostEqual(gappy.drift_rate_mhz_per_ms, clean.drift_rate_mhz_per_ms, delta=0.05)
        self.assertGreater(gappy.masked_channel_fraction, 0.1)

    def test_monte_carlo_uncertainty_is_seeded_and_scales_with_noise(self) -> None:
        settings = DriftAnalysisSettings(monte_carlo_trials=24)
        quiet_data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0, noise=0.2, seed=3)
        loud_data, _, _ = _drifting_burst(-10.0, noise=1.0, seed=3)

        quiet = run_drift_analysis(_inputs(quiet_data, time_axis_ms, freqs_mhz), settings)
        repeat = run_drift_analysis(_inputs(quiet_data, time_axis_ms, freqs_mhz), settings)
        loud = run_drift_analysis(_inputs(loud_data, time_axis_ms, freqs_mhz), settings)

        assert quiet.drift_rate_statistical_mhz_per_ms is not None
        assert loud.drift_rate_statistical_mhz_per_ms is not None
        self.assertEqual(quiet.monte_carlo_trials_used, 24)
        self.assertEqual(
            quiet.drift_rate_statistical_mhz_per_ms,
            repeat.drift_rate_statistical_mhz_per_ms,
        )
        self.assertGreater(
            loud.drift_rate_statistical_mhz_per_ms,
            2.0 * quiet.drift_rate_statistical_mhz_per_ms,
        )

    def test_noisy_measurement_lands_within_its_own_error_bar(self) -> None:
        settings = DriftAnalysisSettings(monte_carlo_trials=32)
        for seed in range(4):
            with self.subTest(seed=seed):
                data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0, noise=0.5, seed=seed)
                result = run_drift_analysis(_inputs(data, time_axis_ms, freqs_mhz), settings)
                assert result.drift_rate_mhz_per_ms is not None
                assert result.drift_rate_statistical_mhz_per_ms is not None
                deviation = abs(result.drift_rate_mhz_per_ms + 10.0)
                self.assertLess(deviation, 5.0 * result.drift_rate_statistical_mhz_per_ms)

    def test_short_event_window_is_reported_rather_than_fitted(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, event_rel_start=100, event_rel_end=104),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "insufficient_time_bins")
        self.assertIsNone(result.drift_rate_mhz_per_ms)
        self.assertEqual(result.drift_rate_status, "unavailable")

    def test_narrow_spectral_selection_is_reported_rather_than_fitted(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, spec_lo=10, spec_hi=13),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "insufficient_channels")

    def test_fully_masked_event_window_is_reported(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0)
        blank = np.full_like(data, np.nan)
        result = run_drift_analysis(
            _inputs(blank, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "insufficient_channels")
        self.assertIn("heavily_masked", result.warning_flags)

    def test_a_non_uniform_frequency_axis_is_refused_rather_than_averaged(self) -> None:
        """The lag axis is index counts times one channel width, so it must be uniform."""
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0)
        irregular = freqs_mhz.copy()
        irregular[N_CHAN // 2 :] += 40.0
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, irregular),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "invalid_axes")
        self.assertIsNone(result.drift_rate_mhz_per_ms)

    def test_a_tiny_float_jitter_on_the_axes_is_tolerated(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0)
        jittered = freqs_mhz + np.linspace(-1e-6, 1e-6, freqs_mhz.size)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, jittered),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "ok")


class DriftDmDegeneracyTest(unittest.TestCase):
    @staticmethod
    def _sheared(covariance: np.ndarray, dm: float, reference_mhz: float) -> np.ndarray:
        """The burst covariance after a dedispersion error of ``dm``.

        A DM error adds ``g (nu - nu_ref)`` to every arrival time, with
        ``g = -2 k dm nu^-3``, which is a shear of the (time, frequency)
        covariance.
        """
        shear = np.array([[1.0, -2.0 * DM_DELAY_MS_MHZ2 * dm / reference_mhz**3], [0.0, 1.0]])
        return shear @ covariance @ shear.T

    def test_dm_equivalent_reproduces_the_measured_slope(self) -> None:
        """The reported DM offset must actually generate the measured tilt."""
        reference_mhz = 1500.0
        slope_ms_per_mhz = -0.0082
        dm_offset = dm_equivalent_of_slope(slope_ms_per_mhz, reference_mhz)
        induced = -2.0 * DM_DELAY_MS_MHZ2 * dm_offset / reference_mhz**3
        self.assertAlmostEqual(induced, slope_ms_per_mhz, places=9)
        self.assertGreater(dm_offset, 0.0)

    def test_dm_equivalent_is_the_offset_that_nulls_the_tilt(self) -> None:
        """Adding the reported DM to the applied DM must flatten the burst."""
        reference_mhz = 1500.0
        sigma_t, sigma_f, correlation = 1.2, 80.0, -0.55
        covariance = np.array(
            [
                [sigma_t**2, correlation * sigma_t * sigma_f],
                [correlation * sigma_t * sigma_f, sigma_f**2],
            ]
        )
        reported = dm_equivalent_of_slope(time_frequency_slope(sigma_t, sigma_f, correlation), reference_mhz)
        nulled = self._sheared(covariance, -reported, reference_mhz)
        self.assertAlmostEqual(nulled[0, 1] / nulled[1, 1], 0.0, places=9)

    def test_the_slope_not_the_drift_rate_is_linear_in_dm(self) -> None:
        """The reciprocal of the drift rate is not the DM-linear quantity."""
        reference_mhz = 1500.0
        covariance = np.array([[1.2**2, -0.55 * 1.2 * 80.0], [-0.55 * 1.2 * 80.0, 80.0**2]])
        slopes = []
        inverse_drifts = []
        for dm in (-1.0, 0.0, 1.0):
            sheared = self._sheared(covariance, dm, reference_mhz)
            slopes.append(sheared[0, 1] / sheared[1, 1])
            inverse_drifts.append(sheared[0, 0] / sheared[0, 1])
        step = dm_slope_sensitivity(reference_mhz)
        self.assertAlmostEqual(slopes[1] - slopes[0], step, places=12)
        self.assertAlmostEqual(slopes[2] - slopes[1], step, places=12)
        self.assertNotAlmostEqual(inverse_drifts[1] - inverse_drifts[0], step, places=6)

    def test_dm_sensitivity_matches_a_finite_difference(self) -> None:
        reference_mhz = 1500.0
        step = 1e-5
        for correlation in (-0.9, -0.55, 0.0, 0.4):
            with self.subTest(correlation=correlation):
                sigma_t, sigma_f = 1.2, 80.0
                covariance = np.array(
                    [
                        [sigma_t**2, correlation * sigma_t * sigma_f],
                        [correlation * sigma_t * sigma_f, sigma_f**2],
                    ]
                )
                before = covariance[0, 1] / covariance[0, 0]
                shifted = self._sheared(covariance, step, reference_mhz)
                numeric = (shifted[0, 1] / shifted[0, 0] - before) / step
                self.assertAlmostEqual(
                    drift_dm_sensitivity(sigma_t, sigma_f, correlation, reference_mhz),
                    numeric,
                    delta=abs(numeric) * 1e-3 + 1e-9,
                )

    def test_without_a_dm_uncertainty_the_drift_is_not_publishable(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0, noise=0.3, seed=5)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=16),
        )
        detail = result.uncertainty_details["drift_rate_mhz_per_ms"]
        self.assertEqual(detail.classification, "statistical_only")
        self.assertFalse(detail.publishable)
        self.assertFalse(detail.is_formal_1sigma)
        self.assertIn("missing_dm_uncertainty", result.warning_flags)
        self.assertIsNone(result.drift_rate_dm_systematic_mhz_per_ms)

    def test_significance_is_judged_against_the_bar_flits_would_report(self) -> None:
        """A DM systematic that swamps the drift must not leave it called constrained."""
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0, noise=0.3, seed=5)
        settings = DriftAnalysisSettings(monte_carlo_trials=8)
        constrained = run_drift_analysis(_inputs(data, time_axis_ms, freqs_mhz, dm_uncertainty_pc_cm3=0.05), settings)
        swamped = run_drift_analysis(_inputs(data, time_axis_ms, freqs_mhz, dm_uncertainty_pc_cm3=200.0), settings)
        self.assertEqual(constrained.drift_rate_status, "ok")
        self.assertEqual(swamped.drift_rate_status, "unconstrained")

    def test_a_dm_uncertainty_promotes_the_drift_and_inflates_the_bar(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0, noise=0.3, seed=5)
        settings = DriftAnalysisSettings(monte_carlo_trials=16)
        bare = run_drift_analysis(_inputs(data, time_axis_ms, freqs_mhz), settings)
        folded = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, dm_uncertainty_pc_cm3=0.5),
            settings,
        )

        detail = folded.uncertainty_details["drift_rate_mhz_per_ms"]
        self.assertEqual(detail.classification, "formal_1sigma")
        self.assertTrue(detail.is_formal_1sigma)
        self.assertTrue(detail.publishable)
        assert folded.drift_rate_dm_systematic_mhz_per_ms is not None
        assert folded.drift_rate_uncertainty_mhz_per_ms is not None
        assert bare.drift_rate_uncertainty_mhz_per_ms is not None
        self.assertGreater(
            folded.drift_rate_uncertainty_mhz_per_ms,
            bare.drift_rate_uncertainty_mhz_per_ms,
        )
        self.assertNotIn("missing_dm_uncertainty", folded.warning_flags)

    def test_drift_within_the_dm_uncertainty_is_flagged_as_unresolved(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-10.0, noise=0.3, seed=5)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, dm_uncertainty_pc_cm3=1000.0),
            DriftAnalysisSettings(monte_carlo_trials=16),
        )
        self.assertIn("drift_consistent_with_dm_error", result.warning_flags)
        assert result.message is not None
        self.assertIn("not resolved", result.message)

    def test_dedispersion_error_produces_the_predicted_apparent_drift(self) -> None:
        """A burst with no intrinsic drift, dedispersed wrongly, reports that DM error.

        This is the degeneracy the result set has to be honest about: nothing in
        the dynamic spectrum distinguishes this from a genuinely drifting burst.
        """
        data, time_axis_ms, freqs_mhz = _drifting_burst(0.0, sigma_t_ms=0.6, sigma_f_mhz=60.0)
        dm_error = 40.0
        reference_mhz = float(np.mean(freqs_mhz))
        smeared = np.zeros_like(data)
        for channel, frequency in enumerate(freqs_mhz):
            delay_ms = DM_DELAY_MS_MHZ2 * dm_error * (frequency**-2.0 - reference_mhz**-2.0)
            smeared[channel] = np.interp(
                time_axis_ms - delay_ms,
                time_axis_ms,
                data[channel],
                left=0.0,
                right=0.0,
            )

        result = run_drift_analysis(
            _inputs(smeared, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "ok")
        assert result.drift_rate_mhz_per_ms is not None
        assert result.dm_equivalent_pc_cm3 is not None
        self.assertLess(result.drift_rate_mhz_per_ms, 0.0)
        self.assertAlmostEqual(result.dm_equivalent_pc_cm3, dm_error, delta=0.1 * dm_error)

    def test_a_dedispersion_error_shifts_the_reported_dm_equivalent_by_that_amount(self) -> None:
        """The degeneracy is additive: intrinsic drift plus a DM error read as their sum."""
        data, time_axis_ms, freqs_mhz = _drifting_burst(-6.0, sigma_t_ms=0.6, sigma_f_mhz=60.0)
        reference_mhz = float(np.mean(freqs_mhz))
        dm_error = 6.0
        smeared = np.zeros_like(data)
        for channel, frequency in enumerate(freqs_mhz):
            delay_ms = DM_DELAY_MS_MHZ2 * dm_error * (frequency**-2.0 - reference_mhz**-2.0)
            smeared[channel] = np.interp(
                time_axis_ms - delay_ms,
                time_axis_ms,
                data[channel],
                left=0.0,
                right=0.0,
            )

        settings = DriftAnalysisSettings(monte_carlo_trials=0)
        intrinsic = run_drift_analysis(_inputs(data, time_axis_ms, freqs_mhz), settings)
        shifted = run_drift_analysis(_inputs(smeared, time_axis_ms, freqs_mhz), settings)
        assert intrinsic.dm_equivalent_pc_cm3 is not None
        assert shifted.dm_equivalent_pc_cm3 is not None
        self.assertAlmostEqual(
            shifted.dm_equivalent_pc_cm3 - intrinsic.dm_equivalent_pc_cm3,
            dm_error,
            delta=0.1 * dm_error,
        )

    def test_the_reported_slope_and_drift_rate_describe_the_same_ellipse(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-9.0)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        assert result.acf_slope_ms_per_mhz is not None
        assert result.acf_correlation is not None
        assert result.drift_rate_mhz_per_ms is not None
        # drift = rho * sigma_nu / sigma_t and slope = rho * sigma_t / sigma_nu,
        # so their product is rho^2 -- not 1.
        self.assertAlmostEqual(
            result.acf_slope_ms_per_mhz * result.drift_rate_mhz_per_ms,
            result.acf_correlation**2,
            places=6,
        )


class DriftComponentCentroidTest(unittest.TestCase):
    @staticmethod
    def _three_component_burst() -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[str, tuple[int, int]]]]:
        rng = np.random.default_rng(11)
        time_axis_ms = (np.arange(N_TIME, dtype=float) - N_TIME / 2) * TSAMP_MS
        freqs_mhz = BASE_FREQ_MHZ + np.arange(N_CHAN, dtype=float) * FREQ_STEP_MHZ
        data = rng.standard_normal((N_CHAN, N_TIME)) * 0.2
        # Three components stepping down 30 MHz every 3 ms: -10 MHz/ms.
        for centre_ms, centre_mhz in ((-3.0, 1520.0), (0.0, 1490.0), (3.0, 1460.0)):
            time_grid, freq_grid = np.meshgrid(time_axis_ms - centre_ms, freqs_mhz - centre_mhz, indexing="xy")
            data += 8.0 * np.exp(-0.5 * ((time_grid / 0.4) ** 2 + (freq_grid / 12.0) ** 2))

        def window(low_ms: float, high_ms: float) -> tuple[int, int]:
            return int(np.searchsorted(time_axis_ms, low_ms)), int(np.searchsorted(time_axis_ms, high_ms))

        components = [
            ("Component 1", window(-5.0, -1.5)),
            ("Component 2", window(-1.5, 1.5)),
            ("Component 3", window(1.5, 5.0)),
        ]
        return data, time_axis_ms, freqs_mhz, components

    def test_component_regression_recovers_the_inter_component_drift(self) -> None:
        data, time_axis_ms, freqs_mhz, components = self._three_component_burst()
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, component_windows=components, dm_uncertainty_pc_cm3=0.05),
            DriftAnalysisSettings(monte_carlo_trials=8),
        )
        self.assertEqual(result.component_drift_status, "ok")
        assert result.component_drift_rate_mhz_per_ms is not None
        assert result.component_drift_r_squared is not None
        self.assertAlmostEqual(result.component_drift_rate_mhz_per_ms, -10.0, delta=1.0)
        self.assertGreater(result.component_drift_r_squared, 0.98)
        assert result.component_dm_equivalent_pc_cm3 is not None
        self.assertGreater(result.component_dm_equivalent_pc_cm3, 0.0)
        self.assertEqual(result.component_labels, ["Component 1", "Component 2", "Component 3"])
        self.assertTrue(np.all(np.diff(result.component_times_ms) > 0))
        self.assertIn("component_drift_rate_mhz_per_ms", result.uncertainty_details)

    def test_two_components_report_an_exact_fit_without_an_r_squared(self) -> None:
        data, time_axis_ms, freqs_mhz, components = self._three_component_burst()
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, component_windows=components[:2]),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.component_drift_status, "exactly_two_components")
        self.assertIsNone(result.component_drift_r_squared)
        assert result.component_drift_rate_mhz_per_ms is not None
        self.assertAlmostEqual(result.component_drift_rate_mhz_per_ms, -10.0, delta=1.5)

    def test_the_component_estimator_agrees_with_the_autocorrelation_estimator(self) -> None:
        data, time_axis_ms, freqs_mhz, components = self._three_component_burst()
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, component_windows=components),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        assert result.drift_rate_mhz_per_ms is not None
        assert result.component_drift_rate_mhz_per_ms is not None
        self.assertAlmostEqual(
            result.drift_rate_mhz_per_ms,
            result.component_drift_rate_mhz_per_ms,
            delta=1.5,
        )

    def test_without_components_only_the_autocorrelation_estimator_reports(self) -> None:
        data, time_axis_ms, freqs_mhz, _ = self._three_component_burst()
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.component_drift_status, "insufficient_components")
        self.assertIsNone(result.component_drift_rate_mhz_per_ms)
        self.assertNotIn("component_drift_rate_mhz_per_ms", result.uncertainty_details)


class DriftResultSerializationTest(unittest.TestCase):
    def test_result_survives_a_json_round_trip(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-9.0, noise=0.3, seed=2)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz, dm_uncertainty_pc_cm3=0.4),
            DriftAnalysisSettings(monte_carlo_trials=8, random_seed=99),
        )
        restored = DriftAnalysisResult.from_dict(result.to_dict())
        assert restored is not None

        self.assertEqual(restored.status, result.status)
        self.assertAlmostEqual(restored.drift_rate_mhz_per_ms or 0.0, result.drift_rate_mhz_per_ms or 0.0, places=5)
        self.assertEqual(restored.warning_flags, result.warning_flags)
        assert restored.settings is not None
        self.assertEqual(restored.settings.random_seed, 99)
        self.assertEqual(restored.settings.monte_carlo_trials, 8)
        np.testing.assert_allclose(restored.acf_lag_time_ms, result.acf_lag_time_ms, atol=1e-6)
        self.assertEqual(restored.acf.shape, result.acf.shape)
        self.assertEqual(
            restored.uncertainty_details["drift_rate_mhz_per_ms"].classification,
            result.uncertainty_details["drift_rate_mhz_per_ms"].classification,
        )

    def test_acf_is_thinned_for_transport_but_keeps_matching_axes(self) -> None:
        data, time_axis_ms, freqs_mhz = _drifting_burst(-9.0)
        result = run_drift_analysis(
            _inputs(data, time_axis_ms, freqs_mhz),
            DriftAnalysisSettings(monte_carlo_trials=0),
        )
        self.assertEqual(result.acf.shape, (result.acf_lag_freq_mhz.size, result.acf_lag_time_ms.size))
        self.assertEqual(result.acf_model.shape, result.acf.shape)
        self.assertLessEqual(result.acf_lag_freq_mhz.size, 129)
        self.assertLessEqual(result.acf_lag_time_ms.size, 129)
        # The centre lag must survive thinning, or the peak is not in the plot.
        self.assertIn(0.0, np.round(result.acf_lag_time_ms, 9))
        self.assertIn(0.0, np.round(result.acf_lag_freq_mhz, 9))


def _drift_session(
    drift_mhz_per_ms: float = -10.0,
    noise: float = 0.3,
    path: Path | str = "synthetic_drift.fil",
) -> BurstSession:
    data, _, freqs_mhz = _drifting_burst(drift_mhz_per_ms, noise=noise, seed=13)
    metadata = FilterbankMetadata(
        source_path=Path(path),
        source_name="synthetic_drift",
        tsamp=TSAMP_MS / 1e3,
        freqres=FREQ_STEP_MHZ,
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=FREQ_STEP_MHZ * N_CHAN,
        npol=1,
        freqs_mhz=freqs_mhz,
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="synthetic",
    )
    session = BurstSession(
        config=ObservationConfig.from_preset(dm=500.0, preset_key="generic", sefd_jy=10.0),
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=N_TIME,
        event_start=EVENT_START,
        event_end=EVENT_END,
        spec_ex_lo=0,
        spec_ex_hi=N_CHAN - 1,
        channel_mask=np.zeros(N_CHAN, dtype=bool),
    )
    session.offpulse_regions = [(0, EVENT_START), (EVENT_END, N_TIME)]
    return session


class DriftSessionIntegrationTest(unittest.TestCase):
    def test_session_measures_and_caches_the_drift_rate(self) -> None:
        session = _drift_session()
        result = session.run_drift_analysis(settings=DriftAnalysisSettings(monte_carlo_trials=8))

        self.assertEqual(result.status, "ok")
        assert result.drift_rate_mhz_per_ms is not None
        self.assertAlmostEqual(result.drift_rate_mhz_per_ms, -10.0, delta=1.0)
        self.assertIs(session.drift_analysis, result)
        self.assertEqual(session.drift_settings.monte_carlo_trials, 8)
        self.assertEqual(result.dm_pc_cm3, 500.0)

    def test_selection_changes_discard_the_cached_drift_rate(self) -> None:
        session = _drift_session()
        session.run_drift_analysis(settings=DriftAnalysisSettings(monte_carlo_trials=0))
        self.assertIsNotNone(session.drift_analysis)

        session.invalidate_analysis_state()
        self.assertIsNone(session.drift_analysis)

    def test_burst_regions_feed_the_component_estimator(self) -> None:
        session = _drift_session()
        session.burst_regions = [(EVENT_START, 120), (120, EVENT_END)]
        result = session.run_drift_analysis(settings=DriftAnalysisSettings(monte_carlo_trials=0))
        self.assertEqual(result.component_labels, ["Component 1", "Component 2"])
        self.assertEqual(result.component_drift_status, "exactly_two_components")

    def test_view_and_snapshot_carry_the_drift_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "synthetic_drift.fil"
            source.write_bytes(b"drift-source")
            session = _drift_session(path=source)
            session.run_drift_analysis(settings=DriftAnalysisSettings(monte_carlo_trials=8, random_seed=5))

            view = session.get_view()
            self.assertIsNotNone(view["drift_analysis"])
            self.assertEqual(view["drift_settings"]["random_seed"], 5)

            snapshot = AnalysisSessionSnapshot.from_dict(session.snapshot_dict())
            restored = BurstSession.from_snapshot(
                snapshot,
                loader=lambda *_args, **_kwargs: _drift_session(path=source),
            )
        assert restored.drift_analysis is not None
        assert session.drift_analysis is not None
        self.assertAlmostEqual(
            restored.drift_analysis.drift_rate_mhz_per_ms or 0.0,
            session.drift_analysis.drift_rate_mhz_per_ms or 0.0,
            places=5,
        )
        self.assertEqual(restored.drift_settings.random_seed, 5)

    def test_dm_sweep_uncertainty_is_folded_in_only_while_it_describes_the_applied_dm(self) -> None:
        session = _drift_session()
        self.assertIsNone(session._drift_dm_uncertainty())

        session.dm_optimization = _dm_optimization_stub(best_dm=session.dm, uncertainty=0.4)
        self.assertEqual(session._drift_dm_uncertainty(), 0.4)

        session.dm_optimization = _dm_optimization_stub(best_dm=session.dm + 5.0, uncertainty=0.4)
        self.assertIsNone(session._drift_dm_uncertainty())

    def test_explicit_dm_uncertainty_overrides_the_sweep(self) -> None:
        session = _drift_session()
        session.dm_optimization = _dm_optimization_stub(best_dm=session.dm, uncertainty=0.4)
        result = session.run_drift_analysis(
            settings=DriftAnalysisSettings(monte_carlo_trials=0),
            dm_uncertainty_pc_cm3=1.25,
        )
        self.assertEqual(result.dm_uncertainty_pc_cm3, 1.25)


def _dm_optimization_stub(*, best_dm: float, uncertainty: float):
    from flits.models import DmOptimizationResult

    empty = np.array([], dtype=float)
    return DmOptimizationResult(
        center_dm=best_dm,
        requested_half_range=1.0,
        actual_half_range=1.0,
        step=0.1,
        trial_dms=empty,
        snr=empty,
        snr_metric="integrated_event_snr",
        applied_dm=best_dm,
        sampled_best_dm=best_dm,
        sampled_best_sn=10.0,
        best_dm=best_dm,
        best_dm_uncertainty=uncertainty,
        best_sn=10.0,
        fit_status="ok",
        subband_freqs_mhz=empty,
        arrival_times_applied_ms=empty,
        arrival_times_best_ms=empty,
        residuals_applied_ms=empty,
        residuals_best_ms=empty,
        residual_status="ok",
    )


if __name__ == "__main__":
    unittest.main()
