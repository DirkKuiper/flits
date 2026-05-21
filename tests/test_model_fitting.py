from __future__ import annotations

import json
import sys
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from flits.analysis.fitting import fitburst_adapter
from flits.analysis.fitting.fitburst_adapter import SpectrumModeler
from flits.analysis.fitting.fitburst_adapter import ModelFitRequestConfig
from flits.analysis.fitting.fitburst_adapter import ModelFitResult
from flits.models import FilterbankMetadata, ModelFitDiagnostics
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

    model = SpectrumModeler(
        freqs,
        times_sec,
        dm_incoherent=0.0,
        num_components=1,
        is_dedispersed=True,
    )
    model.update_parameters(
        {
            "amplitude": [1.05],
            "arrival_time": [times_sec[260]],
            "burst_width": [width_sec],
            "dm": [0.0],
            "dm_index": [-2.0],
            "ref_freq": [float(np.min(freqs))],
            "scattering_timescale": [tau_sec],
            "scattering_index": [-4.0],
            "spectral_index": [0.0],
            "spectral_running": [0.0],
        }
    )
    data = model.compute_model() + rng.normal(0.0, 0.35, size=(freqs.size, num_time_bins))

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


class _FakeSpectrumModeler:
    instances: list["_FakeSpectrumModeler"] = []

    def __init__(
        self,
        freqs_mhz: np.ndarray,
        times_sec: np.ndarray,
        *,
        dm_incoherent: float,
        factor_freq_upsample: int = 1,
        factor_time_upsample: int = 1,
        num_components: int = 1,
        is_dedispersed: bool = False,
        is_folded: bool = False,
        scintillation: bool = False,
    ) -> None:
        self.freqs_mhz = np.asarray(freqs_mhz, dtype=float)
        self.times_sec = np.asarray(times_sec, dtype=float)
        self.factor_freq_upsample = int(factor_freq_upsample)
        self.factor_time_upsample = int(factor_time_upsample)
        self.is_folded = bool(is_folded)
        self.scintillation = bool(scintillation)
        self.parameters: dict[str, list[float] | None] = {}
        self.update_history: list[dict[str, list[float] | None]] = []
        type(self).instances.append(self)

    def update_parameters(self, parameters: dict[str, list[float] | None]) -> None:
        self.parameters = {
            key: None if values is None else [float(value) for value in np.asarray(values, dtype=float).ravel()]
            for key, values in parameters.items()
        }
        self.update_history.append(self.parameters)

    def compute_model(self, data: np.ndarray | None = None) -> np.ndarray:
        if data is not None:
            return np.zeros_like(data, dtype=float)
        return np.zeros((self.freqs_mhz.size, self.times_sec.size), dtype=float)


class _FakeLSFitter:
    instances: list["_FakeLSFitter"] = []
    fit_calls = 0
    fail_on_call: int | None = None

    def __init__(
        self,
        data: np.ndarray,
        model: _FakeSpectrumModeler,
        *,
        good_freq: np.ndarray,
        weighted_fit: bool,
        weight_range: list[int] | None,
    ) -> None:
        self.data = np.asarray(data, dtype=float)
        self.model = model
        self.good_freq = np.asarray(good_freq, dtype=bool)
        self.weighted_fit = bool(weighted_fit)
        self.weight_range = weight_range
        self.model_parameters_at_init = {
            key: None if values is None else list(values)
            for key, values in model.parameters.items()
        }
        self.fit_parameters: list[str] = []
        self.fit_statistics: dict[str, object] = {}
        self.results: SimpleNamespace | None = None
        type(self).instances.append(self)

    def fix_parameter(self, fixed_parameters: list[str]) -> None:
        fit_parameters = [
            parameter
            for parameter in (
                "amplitude",
                "arrival_time",
                "burst_width",
                "scattering_timescale",
                "dm",
                "dm_index",
                "scattering_index",
                "spectral_index",
                "spectral_running",
            )
        ]
        if self.model.scintillation:
            fit_parameters = [
                parameter
                for parameter in fit_parameters
                if parameter not in {"amplitude", "spectral_index", "spectral_running"}
            ]
        self.fit_parameters = [parameter for parameter in fit_parameters if parameter not in fixed_parameters]

    def fit(self, *, exact_jacobian: bool = True) -> None:
        self.exact_jacobian = bool(exact_jacobian)
        type(self).fit_calls += 1
        call_index = type(self).fit_calls
        if type(self).fail_on_call == call_index:
            print("fitburst stdout before failure " + ("x" * 5000))
            print("fitburst stderr before failure \x00\x01" + ("y" * 5000), file=sys.stderr)
            self.results = SimpleNamespace(
                success=False,
                message=f"failed iteration {call_index}",
                status=2,
                nfev=call_index * 10,
            )
            self.fit_statistics = {"chisq_initial": 10.0, "num_fit_parameters": len(self.fit_parameters)}
            return
        self.results = SimpleNamespace(
            success=True,
            message=f"ok iteration {call_index}",
            status=1,
            nfev=call_index * 10,
        )
        self._compute_fit_statistics(self.data, self.results)

    def get_fit_parameters_list(self) -> list[float]:
        return [0.0] * len(self.fit_parameters)

    def compute_residuals(self, parameter_list: list[float]) -> np.ndarray:
        return np.zeros(self.data.size, dtype=float)

    def compute_jacobian(self, parameter_list: list[float]) -> np.ndarray:
        return np.zeros((self.data.size, len(parameter_list)), dtype=float)

    def _compute_fit_statistics(self, data: np.ndarray, results: SimpleNamespace) -> None:
        call_index = max(1, type(self).fit_calls)
        self.fit_statistics = {
            "chisq_initial": 10.0,
            "chisq_final": float(10.0 / call_index),
            "num_fit_parameters": len(self.fit_parameters),
            "bestfit_parameters": {
                "amplitude": [float(call_index)],
                "arrival_time": [0.018],
                "burst_width": [0.001 + 0.0001 * call_index],
                "scattering_timescale": [0.002 + 0.0001 * call_index],
            },
            "bestfit_uncertainties": {
                "burst_width": [0.00001],
                "scattering_timescale": [0.00002],
            },
        }


def _run_fake_fitburst_fit(
    *,
    iterations: int,
    fitter_class: type[_FakeLSFitter] = _FakeLSFitter,
    config: ModelFitRequestConfig | None = None,
) -> ModelFitResult:
    rng = np.random.default_rng(7)
    data = rng.normal(0.0, 1.0, size=(6, 48))
    data[:, 20:26] += 6.0

    _FakeLSFitter.instances = []
    _FakeLSFitter.fit_calls = 0
    _FakeSpectrumModeler.instances = []
    fitter_class.instances = []
    fitter_class.fit_calls = 0
    with (
        patch.object(fitburst_adapter, "SpectrumModeler", _FakeSpectrumModeler),
        patch.object(fitburst_adapter, "LSFitter", fitter_class),
    ):
        return fitburst_adapter.fit_model_selected_band(
            selected_band=data,
            freqs_mhz=np.linspace(1450.0, 1250.0, data.shape[0]),
            time_axis_ms=np.arange(data.shape[1], dtype=float),
            event_rel_start=18,
            event_rel_end=30,
            offpulse_bins=np.r_[0:12, 36:48],
            tsamp_ms=1.0,
            peak_rel_bin=23,
            width_guess_ms=1.5,
            config=config if config is not None else ModelFitRequestConfig(iterations=iterations),
        )


@unittest.skipUnless(SpectrumModeler is not None, "fitburst is not installed")
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
        self.assertEqual(results.uncertainty_details["width_ms_model"].classification, "model_hessian")
        self.assertEqual(results.uncertainty_details["tau_sc_ms"].classification, "model_hessian")
        self.assertEqual(results.diagnostics.model_fit.uncertainty_details["width_ms_model"].classification, "model_hessian")
        self.assertEqual(results.diagnostics.model_fit.fitter, "fitburst")
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
        self.assertEqual(config.fixed_parameters, ["dm", "dm_index", "scattering_index", "spectral_index", "spectral_running"])
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
        self.assertEqual(config.fixed_parameters, ["burst_width", "scattering_timescale", "dm_index", "scattering_index"])
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

        self.assertEqual(config.factor_time_upsample, 1)
        self.assertEqual(config.factor_freq_upsample, 1)
        self.assertIsNone(config.ref_freq_mhz)
        self.assertEqual(config.weighting_mode, "none")
        self.assertIsNone(config.max_function_evaluations)

    def test_max_function_evaluations_clamps_to_interactive_cap(self) -> None:
        config = ModelFitRequestConfig.from_dict(
            {"solver": {"max_function_evaluations": fitburst_adapter.MAX_MODEL_FIT_FUNCTION_EVALUATIONS + 1}}
        )

        self.assertEqual(config.max_function_evaluations, fitburst_adapter.MAX_MODEL_FIT_FUNCTION_EVALUATIONS)

    def test_non_positive_max_function_evaluations_uses_auto_default(self) -> None:
        self.assertIsNone(ModelFitRequestConfig.from_dict({"solver": {"max_function_evaluations": 0}}).max_function_evaluations)
        self.assertIsNone(ModelFitRequestConfig.from_dict({"solver": {"max_function_evaluations": -4}}).max_function_evaluations)

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
    def test_iterations_update_model_with_previous_bestfit_parameters(self) -> None:
        result = _run_fake_fitburst_fit(iterations=2)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.diagnostics.fit_iterations_requested, 2)
        self.assertEqual(result.diagnostics.fit_iterations_completed, 2)
        self.assertEqual(len(_FakeLSFitter.instances), 2)
        self.assertEqual(_FakeLSFitter.instances[1].model_parameters_at_init["burst_width"], [0.0011])
        self.assertAlmostEqual(result.diagnostics.bestfit_parameters["burst_width"][0], 0.0012)
        self.assertAlmostEqual(result.width_ms_model or 0.0, 1.2)

    def test_model_controls_reach_spectrum_modeler_and_diagnostics(self) -> None:
        result = _run_fake_fitburst_fit(
            iterations=1,
            config=ModelFitRequestConfig(
                iterations=1,
                factor_time_upsample=4,
                factor_freq_upsample=3,
                ref_freq_mhz=1400.0,
                is_folded=True,
                exact_jacobian=False,
            ),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(_FakeSpectrumModeler.instances), 1)
        self.assertEqual(_FakeSpectrumModeler.instances[0].factor_time_upsample, 4)
        self.assertEqual(_FakeSpectrumModeler.instances[0].factor_freq_upsample, 3)
        self.assertTrue(_FakeSpectrumModeler.instances[0].is_folded)
        self.assertFalse(_FakeLSFitter.instances[0].exact_jacobian)
        self.assertEqual(result.diagnostics.factor_time_upsample, 4)
        self.assertEqual(result.diagnostics.factor_freq_upsample, 3)
        self.assertEqual(result.diagnostics.ref_freq_mhz, 1400.0)
        self.assertTrue(result.diagnostics.is_folded)
        self.assertFalse(result.diagnostics.exact_jacobian)
        self.assertEqual(result.diagnostics.bestfit_parameters["ref_freq"], [1400.0])
        self.assertEqual(_FakeLSFitter.fit_calls, 1)
        self.assertEqual(result.diagnostics.max_function_evaluations, 400)
        self.assertEqual(result.diagnostics.function_evaluations, 10)
        self.assertEqual(result.diagnostics.solver_status, 1)
        self.assertEqual(result.diagnostics.solver_message, "ok iteration 1")

    def test_custom_max_function_evaluations_uses_scipy_path(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_least_squares(fun: object, x0: list[float], *, jac: object, max_nfev: int) -> SimpleNamespace:
            calls.append({"x0": list(x0), "jac": jac, "max_nfev": max_nfev})
            return SimpleNamespace(success=True, message="custom budget converged", status=2, nfev=37)

        with patch.object(fitburst_adapter, "least_squares", fake_least_squares):
            result = _run_fake_fitburst_fit(
                iterations=1,
                config=ModelFitRequestConfig(max_function_evaluations=1234),
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(_FakeLSFitter.fit_calls, 0)
        self.assertEqual(calls[0]["max_nfev"], 1234)
        self.assertEqual(len(calls[0]["x0"]), 4)
        self.assertEqual(result.diagnostics.max_function_evaluations, 1234)
        self.assertEqual(result.diagnostics.function_evaluations, 37)
        self.assertEqual(result.diagnostics.solver_status, 2)
        self.assertEqual(result.diagnostics.solver_message, "custom budget converged")
        self.assertEqual(result.diagnostics.fit_statistics["max_function_evaluations"], 1234)
        self.assertEqual(result.diagnostics.fit_statistics["function_evaluations"], 37)
        round_tripped = ModelFitDiagnostics.from_dict(result.diagnostics.to_dict())
        self.assertEqual(round_tripped.max_function_evaluations, 1234)
        self.assertEqual(round_tripped.function_evaluations, 37)
        self.assertEqual(round_tripped.solver_status, 2)
        self.assertEqual(round_tripped.solver_message, "custom budget converged")

    def test_evaluation_limit_exceeded_gets_specific_failure_status(self) -> None:
        def fake_least_squares(fun: object, x0: list[float], *, jac: object, max_nfev: int) -> SimpleNamespace:
            return SimpleNamespace(
                success=False,
                message="The maximum number of function evaluations is exceeded.",
                status=0,
                nfev=max_nfev,
            )

        with patch.object(fitburst_adapter, "least_squares", fake_least_squares):
            result = _run_fake_fitburst_fit(
                iterations=1,
                config=ModelFitRequestConfig(max_function_evaluations=5),
            )

        self.assertEqual(result.status, "evaluation_limit_exceeded")
        self.assertEqual(result.message, "The maximum number of function evaluations is exceeded.")
        self.assertEqual(result.diagnostics.max_function_evaluations, 5)
        self.assertEqual(result.diagnostics.function_evaluations, 5)
        self.assertEqual(result.diagnostics.solver_status, 0)
        self.assertEqual(result.diagnostics.solver_message, "The maximum number of function evaluations is exceeded.")

    def test_large_fit_window_fails_before_fitburst_optimizer(self) -> None:
        channels = fitburst_adapter.MIN_FIT_CHANNELS
        time_bins = fitburst_adapter.MAX_MODEL_FIT_CELLS // channels + fitburst_adapter.MIN_FIT_TIME_BINS
        data = np.zeros((channels, time_bins), dtype=float)
        _FakeLSFitter.instances = []
        _FakeLSFitter.fit_calls = 0
        _FakeSpectrumModeler.instances = []

        with (
            patch.object(fitburst_adapter, "SpectrumModeler", _FakeSpectrumModeler),
            patch.object(fitburst_adapter, "LSFitter", _FakeLSFitter),
        ):
            result = fitburst_adapter.fit_model_selected_band(
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
        self.assertEqual(_FakeLSFitter.instances, [])
        self.assertEqual(_FakeSpectrumModeler.instances, [])

    def test_successful_fit_downsamples_diagnostic_dynamic_spectra(self) -> None:
        with (
            patch.object(fitburst_adapter, "MAX_MODEL_FIT_DIAGNOSTIC_CELLS", 60),
            patch.object(fitburst_adapter, "MAX_MODEL_FIT_DIAGNOSTIC_FREQ_BINS", 4),
            patch.object(fitburst_adapter, "MAX_MODEL_FIT_DIAGNOSTIC_TIME_BINS", 20),
        ):
            result = _run_fake_fitburst_fit(iterations=1)

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

    def test_scintillation_reaches_modeler_and_excludes_inactive_parameters(self) -> None:
        result = _run_fake_fitburst_fit(
            iterations=1,
            config=ModelFitRequestConfig.from_dict(
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
            ),
        )

        self.assertEqual(result.status, "ok")
        self.assertTrue(_FakeSpectrumModeler.instances[0].scintillation)
        self.assertTrue(result.diagnostics.scintillation)
        self.assertEqual(result.diagnostics.free_parameters, ["arrival_time", "burst_width", "scattering_timescale"])
        self.assertEqual(result.diagnostics.fixed_parameters, ["dm", "dm_index", "scattering_index"])
        self.assertNotIn("amplitude", result.diagnostics.fit_parameters)
        self.assertNotIn("spectral_index", result.diagnostics.fit_parameters)
        self.assertNotIn("spectral_running", result.diagnostics.fit_parameters)
        self.assertIn("arrival_time", result.diagnostics.fit_parameters)

    def test_weighting_modes_reach_adapter_and_diagnostics(self) -> None:
        auto = _run_fake_fitburst_fit(
            iterations=1,
            config=ModelFitRequestConfig.from_dict({"solver": {"weighting_mode": "auto"}}),
        )
        self.assertEqual(auto.diagnostics.weighting_mode, "auto")
        self.assertEqual(auto.diagnostics.weight_range_basis, "offpulse_bins")
        self.assertFalse(_FakeLSFitter.instances[0].weighted_fit)
        self.assertTrue(hasattr(_FakeLSFitter.instances[0], "weights"))

        fit_window = _run_fake_fitburst_fit(
            iterations=1,
            config=ModelFitRequestConfig.from_dict({"solver": {"weighting_mode": "fit_window"}}),
        )
        self.assertEqual(fit_window.diagnostics.weighting_mode, "fit_window")
        self.assertEqual(fit_window.diagnostics.weight_range_basis, "fit_window")
        self.assertTrue(_FakeLSFitter.instances[0].weighted_fit)
        self.assertIsNone(_FakeLSFitter.instances[0].weight_range)

        manual = _run_fake_fitburst_fit(
            iterations=1,
            config=ModelFitRequestConfig.from_dict(
                {"solver": {"weighting_mode": "manual_range", "weight_range": [1, 5]}}
            ),
        )
        self.assertEqual(manual.diagnostics.weighting_mode, "manual_range")
        self.assertEqual(manual.diagnostics.weight_range_basis, "manual_range")
        self.assertEqual(_FakeLSFitter.instances[0].weight_range, [1, 5])

    def test_failed_intermediate_iteration_returns_completed_bestfit_diagnostics(self) -> None:
        class FailingSecondFit(_FakeLSFitter):
            fail_on_call = 2

        result = _run_fake_fitburst_fit(iterations=3, fitter_class=FailingSecondFit)

        self.assertEqual(result.status, "fit_failed")
        self.assertEqual(result.message, "failed iteration 2")
        self.assertEqual(result.diagnostics.fit_iterations_requested, 3)
        self.assertEqual(result.diagnostics.fit_iterations_completed, 1)
        self.assertEqual(result.diagnostics.bestfit_parameters["burst_width"], [0.0011])
        self.assertLessEqual(len(result.diagnostics.failure_stdout or ""), fitburst_adapter.MAX_FITBURST_LOG_CHARS)
        self.assertLessEqual(len(result.diagnostics.failure_stderr or ""), fitburst_adapter.MAX_FITBURST_LOG_CHARS)
        self.assertNotIn("\x00", result.diagnostics.failure_stderr or "")
        self.assertIn("fitburst diagnostic output truncated", result.diagnostics.failure_stdout or "")
        json.dumps(result.diagnostics.to_dict())
        round_tripped = ModelFitDiagnostics.from_dict(result.diagnostics.to_dict())
        self.assertEqual(round_tripped.failure_stdout, result.diagnostics.failure_stdout)
        self.assertIsNone(result.width_ms_model)

    def test_fitburst_exception_records_sanitized_failure_diagnostics(self) -> None:
        class RaisingFit(_FakeLSFitter):
            def fit(self, *, exact_jacobian: bool = True) -> None:
                print("stdout before exception " + ("a" * 5000))
                print("stderr before exception \x00" + ("b" * 5000), file=sys.stderr)
                raise RuntimeError("optimizer exploded")

        result = _run_fake_fitburst_fit(iterations=2, fitter_class=RaisingFit)

        self.assertEqual(result.status, "fit_failed")
        self.assertEqual(result.message, "fitburst raised an exception during fitting.")
        self.assertIn("RuntimeError: optimizer exploded", result.diagnostics.failure_exception or "")
        self.assertLessEqual(len(result.diagnostics.failure_stdout or ""), fitburst_adapter.MAX_FITBURST_LOG_CHARS)
        self.assertLessEqual(len(result.diagnostics.failure_stderr or ""), fitburst_adapter.MAX_FITBURST_LOG_CHARS)
        self.assertLessEqual(len(result.diagnostics.failure_exception or ""), fitburst_adapter.MAX_FITBURST_LOG_CHARS)
        self.assertNotIn("\x00", result.diagnostics.failure_stderr or "")
        json.dumps(result.diagnostics.to_dict())

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
                fitter="fitburst",
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
                fitter="fitburst",
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
                fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
                    fitter="fitburst",
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
