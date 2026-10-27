from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

from flits.analysis.fitting import model_fit
from flits.analysis.fitting.burst_model import BurstModel, BurstParameters
from flits.analysis.fitting.least_squares import fit_burst_model
from flits.analysis.fitting.model_fit import ModelFitRequestConfig, ModelFitResult
from flits.models import FilterbankMetadata, MeasurementDiagnostics, ModelFitDiagnostics
from flits.session import BurstSession
from flits.settings import ObservationConfig


def _synthetic_scattering_session() -> tuple[BurstSession, float, float]:
    rng = np.random.default_rng(42)
    freqs = np.linspace(1450.0, 1250.0, 16)
    tsamp = 1e-4
    num_time_bins = 512
    times_sec = np.arange(num_time_bins, dtype=float) * tsamp
    width_sec = 0.0012
    tau_sec = 0.0024

    model = BurstModel(freqs, times_sec)
    clean = model.evaluate(
        BurstParameters.from_dict(
            {
                "amplitude": [1.05],
                "arrival_time": [times_sec[260]],
                "burst_width": [width_sec],
                "ref_freq": [float(np.min(freqs))],
                "scattering_timescale": [tau_sec],
            }
        )
    )
    data = clean + rng.normal(0.0, 0.35, size=(freqs.size, num_time_bins))

    metadata = FilterbankMetadata(
        source_path=Path("synthetic_scattering.fil"),
        source_name="synthetic_scattering",
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
        event_start=235,
        event_end=320,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )
    return session, width_sec * 1e3, tau_sec * 1e3


def _synthetic_scattering_dispatch_session() -> BurstSession:
    freqs = np.linspace(1450.0, 1250.0, 16)
    tsamp = 1e-4
    num_time_bins = 512
    time = np.arange(num_time_bins, dtype=float)
    pulse = np.exp(-0.5 * ((time - 260.0) / 12.0) ** 2)
    data = pulse[None, :] * np.linspace(0.8, 1.2, freqs.size)[:, None]

    metadata = FilterbankMetadata(
        source_path=Path("synthetic_scattering_dispatch.fil"),
        source_name="synthetic_scattering_dispatch",
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
        event_start=235,
        event_end=320,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )


_ADAPTER_FREQS = np.linspace(1450.0, 1250.0, 6)
_ADAPTER_OFFPULSE = np.r_[0:12, 36:48]


def _adapter_band(seed: int = 7) -> np.ndarray:
    """A bright scattered burst on a (6, 48) band with unit-variance noise, 1 ms bins."""
    times_sec = np.arange(48, dtype=float) * 1e-3
    clean = BurstModel(_ADAPTER_FREQS, times_sec).evaluate(
        BurstParameters.from_dict(
            {
                "amplitude": [1.2],
                "arrival_time": [0.0225],
                "burst_width": [0.0012],
                "ref_freq": [1250.0],
                "scattering_timescale": [0.0015],
            }
        )
    )
    return clean + np.random.default_rng(seed).normal(0.0, 1.0, size=clean.shape)


def _run_adapter_fit(
    config: ModelFitRequestConfig | None = None,
    *,
    fit_side_effect: Any = None,
) -> tuple[ModelFitResult, Any, Any]:
    """Run the selected-band fit on :func:`_adapter_band`, spying on the model and the solver."""
    with (
        patch.object(model_fit, "BurstModel", wraps=BurstModel) as model_spy,
        patch.object(
            model_fit,
            "fit_burst_model",
            side_effect=fit_side_effect if fit_side_effect is not None else fit_burst_model,
        ) as fit_spy,
    ):
        result = model_fit.fit_model_selected_band(
            selected_band=_adapter_band(),
            freqs_mhz=_ADAPTER_FREQS,
            time_axis_ms=np.arange(48, dtype=float),
            event_rel_start=18,
            event_rel_end=30,
            offpulse_bins=_ADAPTER_OFFPULSE,
            tsamp_ms=1.0,
            peak_rel_bin=23,
            width_guess_ms=1.5,
            config=config if config is not None else ModelFitRequestConfig(),
        )
    return result, model_spy, fit_spy


class ModelFitIntegrationTest(unittest.TestCase):
    def test_fit_model_recovers_intrinsic_width_and_scattering_time(self) -> None:
        session, expected_width_ms, expected_tau_ms = _synthetic_scattering_session()

        results = session.fit_model()

        self.assertEqual(results.diagnostics.model_fit.status, "ok")
        self.assertIsNotNone(results.width_ms_model)
        self.assertIsNotNone(results.tau_sc_ms)
        self.assertAlmostEqual(results.width_ms_model or 0.0, expected_width_ms, delta=0.25)
        self.assertAlmostEqual(results.tau_sc_ms or 0.0, expected_tau_ms, delta=0.35)
        self.assertIsNotNone(results.uncertainties.width_ms_model)
        self.assertIsNotNone(results.uncertainties.tau_sc_ms)
        # The truth lies within a few reported sigma.
        self.assertLess(
            abs((results.width_ms_model or 0.0) - expected_width_ms), 4 * results.uncertainties.width_ms_model
        )
        self.assertLess(abs((results.tau_sc_ms or 0.0) - expected_tau_ms), 4 * results.uncertainties.tau_sc_ms)
        self.assertEqual(results.uncertainty_details["width_ms_model"].classification, "model_hessian")
        self.assertEqual(results.uncertainty_details["tau_sc_ms"].classification, "model_hessian")
        self.assertEqual(
            results.diagnostics.model_fit.uncertainty_details["width_ms_model"].classification, "model_hessian"
        )
        self.assertEqual(results.diagnostics.model_fit.fitter, "flits")
        self.assertEqual(results.diagnostics.model_fit.covariance_basis, "observed_hessian")
        self.assertEqual(
            results.diagnostics.model_fit.parameter_labels,
            ["amplitude1", "arrival_time1", "burst_width1", "scattering_timescale"],
        )
        self.assertEqual(results.diagnostics.model_fit.covariance.shape, (4, 4))
        self.assertAlmostEqual(
            float(np.sqrt(results.diagnostics.model_fit.covariance[2, 2])) * 1e3,
            results.uncertainties.width_ms_model or 0.0,
        )
        self.assertGreater(len(results.diagnostics.model_fit.time_axis_ms), 0)
        self.assertGreater(len(results.diagnostics.model_fit.freq_axis_mhz), 0)
        self.assertEqual(
            len(results.diagnostics.model_fit.time_axis_ms),
            len(results.diagnostics.model_fit.model_profile_sn),
        )
        self.assertEqual(
            results.diagnostics.model_fit.data_dynamic_spectrum_sn.shape,
            results.diagnostics.model_fit.model_dynamic_spectrum_sn.shape,
        )
        self.assertEqual(
            results.diagnostics.model_fit.data_dynamic_spectrum_sn.shape,
            results.diagnostics.model_fit.residual_dynamic_spectrum_sn.shape,
        )
        self.assertEqual(
            results.diagnostics.model_fit.data_dynamic_spectrum_sn.shape,
            (
                len(results.diagnostics.model_fit.freq_axis_mhz),
                len(results.diagnostics.model_fit.time_axis_ms),
            ),
        )
        self.assertEqual(
            results.diagnostics.model_fit.free_parameters,
            ["amplitude", "arrival_time", "burst_width", "scattering_timescale"],
        )
        self.assertEqual(
            results.diagnostics.model_fit.fixed_parameters,
            ["dm", "dm_index", "scattering_index", "spectral_index", "spectral_running"],
        )
        self.assertEqual(results.diagnostics.model_fit.weighting_mode, "none")
        self.assertIsNone(results.diagnostics.model_fit.weight_range)
        self.assertEqual(results.diagnostics.model_fit.weight_range_basis, "none")
        self.assertEqual(results.diagnostics.model_fit.fit_iterations_requested, 1)
        self.assertEqual(results.diagnostics.model_fit.fit_iterations_completed, 1)
        self.assertEqual(results.diagnostics.model_fit.factor_time_upsample, 1)
        self.assertEqual(results.diagnostics.model_fit.factor_freq_upsample, 1)
        self.assertAlmostEqual(results.diagnostics.model_fit.ref_freq_mhz or 0.0, 1250.0)
        self.assertFalse(results.diagnostics.model_fit.is_folded)
        self.assertTrue(results.diagnostics.model_fit.exact_jacobian)

    def test_weighted_fit_records_offpulse_weighting_diagnostics(self) -> None:
        unweighted_session, _, _ = _synthetic_scattering_session()
        weighted_session, _, _ = _synthetic_scattering_session()

        unweighted = unweighted_session.fit_model({"solver": {"weighting_mode": "none"}})
        weighted = weighted_session.fit_model({"solver": {"weighting_mode": "auto"}})

        self.assertEqual(unweighted.diagnostics.model_fit.status, "ok")
        self.assertEqual(weighted.diagnostics.model_fit.status, "ok")
        self.assertEqual(unweighted.diagnostics.model_fit.weighting_mode, "none")
        self.assertEqual(weighted.diagnostics.model_fit.weighting_mode, "auto")
        self.assertEqual(weighted.diagnostics.model_fit.weight_range_basis, "offpulse_bins")
        self.assertIsNotNone(weighted.diagnostics.model_fit.weight_range)
        self.assertIsNotNone(unweighted.width_ms_model)
        self.assertIsNotNone(weighted.width_ms_model)
        self.assertAlmostEqual(weighted.width_ms_model or 0.0, unweighted.width_ms_model or 0.0, delta=0.5)

    def test_diagnostics_keep_microsecond_parameters_through_serialization(self) -> None:
        session, _, _ = _synthetic_scattering_session()
        diagnostics = session.fit_model().diagnostics.model_fit

        payload = json.loads(json.dumps(diagnostics.to_dict()))
        restored = ModelFitDiagnostics.from_dict(payload)

        width_uncertainty = diagnostics.bestfit_uncertainties["burst_width"][0]
        self.assertLess(width_uncertainty, 1e-4)
        self.assertAlmostEqual(
            restored.bestfit_uncertainties["burst_width"][0], width_uncertainty, delta=1e-9 * width_uncertainty
        )
        self.assertEqual(restored.parameter_labels, diagnostics.parameter_labels)
        np.testing.assert_allclose(restored.covariance, diagnostics.covariance, rtol=1e-8)
        self.assertEqual(restored.covariance_basis, diagnostics.covariance_basis)


class ModelFitRequestConfigTest(unittest.TestCase):
    def test_advanced_fit_options_round_trip_through_request_config(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "num_components": 2,
                "free_parameters": ["arrival_time", "burst_width", "dm", "spectral_index"],
                "solver": {
                    "weighting_mode": "manual_range",
                    "weight_range": [3.2, 19.8],
                    "iterations": "3",
                    "factor_time_upsample": "4",
                    "factor_freq_upsample": 2,
                    "ref_freq_mhz": "1375.5",
                    "is_folded": True,
                    "exact_jacobian": False,
                    "max_function_evaluations": "1200",
                },
            }
        )

        self.assertEqual(config.num_components, 2)
        self.assertEqual(config.free_parameters, ["arrival_time", "burst_width", "dm", "spectral_index"])
        self.assertEqual(
            config.fixed_parameters,
            ["amplitude", "scattering_timescale", "dm_index", "scattering_index", "spectral_running"],
        )
        self.assertEqual(config.weighting_mode, "manual_range")
        self.assertEqual(config.weight_range, [3, 20])
        self.assertEqual(config.iterations, 3)
        self.assertEqual(config.factor_time_upsample, 4)
        self.assertEqual(config.factor_freq_upsample, 2)
        self.assertEqual(config.ref_freq_mhz, 1375.5)
        self.assertTrue(config.is_folded)
        self.assertFalse(config.exact_jacobian)
        self.assertEqual(config.max_function_evaluations, 1200)
        payload = config.to_dict()
        self.assertEqual(payload["free_parameters"], ["arrival_time", "burst_width", "dm", "spectral_index"])
        self.assertNotIn("fit_profile", payload)
        self.assertNotIn("weighted_fit", payload)
        self.assertEqual(payload["solver"]["weighting_mode"], "manual_range")
        self.assertEqual(payload["solver"]["weight_range"], [3, 20])
        self.assertEqual(payload["solver"]["iterations"], 3)
        self.assertEqual(payload["solver"]["factor_time_upsample"], 4)
        self.assertEqual(payload["solver"]["factor_freq_upsample"], 2)
        self.assertEqual(payload["solver"]["ref_freq_mhz"], 1375.5)
        self.assertTrue(payload["solver"]["is_folded"])
        self.assertFalse(payload["solver"]["exact_jacobian"])
        self.assertEqual(payload["solver"]["max_function_evaluations"], 1200)

    def test_initial_parameter_source_round_trips(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "initial_parameters": {"arrival_time": [0.01]},
                "initial_parameter_source": "previous_fit",
            }
        )

        self.assertEqual(config.initial_parameter_source, "previous_fit")
        self.assertEqual(config.to_dict()["initial_parameter_source"], "previous_fit")

    def test_default_free_parameters_derive_fixed_parameters(self) -> None:
        config = ModelFitRequestConfig.from_dict({})

        self.assertEqual(config.free_parameters, ["amplitude", "arrival_time", "burst_width", "scattering_timescale"])
        self.assertEqual(
            config.fixed_parameters, ["dm", "dm_index", "scattering_index", "spectral_index", "spectral_running"]
        )
        self.assertEqual(config.weighting_mode, "none")
        self.assertEqual(config.initial_parameter_source, "current_selection")
        self.assertIsNone(config.max_function_evaluations)

    def test_scintillation_filters_non_optimizer_free_parameters(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "scintillation": True,
                "free_parameters": [
                    "amplitude",
                    "dm",
                    "arrival_time",
                    "spectral_index",
                    "spectral_running",
                ],
            }
        )

        self.assertTrue(config.scintillation)
        self.assertEqual(config.free_parameters, ["dm", "arrival_time"])
        self.assertEqual(
            config.fixed_parameters, ["burst_width", "scattering_timescale", "dm_index", "scattering_index"]
        )
        self.assertTrue(config.to_dict()["scintillation"])
        self.assertNotIn("amplitude", config.to_dict()["free_parameters"])

    def test_invalid_iteration_count_falls_back_to_one(self) -> None:
        self.assertEqual(ModelFitRequestConfig.from_dict({"iterations": "bad"}).iterations, 1)
        self.assertEqual(ModelFitRequestConfig.from_dict({"iterations": 0}).iterations, 1)

    def test_invalid_model_controls_fall_back_to_defaults(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "solver": {
                    "factor_time_upsample": 0,
                    "factor_freq_upsample": "bad",
                    "ref_freq_mhz": -1.0,
                    "weighting_mode": "unknown",
                    "max_function_evaluations": "bad",
                },
            }
        )

        # Zero time upsampling means automatic.
        self.assertIsNone(config.factor_time_upsample)
        self.assertEqual(config.factor_freq_upsample, 1)
        self.assertIsNone(config.ref_freq_mhz)
        self.assertEqual(config.weighting_mode, "none")
        self.assertIsNone(config.max_function_evaluations)

    def test_max_function_evaluations_clamps_to_interactive_cap(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {"solver": {"max_function_evaluations": model_fit.MAX_MODEL_FIT_FUNCTION_EVALUATIONS + 1}}
        )

        self.assertEqual(config.max_function_evaluations, model_fit.MAX_MODEL_FIT_FUNCTION_EVALUATIONS)

    def test_non_positive_max_function_evaluations_uses_auto_default(self) -> None:
        self.assertIsNone(
            ModelFitRequestConfig.from_dict({"solver": {"max_function_evaluations": 0}}).max_function_evaluations
        )
        self.assertIsNone(
            ModelFitRequestConfig.from_dict({"solver": {"max_function_evaluations": -4}}).max_function_evaluations
        )

    def test_legacy_bounds_payload_is_ignored_and_not_serialized(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "bounds": {
                    "burst_width": [[0.0, 0.01]],
                    "scattering_timescale": [[0.0, 0.1]],
                },
            }
        )

        self.assertFalse(hasattr(config, "bounds"))
        self.assertNotIn("bounds", config.to_dict())

    def test_valid_free_parameter_payload_is_accepted(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "free_parameters": [
                    "dm",
                    "burst_width",
                    "spectral_running",
                ],
            }
        )

        self.assertEqual(config.free_parameters, ["dm", "burst_width", "spectral_running"])
        self.assertEqual(
            config.fixed_parameters,
            [
                "amplitude",
                "arrival_time",
                "scattering_timescale",
                "dm_index",
                "scattering_index",
                "spectral_index",
            ],
        )

    def test_unknown_free_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown free fit parameters: made_up"):
            ModelFitRequestConfig.from_dict({"free_parameters": ["dm", "made_up"]})

    def test_duplicate_free_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate free fit parameters: dm"):
            ModelFitRequestConfig.from_dict({"free_parameters": ["dm", "dm"]})

    def test_ref_freq_free_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ref_freq is an initialization/reference parameter"):
            ModelFitRequestConfig.from_dict({"free_parameters": ["ref_freq"]})

    def test_empty_free_parameter_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one model parameter must remain free"):
            ModelFitRequestConfig.from_dict({"free_parameters": []})

    def test_manual_weighting_requires_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "manual_range weighting requires"):
            ModelFitRequestConfig.from_dict({"solver": {"weighting_mode": "manual_range"}})

    def test_solution_import_seeds_initial_parameters(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {
                "solution": {
                    "model_parameters": {
                        "arrival_time": [0.01],
                        "burst_width": [0.002],
                    },
                    "fit_statistics": {"snr": 12.0},
                }
            }
        )

        self.assertEqual(config.initial_parameter_source, "imported_solution")
        self.assertEqual(config.initial_parameters, {"arrival_time": [0.01], "burst_width": [0.002]})


class ModelFitIterationAdapterTest(unittest.TestCase):
    def test_iterations_restart_from_the_previous_best_fit(self) -> None:
        result, _model_spy, fit_spy = _run_adapter_fit(ModelFitRequestConfig(iterations=2, factor_time_upsample=1))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.diagnostics.fit_iterations_requested, 2)
        self.assertEqual(result.diagnostics.fit_iterations_completed, 2)
        self.assertEqual(fit_spy.call_count, 2)
        first_start = fit_spy.call_args_list[0].args[2]
        second_start = fit_spy.call_args_list[1].args[2]
        self.assertNotEqual(float(first_start.burst_width[0]), float(second_start.burst_width[0]))
        # The second pass starts at the first pass's optimum and stays there.
        self.assertAlmostEqual(
            result.diagnostics.bestfit_parameters["burst_width"][0], float(second_start.burst_width[0]), places=7
        )
        self.assertAlmostEqual(
            result.width_ms_model or 0.0, result.diagnostics.bestfit_parameters["burst_width"][0] * 1e3
        )

    def test_model_controls_reach_the_model_and_diagnostics(self) -> None:
        result, model_spy, fit_spy = _run_adapter_fit(
            ModelFitRequestConfig(
                iterations=1,
                factor_time_upsample=4,
                factor_freq_upsample=3,
                ref_freq_mhz=1400.0,
                is_folded=True,
                exact_jacobian=False,
            )
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(model_spy.call_count, 1)
        self.assertEqual(model_spy.call_args.kwargs["factor_time_upsample"], 4)
        self.assertEqual(model_spy.call_args.kwargs["factor_freq_upsample"], 3)
        self.assertTrue(model_spy.call_args.kwargs["is_folded"])
        self.assertFalse(fit_spy.call_args.kwargs["exact_jacobian"])
        self.assertEqual(result.diagnostics.factor_time_upsample, 4)
        self.assertEqual(result.diagnostics.factor_freq_upsample, 3)
        self.assertEqual(result.diagnostics.ref_freq_mhz, 1400.0)
        self.assertTrue(result.diagnostics.is_folded)
        self.assertFalse(result.diagnostics.exact_jacobian)
        self.assertEqual(result.diagnostics.bestfit_parameters["ref_freq"], [1400.0])
        # SciPy's default budget: 100 evaluations per fitted value.
        self.assertEqual(result.diagnostics.max_function_evaluations, 400)
        self.assertGreater(result.diagnostics.function_evaluations or 0, 1)
        self.assertIn(result.diagnostics.solver_status, (1, 2, 3, 4))
        self.assertIn("termination condition", result.diagnostics.solver_message or "")

    def test_custom_max_function_evaluations_reach_the_solver(self) -> None:
        result, _model_spy, fit_spy = _run_adapter_fit(ModelFitRequestConfig(max_function_evaluations=1234))

        self.assertEqual(result.status, "ok")
        self.assertEqual(fit_spy.call_args.kwargs["max_function_evaluations"], 1234)
        self.assertEqual(result.diagnostics.max_function_evaluations, 1234)
        self.assertEqual(result.diagnostics.fit_statistics["max_function_evaluations"], 1234)
        self.assertEqual(
            result.diagnostics.fit_statistics["function_evaluations"], result.diagnostics.function_evaluations
        )
        round_tripped = ModelFitDiagnostics.from_dict(result.diagnostics.to_dict())
        self.assertEqual(round_tripped.max_function_evaluations, 1234)
        self.assertEqual(round_tripped.function_evaluations, result.diagnostics.function_evaluations)
        self.assertEqual(round_tripped.solver_status, result.diagnostics.solver_status)
        self.assertEqual(round_tripped.solver_message, result.diagnostics.solver_message)

    def test_evaluation_limit_exceeded_gets_specific_failure_status(self) -> None:
        result, _model_spy, _fit_spy = _run_adapter_fit(ModelFitRequestConfig(max_function_evaluations=1))

        self.assertEqual(result.status, "evaluation_limit_exceeded")
        self.assertEqual(result.message, "The maximum number of function evaluations is exceeded.")
        self.assertEqual(result.diagnostics.max_function_evaluations, 1)
        self.assertEqual(result.diagnostics.function_evaluations, 1)
        self.assertEqual(result.diagnostics.solver_status, 0)
        self.assertEqual(result.diagnostics.solver_message, "The maximum number of function evaluations is exceeded.")

    def test_large_fit_window_fails_before_the_model_is_built(self) -> None:
        channels = model_fit.MIN_FIT_CHANNELS
        time_bins = model_fit.MAX_MODEL_FIT_CELLS // channels + model_fit.MIN_FIT_TIME_BINS
        data = np.zeros((channels, time_bins), dtype=float)

        with patch.object(model_fit, "BurstModel", wraps=BurstModel) as model_spy:
            result = model_fit.fit_model_selected_band(
                selected_band=data,
                freqs_mhz=np.linspace(1450.0, 1250.0, channels),
                time_axis_ms=np.arange(time_bins, dtype=float),
                event_rel_start=0,
                event_rel_end=time_bins,
                offpulse_bins=np.arange(0, min(32, time_bins), dtype=int),
                tsamp_ms=1.0,
                peak_rel_bin=0,
                width_guess_ms=1.0,
                config=ModelFitRequestConfig(),
            )

        self.assertEqual(result.status, "fit_window_too_large")
        self.assertIn("channel-time samples", result.message or "")
        self.assertEqual(model_spy.call_count, 0)

    def test_successful_fit_downsamples_diagnostic_dynamic_spectra(self) -> None:
        with (
            patch.object(model_fit, "MAX_MODEL_FIT_DIAGNOSTIC_CELLS", 60),
            patch.object(model_fit, "MAX_MODEL_FIT_DIAGNOSTIC_FREQ_BINS", 4),
            patch.object(model_fit, "MAX_MODEL_FIT_DIAGNOSTIC_TIME_BINS", 20),
        ):
            result, _model_spy, _fit_spy = _run_adapter_fit()

        diagnostics = result.diagnostics
        self.assertEqual(result.status, "ok")
        self.assertLessEqual(diagnostics.data_dynamic_spectrum_sn.size, 60)
        self.assertEqual(diagnostics.data_dynamic_spectrum_sn.shape, diagnostics.model_dynamic_spectrum_sn.shape)
        self.assertEqual(diagnostics.data_dynamic_spectrum_sn.shape, diagnostics.residual_dynamic_spectrum_sn.shape)
        self.assertEqual(diagnostics.data_dynamic_spectrum_sn.shape[0], len(diagnostics.freq_axis_mhz))
        self.assertEqual(diagnostics.data_dynamic_spectrum_sn.shape[1], len(diagnostics.time_axis_ms))
        self.assertEqual(len(diagnostics.data_profile_sn), len(diagnostics.time_axis_ms))
        self.assertEqual(len(diagnostics.data_freq_profile_sn), len(diagnostics.freq_axis_mhz))
        self.assertEqual(diagnostics.fit_statistics["diagnostic_num_cells"], diagnostics.data_dynamic_spectrum_sn.size)
        json.dumps(diagnostics.to_dict())

    def test_scintillation_reaches_the_model_and_excludes_inactive_parameters(self) -> None:
        result, model_spy, _fit_spy = _run_adapter_fit(
            ModelFitRequestConfig.from_dict(
                {
                    "scintillation": True,
                    "free_parameters": [
                        "amplitude",
                        "arrival_time",
                        "burst_width",
                        "scattering_timescale",
                        "spectral_index",
                        "spectral_running",
                    ],
                }
            )
        )

        self.assertEqual(result.status, "ok")
        self.assertTrue(model_spy.call_args.kwargs["scintillation"])
        self.assertTrue(result.diagnostics.scintillation)
        self.assertEqual(result.diagnostics.free_parameters, ["arrival_time", "burst_width", "scattering_timescale"])
        self.assertEqual(result.diagnostics.fixed_parameters, ["dm", "dm_index", "scattering_index"])
        self.assertEqual(result.diagnostics.fit_parameters, ["arrival_time", "burst_width", "scattering_timescale"])
        # One solved amplitude per diagnostic channel and component.
        self.assertEqual(result.diagnostics.channel_amplitudes.shape, (len(result.diagnostics.freq_axis_mhz), 1))
        self.assertTrue(np.all(result.diagnostics.channel_amplitudes > 0))
        self.assertEqual(result.diagnostics.fit_statistics["num_linear_parameters"], 6)
        restored = ModelFitDiagnostics.from_dict(json.loads(json.dumps(result.diagnostics.to_dict())))
        np.testing.assert_allclose(restored.channel_amplitudes, result.diagnostics.channel_amplitudes, rtol=1e-5)

    def test_weighting_modes_reach_the_solver_and_diagnostics(self) -> None:
        auto, _model_spy, fit_spy = _run_adapter_fit(
            ModelFitRequestConfig.from_dict({"solver": {"weighting_mode": "auto"}})
        )
        self.assertEqual(auto.diagnostics.weighting_mode, "auto")
        self.assertEqual(auto.diagnostics.weight_range_basis, "offpulse_bins")
        self.assertEqual(auto.diagnostics.weight_range, [0, 48])
        auto_weights = fit_spy.call_args.kwargs["weights"]
        self.assertTrue(np.all(auto_weights > 0))

        fit_window, _model_spy, fit_spy = _run_adapter_fit(
            ModelFitRequestConfig.from_dict({"solver": {"weighting_mode": "fit_window"}})
        )
        self.assertEqual(fit_window.diagnostics.weighting_mode, "fit_window")
        self.assertEqual(fit_window.diagnostics.weight_range_basis, "fit_window")
        fit_data = np.asarray(fit_spy.call_args.args[1])
        # fitburst's weights: inverse RMS over all but the last bin of the fitted window.
        np.testing.assert_allclose(
            fit_spy.call_args.kwargs["weights"], 1.0 / np.sqrt(np.mean(fit_data[:, :-1] ** 2, axis=1))
        )

        manual, _model_spy, fit_spy = _run_adapter_fit(
            ModelFitRequestConfig.from_dict({"solver": {"weighting_mode": "manual_range", "weight_range": [1, 5]}})
        )
        self.assertEqual(manual.diagnostics.weighting_mode, "manual_range")
        self.assertEqual(manual.diagnostics.weight_range_basis, "manual_range")
        self.assertEqual(manual.diagnostics.weight_range, [1, 5])
        fit_data = np.asarray(fit_spy.call_args.args[1])
        # The range is in event-window bins; the fit window starts 12 bins earlier.
        np.testing.assert_allclose(
            fit_spy.call_args.kwargs["weights"], 1.0 / np.sqrt(np.mean(fit_data[:, 13:17] ** 2, axis=1))
        )

        unweighted, _model_spy, fit_spy = _run_adapter_fit()
        self.assertEqual(unweighted.diagnostics.weighting_mode, "none")
        np.testing.assert_array_equal(fit_spy.call_args.kwargs["weights"], np.ones(6))

    def test_failed_intermediate_iteration_returns_completed_bestfit_diagnostics(self) -> None:
        calls: list[Any] = []

        def fail_second_pass(*args: Any, **kwargs: Any) -> Any:
            fit = fit_burst_model(*args, **kwargs)
            calls.append(fit)
            if len(calls) == 2:
                return replace(fit, success=False, status=2, message="failed iteration 2")
            return fit

        result, _model_spy, _fit_spy = _run_adapter_fit(
            ModelFitRequestConfig(iterations=3), fit_side_effect=fail_second_pass
        )

        self.assertEqual(result.status, "fit_failed")
        self.assertEqual(result.message, "failed iteration 2")
        self.assertEqual(result.diagnostics.fit_iterations_requested, 3)
        self.assertEqual(result.diagnostics.fit_iterations_completed, 1)
        self.assertAlmostEqual(
            result.diagnostics.bestfit_parameters["burst_width"][0], float(calls[0].parameters.burst_width[0])
        )
        json.dumps(result.diagnostics.to_dict())
        self.assertIsNone(result.width_ms_model)

    def test_solver_exception_records_sanitized_failure_diagnostics(self) -> None:
        def raise_on_second_pass(*args: Any, **kwargs: Any) -> Any:
            if raise_on_second_pass.calls:  # type: ignore[attr-defined]
                raise RuntimeError("optimizer exploded \x00" + ("b" * 5000))
            raise_on_second_pass.calls += 1  # type: ignore[attr-defined]
            return fit_burst_model(*args, **kwargs)

        raise_on_second_pass.calls = 0  # type: ignore[attr-defined]
        result, _model_spy, _fit_spy = _run_adapter_fit(
            ModelFitRequestConfig(iterations=2), fit_side_effect=raise_on_second_pass
        )

        self.assertEqual(result.status, "fit_failed")
        self.assertEqual(result.message, "The model fit raised an exception.")
        self.assertIn("RuntimeError: optimizer exploded", result.diagnostics.failure_exception or "")
        self.assertLessEqual(len(result.diagnostics.failure_exception or ""), model_fit.MAX_FAILURE_LOG_CHARS)
        self.assertNotIn("\x00", result.diagnostics.failure_exception or "")
        self.assertIn("diagnostic output truncated", result.diagnostics.failure_exception or "")
        self.assertEqual(result.diagnostics.fit_iterations_completed, 1)
        json.dumps(result.diagnostics.to_dict())

    def test_invalid_starting_width_is_reported_as_invalid_config(self) -> None:
        result, _model_spy, _fit_spy = _run_adapter_fit(
            ModelFitRequestConfig(initial_parameters={"burst_width": [-0.001]}, initial_parameter_source="api")
        )

        self.assertEqual(result.status, "invalid_config")
        self.assertIn("burst_width", result.message or "")


class ModelFitDispatchTest(unittest.TestCase):
    def test_model_fit_guess_uses_component_regions(self) -> None:
        session = _synthetic_scattering_dispatch_session()
        session.add_region_ms(session.bin_to_ms(245), session.bin_to_ms(260))
        session.add_region_ms(session.bin_to_ms(275), session.bin_to_ms(310))

        guess = session.get_view()["model_fit_guess"]

        self.assertEqual(guess["status"], "ok")
        self.assertEqual(guess["source"], "component_regions")
        self.assertEqual(guess["component_count"], 2)
        arrivals = [row["arrival_time_ms"] for row in guess["component_guesses"]]
        widths = [row["width_ms"] for row in guess["component_guesses"]]
        self.assertNotEqual(arrivals[0], arrivals[1])
        self.assertGreater(widths[0], 0.0)
        self.assertGreater(widths[1], 0.0)
        self.assertNotEqual(widths[0], widths[1])
        self.assertEqual(len(guess["initial_parameters"]["arrival_time"]), 2)

    def test_model_fit_guess_uses_manual_peaks_without_regions(self) -> None:
        session = _synthetic_scattering_dispatch_session()
        first_peak = session.bin_to_ms(250)
        second_peak = session.bin_to_ms(290)
        session.add_peak_ms(first_peak)
        session.add_peak_ms(second_peak)

        guess = session.get_view()["model_fit_guess"]

        self.assertEqual(guess["status"], "ok")
        self.assertEqual(guess["source"], "manual_peaks")
        self.assertEqual(guess["component_count"], 2)
        arrivals = [row["arrival_time_ms"] for row in guess["component_guesses"]]
        self.assertEqual(arrivals, [first_peak, second_peak])
        windows = [row["component_window_ms"] for row in guess["component_guesses"]]
        self.assertLess(windows[0][1], windows[1][1])
        self.assertAlmostEqual(windows[0][1], windows[1][0], places=6)

    def test_model_fit_guess_falls_back_to_single_automatic_component(self) -> None:
        session = _synthetic_scattering_dispatch_session()

        guess = session.get_view()["model_fit_guess"]

        self.assertEqual(guess["status"], "ok")
        self.assertEqual(guess["source"], "automatic")
        self.assertEqual(guess["component_count"], 1)
        self.assertEqual(len(guess["component_guesses"]), 1)
        self.assertEqual(len(guess["initial_parameters"]["arrival_time"]), 1)

    @patch("flits.session.fit_model_selected_band")
    def test_fit_model_uses_reduced_grid(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        session.set_time_factor(4)
        session.set_freq_factor(2)

        mock_fit.return_value = ModelFitResult(
            status="ok",
            message=None,
            width_ms_model=1.0,
            width_uncertainty_ms=0.1,
            tau_sc_ms=2.0,
            tau_uncertainty_ms=0.2,
            diagnostics=ModelFitDiagnostics(
                status="ok",
                message=None,
                fitter="flits",
                component_count=1,
            ),
        )

        results = session.fit_model()

        kwargs = mock_fit.call_args.kwargs
        self.assertEqual(kwargs["selected_band"].shape[0], session.total_channels // 2)
        self.assertEqual(kwargs["tsamp_ms"], session.tsamp_ms * 4)
        self.assertEqual(kwargs["freqs_mhz"].shape[0], session.total_channels // 2)
        self.assertEqual(results.width_ms_model, 1.0)
        self.assertEqual(results.tau_sc_ms, 2.0)

    @patch("flits.session.fit_model_selected_band")
    def test_fit_model_converts_component_guesses_to_initial_parameters(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        first_arrival_ms = session.bin_to_ms(250)
        second_arrival_ms = session.bin_to_ms(290)

        mock_fit.return_value = ModelFitResult(
            status="ok",
            message=None,
            width_ms_model=1.0,
            width_uncertainty_ms=0.1,
            tau_sc_ms=2.0,
            tau_uncertainty_ms=0.2,
            diagnostics=ModelFitDiagnostics(
                status="ok",
                message=None,
                fitter="flits",
                component_count=2,
                initial_parameters={},
            ),
        )

        session.fit_model(
            {
                "num_components": 2,
                "free_parameters": [
                    "amplitude",
                    "arrival_time",
                    "burst_width",
                    "scattering_timescale",
                    "dm_index",
                    "scattering_index",
                    "spectral_index",
                    "spectral_running",
                ],
                "component_guesses": [
                    {
                        "arrival_time_ms": first_arrival_ms,
                        "width_ms": 1.2,
                        "tau_ms": 0.4,
                        "log_amplitude": 0.7,
                        "dm": 0.1,
                        "dm_index": -2.1,
                        "scattering_index": -4.1,
                        "spectral_index": 0.2,
                        "spectral_running": 0.01,
                        "ref_freq_mhz": 1300.0,
                    },
                    {
                        "arrival_time_ms": second_arrival_ms,
                        "width_ms": 1.8,
                        "tau_ms": 0.5,
                        "log_amplitude": 0.6,
                        "dm": 0.2,
                        "dm_index": -2.2,
                        "scattering_index": -4.2,
                        "spectral_index": 0.3,
                        "spectral_running": 0.02,
                        "ref_freq_mhz": 1310.0,
                    },
                ],
            }
        )

        config = mock_fit.call_args.kwargs["config"]
        self.assertEqual(config.num_components, 2)
        self.assertEqual(config.fixed_parameters, ["dm"])
        self.assertAlmostEqual(config.initial_parameters["arrival_time"][0], first_arrival_ms / 1e3)
        self.assertAlmostEqual(config.initial_parameters["arrival_time"][1], second_arrival_ms / 1e3)
        self.assertEqual(config.initial_parameters["burst_width"], [0.0012, 0.0018])
        self.assertEqual(config.initial_parameters["scattering_timescale"], [0.0004, 0.0005])
        self.assertEqual(config.initial_parameters["amplitude"], [0.7, 0.6])
        self.assertEqual(config.initial_parameters["dm"], [0.1, 0.2])
        self.assertEqual(config.initial_parameters["dm_index"], [-2.1, -2.2])
        self.assertEqual(config.initial_parameters["scattering_index"], [-4.1, -4.2])
        self.assertEqual(config.initial_parameters["spectral_index"], [0.2, 0.3])
        self.assertEqual(config.initial_parameters["spectral_running"], [0.01, 0.02])
        self.assertEqual(config.initial_parameters["ref_freq"], [1300.0, 1310.0])

    @patch("flits.session.fit_model_selected_band")
    def test_fit_model_passes_advanced_fit_config(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()

        mock_fit.return_value = ModelFitResult(
            status="ok",
            message=None,
            width_ms_model=1.0,
            width_uncertainty_ms=0.1,
            tau_sc_ms=2.0,
            tau_uncertainty_ms=0.2,
            diagnostics=ModelFitDiagnostics(
                status="ok",
                message=None,
                fitter="flits",
                component_count=1,
            ),
        )

        session.fit_model(
            {
                "free_parameters": ["arrival_time", "burst_width", "dm", "spectral_index"],
                "solver": {
                    "weighting_mode": "manual_range",
                    "weight_range": [2, 20],
                    "iterations": 3,
                    "factor_time_upsample": 2,
                    "factor_freq_upsample": 4,
                    "ref_freq_mhz": 1333.0,
                    "is_folded": True,
                    "exact_jacobian": False,
                },
            }
        )

        config = mock_fit.call_args.kwargs["config"]
        self.assertEqual(config.free_parameters, ["arrival_time", "burst_width", "dm", "spectral_index"])
        self.assertEqual(config.weighting_mode, "manual_range")
        self.assertEqual(config.weight_range, [2, 20])
        self.assertEqual(config.iterations, 3)
        self.assertEqual(config.factor_time_upsample, 2)
        self.assertEqual(config.factor_freq_upsample, 4)
        self.assertEqual(config.ref_freq_mhz, 1333.0)
        self.assertTrue(config.is_folded)
        self.assertFalse(config.exact_jacobian)

    @patch("flits.session.fit_model_selected_band")
    def test_fit_model_can_seed_from_previous_successful_fit(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        previous_arrival_ms = session.bin_to_ms(260)
        mock_fit.side_effect = [
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.2,
                width_uncertainty_ms=None,
                tau_sc_ms=2.4,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=2,
                    bestfit_parameters={
                        "amplitude": [0.9, 0.8],
                        "arrival_time": [previous_arrival_ms / 1e3, session.bin_to_ms(280) / 1e3],
                        "burst_width": [0.0012, 0.0014],
                        "dm": [0.0, 0.0],
                        "dm_index": [-2.0, -2.0],
                        "ref_freq": [1250.0, 1250.0],
                        "scattering_timescale": [0.0024, 0.0026],
                        "scattering_index": [-4.0, -4.0],
                        "spectral_index": [0.2, 0.1],
                        "spectral_running": [0.0, 0.0],
                    },
                ),
            ),
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.1,
                width_uncertainty_ms=None,
                tau_sc_ms=2.1,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=1,
                ),
            ),
        ]

        session.fit_model()
        session.fit_model({"seed_from_previous_fit": True, "num_components": 1})

        config = mock_fit.call_args.kwargs["config"]
        self.assertEqual(config.initial_parameter_source, "previous_fit")
        self.assertEqual(config.num_components, 1)
        self.assertEqual(config.initial_parameters["arrival_time"], [previous_arrival_ms / 1e3])
        self.assertEqual(config.initial_parameters["burst_width"], [0.0012])
        self.assertEqual(config.initial_parameters["ref_freq"], [1250.0])

    @patch("flits.session.fit_model_selected_band")
    def test_previous_fit_seed_sanitizes_pathological_timescales_and_indices(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        previous_arrival_ms = session.bin_to_ms(260)
        mock_fit.side_effect = [
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.2,
                width_uncertainty_ms=None,
                tau_sc_ms=100.0,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=1,
                    bestfit_parameters={
                        "amplitude": [0.9],
                        "arrival_time": [previous_arrival_ms / 1e3],
                        "burst_width": [0.0012],
                        "dm": [0.003],
                        "dm_index": [-0.001],
                        "ref_freq": [1250.0],
                        "scattering_timescale": [0.1],
                        "scattering_index": [-20.0],
                        "spectral_index": [21.0],
                        "spectral_running": [-41.0],
                    },
                ),
            ),
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.1,
                width_uncertainty_ms=None,
                tau_sc_ms=2.1,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=1,
                ),
            ),
        ]

        session.fit_model()
        session.fit_model({"seed_from_previous_fit": True, "num_components": 1})

        config = mock_fit.call_args.kwargs["config"]
        self.assertEqual(config.initial_parameter_source, "previous_fit")
        self.assertEqual(config.initial_parameters["arrival_time"], [previous_arrival_ms / 1e3])
        self.assertEqual(config.initial_parameters["burst_width"], [0.0012])
        self.assertNotEqual(config.initial_parameters["scattering_timescale"], [0.1])
        self.assertEqual(config.initial_parameters["dm"], [0.003])
        self.assertEqual(config.initial_parameters["dm_index"], [-2.0])
        self.assertEqual(config.initial_parameters["scattering_index"], [-4.0])
        self.assertEqual(config.initial_parameters["spectral_index"], [0.0])
        self.assertEqual(config.initial_parameters["spectral_running"], [0.0])

    @patch("flits.session.fit_model_selected_band")
    def test_incompatible_previous_fit_seed_falls_back_to_current_guesses(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        mock_fit.side_effect = [
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.2,
                width_uncertainty_ms=None,
                tau_sc_ms=2.4,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=1,
                    bestfit_parameters={
                        "arrival_time": [0.001],
                        "burst_width": [0.0012],
                    },
                ),
            ),
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.1,
                width_uncertainty_ms=None,
                tau_sc_ms=2.1,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=1,
                ),
            ),
        ]

        session.fit_model()
        session.fit_model({"seed_from_previous_fit": True, "num_components": 1})

        config = mock_fit.call_args.kwargs["config"]
        self.assertEqual(config.initial_parameter_source, "current_selection")
        self.assertIsNotNone(config.initial_parameters)
        self.assertNotEqual(config.initial_parameters["arrival_time"], [0.001])

    @patch("flits.session.fit_model_selected_band")
    def test_failed_refit_preserves_previous_successful_fit_values(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        mock_fit.side_effect = [
            ModelFitResult(
                status="ok",
                message=None,
                width_ms_model=1.0,
                width_uncertainty_ms=None,
                tau_sc_ms=2.0,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="flits",
                    component_count=1,
                ),
            ),
            ModelFitResult(
                status="fit_failed",
                message="failed iteration 2",
                width_ms_model=None,
                width_uncertainty_ms=None,
                tau_sc_ms=None,
                tau_uncertainty_ms=None,
                diagnostics=ModelFitDiagnostics(
                    status="fit_failed",
                    message="failed iteration 2",
                    fitter="flits",
                    component_count=1,
                    fit_iterations_requested=3,
                    fit_iterations_completed=1,
                ),
            ),
        ]

        session.fit_model()
        results = session.fit_model({"solver": {"iterations": 3}})

        self.assertEqual(results.width_ms_model, 1.0)
        self.assertEqual(results.tau_sc_ms, 2.0)
        self.assertEqual(results.diagnostics.model_fit.status, "fit_failed")
        self.assertEqual(results.diagnostics.model_fit.fit_iterations_completed, 1)


if __name__ == "__main__":
    unittest.main()


# Fit window, time resolution and component-count comparison.

_GUIDANCE_FREQS = np.linspace(1450.0, 1250.0, 16)
_GUIDANCE_OFFPULSE = np.r_[0:20, 108:128]


def _guidance_band(components: list[tuple[float, float, float]], *, tau_ms: float = 0.0, seed: int = 3) -> np.ndarray:
    """Components (log amplitude, arrival ms, width ms) on a (16, 128) band of 1 ms bins with unit noise.

    The model is averaged from a grid 16 times finer, as real bins integrate the signal.
    """
    count = len(components)
    clean = BurstModel(_GUIDANCE_FREQS, np.arange(128, dtype=float) * 1e-3, factor_time_upsample=16).evaluate(
        BurstParameters.from_dict(
            {
                "amplitude": [amplitude for amplitude, _arrival, _width in components],
                "arrival_time": [arrival * 1e-3 for _amplitude, arrival, _width in components],
                "burst_width": [width * 1e-3 for _amplitude, _arrival, width in components],
                "ref_freq": [1250.0] * count,
                "scattering_timescale": [tau_ms * 1e-3] * count,
            }
        )
    )
    return clean + np.random.default_rng(seed).normal(0.0, 1.0, size=clean.shape)


def _guidance_inputs(data: np.ndarray, event: tuple[int, int]) -> dict[str, Any]:
    return {
        "selected_band": data,
        "freqs_mhz": _GUIDANCE_FREQS,
        "time_axis_ms": np.arange(data.shape[1], dtype=float),
        "event_rel_start": event[0],
        "event_rel_end": event[1],
        "offpulse_bins": _GUIDANCE_OFFPULSE,
        "tsamp_ms": 1.0,
        "peak_rel_bin": None,
        "width_guess_ms": None,
    }


_UNSCATTERED = {"free_parameters": ["amplitude", "arrival_time", "burst_width"]}
_NO_SCATTERING = {"scattering_timescale": [0.0]}


def test_the_fit_window_pads_the_event_window_by_its_length() -> None:
    data = _guidance_band([(1.0, 60.0, 3.0)], tau_ms=1.0)

    padded = model_fit.fit_model_selected_band(**_guidance_inputs(data, (50, 75)), config=ModelFitRequestConfig())
    exact = model_fit.fit_model_selected_band(
        **_guidance_inputs(data, (50, 75)), config=ModelFitRequestConfig(fit_window_padding_bins=0)
    )

    assert padded.status == "ok" and exact.status == "ok"
    assert padded.diagnostics.fit_window_ms == [25.0, 100.0]
    assert padded.diagnostics.time_axis_ms[0] == 25.0 and padded.diagnostics.time_axis_ms[-1] == 99.0
    assert padded.diagnostics.fit_statistics["num_time"] == 75
    assert exact.diagnostics.fit_window_ms == [50.0, 75.0]
    assert padded.diagnostics.time_resolution_ms == 1.0


def test_fit_window_padding_stays_inside_the_data_and_the_size_limit() -> None:
    window = model_fit._fit_window
    assert window(event_rel_start=2, event_rel_end=10, num_time=128, num_channels=16, padding_bins=None) == (0, 18)
    # A short event still gets eight bins of baseline on each side.
    assert window(event_rel_start=60, event_rel_end=62, num_time=128, num_channels=16, padding_bins=None) == (52, 70)
    assert window(event_rel_start=60, event_rel_end=62, num_time=128, num_channels=16, padding_bins=3) == (57, 65)
    # Padding shrinks to keep the window within the interactive limit.
    channels = model_fit.MAX_MODEL_FIT_CELLS // 30
    start, end = window(event_rel_start=100, event_rel_end=110, num_time=1000, num_channels=channels, padding_bins=None)
    assert (start, end) == (90, 120)
    assert (end - start) * channels <= model_fit.MAX_MODEL_FIT_CELLS


def test_a_burst_narrower_than_a_bin_is_fitted_on_a_finer_time_grid() -> None:
    data = _guidance_band([(1.3, 60.2, 0.3)], seed=0)
    inputs = _guidance_inputs(data, (55, 66))

    auto = model_fit.fit_model_selected_band(
        **inputs, config=ModelFitRequestConfig(**_UNSCATTERED, initial_parameters=_NO_SCATTERING)
    )
    plain = model_fit.fit_model_selected_band(
        **inputs,
        config=ModelFitRequestConfig(**_UNSCATTERED, initial_parameters=_NO_SCATTERING, factor_time_upsample=1),
    )

    assert auto.status == "ok" and plain.status == "ok"
    assert auto.diagnostics.time_upsample_basis == "auto"
    # Sub-samples no wider than a third of the 0.3 ms width.
    assert auto.diagnostics.factor_time_upsample >= 9
    assert plain.diagnostics.time_upsample_basis == "requested" and plain.diagnostics.factor_time_upsample == 1
    assert auto.width_ms_model is not None and auto.width_uncertainty_ms is not None
    assert plain.width_ms_model is not None and plain.width_uncertainty_ms is not None
    # Sampled at bin centres, the model needs a wider Gaussian to put flux in
    # the neighbouring bins; averaged over each bin, it recovers the width.
    assert abs(auto.width_ms_model - 0.3) < 2 * auto.width_uncertainty_ms
    assert plain.width_ms_model - 0.3 > 2 * plain.width_uncertainty_ms
    assert auto.diagnostics.resolution_flags == ["width_below_time_resolution"]
    detail = auto.diagnostics.uncertainty_details["width_ms_model"]
    assert "below_time_resolution" in detail.warning_flags
    assert "shorter than one time bin" in detail.tooltip


def test_a_resolved_burst_keeps_the_time_resolution_of_the_data() -> None:
    data = _guidance_band([(1.0, 60.0, 6.0)], tau_ms=1.0)

    result = model_fit.fit_model_selected_band(**_guidance_inputs(data, (45, 80)), config=ModelFitRequestConfig())

    assert result.status == "ok"
    assert result.diagnostics.time_upsample_basis == "auto"
    assert result.diagnostics.factor_time_upsample == 1
    assert result.diagnostics.resolution_flags == []


def test_the_comparison_finds_both_components_of_a_double_burst() -> None:
    data = _guidance_band([(1.0, 55.0, 2.0), (0.8, 68.0, 2.0)], tau_ms=0.5, seed=0)

    comparison = model_fit.compare_component_counts(**_guidance_inputs(data, (45, 80)), max_components=3)

    assert comparison.status == "ok"
    assert comparison.preferred_components == 2
    one, two, three = comparison.rows
    assert [row.status for row in comparison.rows] == ["ok", "ok", "ok"]
    assert two.delta_bic == 0.0
    assert one.delta_bic is not None and one.delta_bic > 10
    assert three.delta_bic is not None and three.delta_bic > 0
    # The second component is needed; a third is not.
    assert two.f_test_p_value is not None and two.f_test_p_value < 1e-6
    assert three.f_test_p_value is not None and three.f_test_p_value > 0.01
    arrivals_ms = np.asarray(two.bestfit_parameters["arrival_time"] or [], dtype=float) * 1e3
    sigmas_ms = np.asarray(two.bestfit_uncertainties["arrival_time"] or [], dtype=float) * 1e3
    order = np.argsort(arrivals_ms)
    assert np.all(np.abs(arrivals_ms[order] - [55.0, 68.0]) < 3 * sigmas_ms[order])
    assert one.num_parameters is not None and two.num_parameters == one.num_parameters + 3


def test_the_comparison_prefers_one_component_for_a_single_burst() -> None:
    data = _guidance_band([(1.0, 60.0, 2.0)], tau_ms=0.5, seed=0)

    comparison = model_fit.compare_component_counts(**_guidance_inputs(data, (50, 75)), max_components=3)

    assert comparison.status == "ok"
    assert comparison.preferred_components == 1
    assert all(row.delta_bic is not None and row.delta_bic > 0 for row in comparison.rows[1:])


def test_a_comparison_keeps_the_fits_before_one_that_fails() -> None:
    data = _guidance_band([(1.0, 60.0, 2.0)], tau_ms=0.5)
    real_fit = model_fit.fit_model_selected_band

    def fail_from_two_components(**kwargs: Any) -> ModelFitResult:
        if kwargs["config"].num_components < 2:
            return real_fit(**kwargs)
        return model_fit._failed_result(
            status="evaluation_limit_exceeded", message="The maximum number of function evaluations is exceeded."
        )

    with patch.object(model_fit, "fit_model_selected_band", side_effect=fail_from_two_components):
        comparison = model_fit.compare_component_counts(**_guidance_inputs(data, (50, 75)), max_components=3)

    assert comparison.status == "ok"
    assert comparison.preferred_components == 1
    assert [row.status for row in comparison.rows] == ["ok", "evaluation_limit_exceeded"]
    assert comparison.message is not None
    assert "2-component fit did not converge: it reached the evaluation limit" in comparison.message


def test_time_upsampling_and_padding_accept_auto_in_requests() -> None:
    for value in (None, "", "auto", 0):
        config = ModelFitRequestConfig.from_dict({"solver": {"factor_time_upsample": value}})
        assert config.factor_time_upsample is None, value
    config = ModelFitRequestConfig.from_dict({"solver": {"factor_time_upsample": "4", "fit_window_padding_bins": 6}})
    assert config.factor_time_upsample == 4
    assert config.fit_window_padding_bins == 6
    assert config.to_dict()["solver"]["fit_window_padding_bins"] == 6
    assert (
        ModelFitRequestConfig.from_dict({"solver": {"fit_window_padding_bins": "auto"}}).fit_window_padding_bins is None
    )


class ModelComparisonSessionTest(unittest.TestCase):
    def test_a_stored_comparison_survives_snapshots_and_its_fit_can_be_kept(self) -> None:
        session, _width_ms, _tau_ms = _synthetic_scattering_session()

        results = session.compare_model_components(max_components=2)

        comparison = results.diagnostics.model_comparison
        assert comparison is not None
        self.assertEqual(comparison.status, "ok")
        self.assertEqual([row.num_components for row in comparison.rows], [1, 2])
        self.assertEqual(comparison.preferred_components, 1)
        self.assertIn("model_comparison", session.software_provenance()["products"])
        self.assertEqual(
            MeasurementDiagnostics.from_dict(results.diagnostics.to_dict()).to_dict(), results.diagnostics.to_dict()
        )

        kept = session.fit_model(
            {
                "num_components": 1,
                "initial_parameters": comparison.rows[0].bestfit_parameters,
                "initial_parameter_source": "component_comparison",
            }
        )
        fit = kept.diagnostics.model_fit
        assert fit is not None
        self.assertEqual(fit.initial_parameter_source, "component_comparison")
        self.assertAlmostEqual(
            kept.width_ms_model or 0.0,
            (comparison.rows[0].bestfit_parameters["burst_width"] or [0.0])[0] * 1e3,
            places=4,
        )
        # Recomputing the measurements keeps the comparison, as it keeps the fit.
        session.compute_properties()
        assert session.results is not None
        self.assertIsNotNone(session.results.diagnostics.model_comparison)
