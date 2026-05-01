from __future__ import annotations

from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from flits.analysis.fitting import fitburst_adapter
from flits.analysis.fitting.fitburst_adapter import SpectrumModeler
from flits.analysis.fitting.fitburst_adapter import FitburstRequestConfig
from flits.analysis.fitting.fitburst_adapter import FitburstScatteringResult
from flits.models import FilterbankMetadata, ScatteringFitDiagnostics
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
    def __init__(
        self,
        freqs_mhz: np.ndarray,
        times_sec: np.ndarray,
        *,
        dm_incoherent: float,
        num_components: int,
        is_dedispersed: bool,
    ) -> None:
        self.freqs_mhz = np.asarray(freqs_mhz, dtype=float)
        self.times_sec = np.asarray(times_sec, dtype=float)
        self.parameters: dict[str, list[float] | None] = {}
        self.update_history: list[dict[str, list[float] | None]] = []

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
        self.fit_parameters = [
            parameter
            for parameter in ("amplitude", "arrival_time", "burst_width", "scattering_timescale")
            if parameter not in fixed_parameters
        ]

    def fit(self) -> None:
        type(self).fit_calls += 1
        call_index = type(self).fit_calls
        if type(self).fail_on_call == call_index:
            self.results = SimpleNamespace(success=False, message=f"failed iteration {call_index}")
            self.fit_statistics = {"chisq_initial": 10.0, "num_fit_parameters": len(self.fit_parameters)}
            return
        self.results = SimpleNamespace(success=True, message=f"ok iteration {call_index}")
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


def _run_fake_fitburst_fit(*, iterations: int, fitter_class: type[_FakeLSFitter] = _FakeLSFitter) -> FitburstScatteringResult:
    rng = np.random.default_rng(7)
    data = rng.normal(0.0, 1.0, size=(6, 48))
    data[:, 20:26] += 6.0

    _FakeLSFitter.instances = []
    _FakeLSFitter.fit_calls = 0
    fitter_class.instances = []
    fitter_class.fit_calls = 0
    with (
        patch.object(fitburst_adapter, "SpectrumModeler", _FakeSpectrumModeler),
        patch.object(fitburst_adapter, "LSFitter", fitter_class),
    ):
        return fitburst_adapter.fit_scattering_selected_band(
            selected_band=data,
            freqs_mhz=np.linspace(1450.0, 1250.0, data.shape[0]),
            time_axis_ms=np.arange(data.shape[1], dtype=float),
            event_rel_start=18,
            event_rel_end=30,
            offpulse_bins=np.r_[0:12, 36:48],
            tsamp_ms=1.0,
            peak_rel_bin=23,
            width_guess_ms=1.5,
            config=FitburstRequestConfig(iterations=iterations),
        )


@unittest.skipUnless(SpectrumModeler is not None, "fitburst is not installed")
class FitScatteringTest(unittest.TestCase):
    def test_fit_scattering_recovers_intrinsic_width_and_scattering_time(self) -> None:
        session, expected_width_ms, expected_tau_ms = _synthetic_scattering_session()

        results = session.fit_scattering()

        self.assertEqual(results.diagnostics.scattering_fit.status, "ok")
        self.assertIsNotNone(results.width_ms_model)
        self.assertIsNotNone(results.tau_sc_ms)
        self.assertAlmostEqual(results.width_ms_model or 0.0, expected_width_ms, delta=0.25)
        self.assertAlmostEqual(results.tau_sc_ms or 0.0, expected_tau_ms, delta=0.35)
        self.assertIsNotNone(results.uncertainties.width_ms_model)
        self.assertIsNotNone(results.uncertainties.tau_sc_ms)
        self.assertEqual(results.uncertainty_details["width_ms_model"].classification, "model_hessian")
        self.assertEqual(results.uncertainty_details["tau_sc_ms"].classification, "model_hessian")
        self.assertEqual(results.diagnostics.scattering_fit.uncertainty_details["width_ms_model"].classification, "model_hessian")
        self.assertEqual(results.diagnostics.scattering_fit.fitter, "fitburst")
        self.assertGreater(len(results.diagnostics.scattering_fit.time_axis_ms), 0)
        self.assertGreater(len(results.diagnostics.scattering_fit.freq_axis_mhz), 0)
        self.assertEqual(
            len(results.diagnostics.scattering_fit.time_axis_ms),
            len(results.diagnostics.scattering_fit.model_profile_sn),
        )
        self.assertEqual(
            results.diagnostics.scattering_fit.data_dynamic_spectrum_sn.shape,
            results.diagnostics.scattering_fit.model_dynamic_spectrum_sn.shape,
        )
        self.assertEqual(
            results.diagnostics.scattering_fit.data_dynamic_spectrum_sn.shape,
            results.diagnostics.scattering_fit.residual_dynamic_spectrum_sn.shape,
        )
        self.assertEqual(
            results.diagnostics.scattering_fit.data_dynamic_spectrum_sn.shape,
            (
                len(results.diagnostics.scattering_fit.freq_axis_mhz),
                len(results.diagnostics.scattering_fit.time_axis_ms),
            ),
        )
        self.assertFalse(results.diagnostics.scattering_fit.weighted_fit)
        self.assertIsNone(results.diagnostics.scattering_fit.weight_range)
        self.assertEqual(results.diagnostics.scattering_fit.weight_range_basis, "unweighted")
        self.assertEqual(results.diagnostics.scattering_fit.fit_iterations_requested, 1)
        self.assertEqual(results.diagnostics.scattering_fit.fit_iterations_completed, 1)

    def test_weighted_fit_records_offpulse_weighting_diagnostics(self) -> None:
        unweighted_session, _, _ = _synthetic_scattering_session()
        weighted_session, _, _ = _synthetic_scattering_session()

        unweighted = unweighted_session.fit_scattering({"weighted_fit": False})
        weighted = weighted_session.fit_scattering({"weighted_fit": True})

        self.assertEqual(unweighted.diagnostics.scattering_fit.status, "ok")
        self.assertEqual(weighted.diagnostics.scattering_fit.status, "ok")
        self.assertFalse(unweighted.diagnostics.scattering_fit.weighted_fit)
        self.assertTrue(weighted.diagnostics.scattering_fit.weighted_fit)
        self.assertEqual(weighted.diagnostics.scattering_fit.weight_range_basis, "offpulse_bins")
        self.assertIsNotNone(weighted.diagnostics.scattering_fit.weight_range)
        self.assertIsNotNone(unweighted.width_ms_model)
        self.assertIsNotNone(weighted.width_ms_model)
        self.assertAlmostEqual(weighted.width_ms_model or 0.0, unweighted.width_ms_model or 0.0, delta=0.5)


class FitburstRequestConfigTest(unittest.TestCase):
    def test_advanced_fit_options_round_trip_through_request_config(self) -> None:
        config = FitburstRequestConfig.from_dict(
            {
                "num_components": 2,
                "fixed_parameters": ["dm", "dm_index"],
                "weighted_fit": True,
                "weight_range": [3.2, 19.8],
                "iterations": "3",
            }
        )

        self.assertEqual(config.num_components, 2)
        self.assertTrue(config.weighted_fit)
        self.assertEqual(config.weight_range, [3, 20])
        self.assertEqual(config.iterations, 3)
        self.assertEqual(config.to_dict()["weighted_fit"], True)
        self.assertEqual(config.to_dict()["weight_range"], [3, 20])
        self.assertEqual(config.to_dict()["iterations"], 3)

    def test_invalid_iteration_count_falls_back_to_one(self) -> None:
        self.assertEqual(FitburstRequestConfig.from_dict({"iterations": "bad"}).iterations, 1)
        self.assertEqual(FitburstRequestConfig.from_dict({"iterations": 0}).iterations, 1)

    def test_legacy_bounds_payload_is_ignored_and_not_serialized(self) -> None:
        config = FitburstRequestConfig.from_dict(
            {
                "bounds": {
                    "burst_width": [[0.0, 0.01]],
                    "scattering_timescale": [[0.0, 0.1]],
                },
            }
        )

        self.assertFalse(hasattr(config, "bounds"))
        self.assertNotIn("bounds", config.to_dict())


class FitburstIterationAdapterTest(unittest.TestCase):
    def test_iterations_update_model_with_previous_bestfit_parameters(self) -> None:
        result = _run_fake_fitburst_fit(iterations=2)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.diagnostics.fit_iterations_requested, 2)
        self.assertEqual(result.diagnostics.fit_iterations_completed, 2)
        self.assertEqual(len(_FakeLSFitter.instances), 2)
        self.assertEqual(_FakeLSFitter.instances[1].model_parameters_at_init["burst_width"], [0.0011])
        self.assertAlmostEqual(result.diagnostics.bestfit_parameters["burst_width"][0], 0.0012)
        self.assertAlmostEqual(result.width_ms_model or 0.0, 1.2)

    def test_failed_intermediate_iteration_returns_completed_bestfit_diagnostics(self) -> None:
        class FailingSecondFit(_FakeLSFitter):
            fail_on_call = 2

        result = _run_fake_fitburst_fit(iterations=3, fitter_class=FailingSecondFit)

        self.assertEqual(result.status, "fit_failed")
        self.assertEqual(result.message, "failed iteration 2")
        self.assertEqual(result.diagnostics.fit_iterations_requested, 3)
        self.assertEqual(result.diagnostics.fit_iterations_completed, 1)
        self.assertEqual(result.diagnostics.bestfit_parameters["burst_width"], [0.0011])
        self.assertIsNone(result.width_ms_model)

class FitScatteringDispatchTest(unittest.TestCase):
    def test_fitburst_guess_uses_component_regions(self) -> None:
        session = _synthetic_scattering_dispatch_session()
        session.add_region_ms(session.bin_to_ms(245), session.bin_to_ms(260))
        session.add_region_ms(session.bin_to_ms(275), session.bin_to_ms(310))

        guess = session.get_view()["fitburst_guess"]

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

    def test_fitburst_guess_uses_manual_peaks_without_regions(self) -> None:
        session = _synthetic_scattering_dispatch_session()
        first_peak = session.bin_to_ms(250)
        second_peak = session.bin_to_ms(290)
        session.add_peak_ms(first_peak)
        session.add_peak_ms(second_peak)

        guess = session.get_view()["fitburst_guess"]

        self.assertEqual(guess["status"], "ok")
        self.assertEqual(guess["source"], "manual_peaks")
        self.assertEqual(guess["component_count"], 2)
        arrivals = [row["arrival_time_ms"] for row in guess["component_guesses"]]
        self.assertEqual(arrivals, [first_peak, second_peak])
        windows = [row["component_window_ms"] for row in guess["component_guesses"]]
        self.assertLess(windows[0][1], windows[1][1])
        self.assertAlmostEqual(windows[0][1], windows[1][0], places=6)

    def test_fitburst_guess_falls_back_to_single_automatic_component(self) -> None:
        session = _synthetic_scattering_dispatch_session()

        guess = session.get_view()["fitburst_guess"]

        self.assertEqual(guess["status"], "ok")
        self.assertEqual(guess["source"], "automatic")
        self.assertEqual(guess["component_count"], 1)
        self.assertEqual(len(guess["component_guesses"]), 1)
        self.assertEqual(len(guess["initial_parameters"]["arrival_time"]), 1)

    @patch("flits.session.fit_scattering_selected_band")
    def test_fit_scattering_uses_reduced_grid(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        session.set_time_factor(4)
        session.set_freq_factor(2)

        mock_fit.return_value = FitburstScatteringResult(
            status="ok",
            message=None,
            width_ms_model=1.0,
            width_uncertainty_ms=0.1,
            tau_sc_ms=2.0,
            tau_uncertainty_ms=0.2,
            diagnostics=ScatteringFitDiagnostics(
                status="ok",
                message=None,
                fitter="fitburst",
                component_count=1,
            ),
        )

        results = session.fit_scattering()

        kwargs = mock_fit.call_args.kwargs
        self.assertEqual(kwargs["selected_band"].shape[0], session.total_channels // 2)
        self.assertEqual(kwargs["tsamp_ms"], session.tsamp_ms * 4)
        self.assertEqual(kwargs["freqs_mhz"].shape[0], session.total_channels // 2)
        self.assertEqual(results.width_ms_model, 1.0)
        self.assertEqual(results.tau_sc_ms, 2.0)

    @patch("flits.session.fit_scattering_selected_band")
    def test_fit_scattering_converts_component_guesses_to_initial_parameters(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        first_arrival_ms = session.bin_to_ms(250)
        second_arrival_ms = session.bin_to_ms(290)

        mock_fit.return_value = FitburstScatteringResult(
            status="ok",
            message=None,
            width_ms_model=1.0,
            width_uncertainty_ms=0.1,
            tau_sc_ms=2.0,
            tau_uncertainty_ms=0.2,
            diagnostics=ScatteringFitDiagnostics(
                status="ok",
                message=None,
                fitter="fitburst",
                component_count=2,
                initial_parameters={},
            ),
        )

        session.fit_scattering(
            {
                "num_components": 2,
                "fixed_parameters": ["dm"],
                "component_guesses": [
                    {
                        "arrival_time_ms": first_arrival_ms,
                        "width_ms": 1.2,
                        "tau_ms": 0.4,
                        "log_amplitude": 0.7,
                    },
                    {
                        "arrival_time_ms": second_arrival_ms,
                        "width_ms": 1.8,
                        "tau_ms": 0.5,
                        "log_amplitude": 0.6,
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

    @patch("flits.session.fit_scattering_selected_band")
    def test_fit_scattering_passes_advanced_fit_config(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()

        mock_fit.return_value = FitburstScatteringResult(
            status="ok",
            message=None,
            width_ms_model=1.0,
            width_uncertainty_ms=0.1,
            tau_sc_ms=2.0,
            tau_uncertainty_ms=0.2,
            diagnostics=ScatteringFitDiagnostics(
                status="ok",
                message=None,
                fitter="fitburst",
                component_count=1,
            ),
        )

        session.fit_scattering({"weighted_fit": True, "weight_range": [2, 20], "iterations": 3})

        config = mock_fit.call_args.kwargs["config"]
        self.assertTrue(config.weighted_fit)
        self.assertEqual(config.weight_range, [2, 20])
        self.assertEqual(config.iterations, 3)

    @patch("flits.session.fit_scattering_selected_band")
    def test_failed_refit_preserves_previous_successful_fit_values(self, mock_fit: object) -> None:
        session = _synthetic_scattering_dispatch_session()
        mock_fit.side_effect = [
            FitburstScatteringResult(
                status="ok",
                message=None,
                width_ms_model=1.0,
                width_uncertainty_ms=None,
                tau_sc_ms=2.0,
                tau_uncertainty_ms=None,
                diagnostics=ScatteringFitDiagnostics(
                    status="ok",
                    message=None,
                    fitter="fitburst",
                    component_count=1,
                ),
            ),
            FitburstScatteringResult(
                status="fit_failed",
                message="failed iteration 2",
                width_ms_model=None,
                width_uncertainty_ms=None,
                tau_sc_ms=None,
                tau_uncertainty_ms=None,
                diagnostics=ScatteringFitDiagnostics(
                    status="fit_failed",
                    message="failed iteration 2",
                    fitter="fitburst",
                    component_count=1,
                    fit_iterations_requested=3,
                    fit_iterations_completed=1,
                ),
            ),
        ]

        session.fit_scattering()
        results = session.fit_scattering({"iterations": 3})

        self.assertEqual(results.width_ms_model, 1.0)
        self.assertEqual(results.tau_sc_ms, 2.0)
        self.assertEqual(results.diagnostics.scattering_fit.status, "fit_failed")
        self.assertEqual(results.diagnostics.scattering_fit.fit_iterations_completed, 1)


if __name__ == "__main__":
    unittest.main()
