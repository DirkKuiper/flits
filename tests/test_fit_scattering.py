from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

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
    def test_weighted_fit_round_trips_through_request_config(self) -> None:
        config = FitburstRequestConfig.from_dict(
            {
                "num_components": 2,
                "fixed_parameters": ["dm", "dm_index"],
                "weighted_fit": True,
                "weight_range": [3.2, 19.8],
            }
        )

        self.assertEqual(config.num_components, 2)
        self.assertTrue(config.weighted_fit)
        self.assertEqual(config.weight_range, [3, 20])
        self.assertEqual(config.to_dict()["weighted_fit"], True)
        self.assertEqual(config.to_dict()["weight_range"], [3, 20])

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
    def test_fit_scattering_passes_weighted_fit_config(self, mock_fit: object) -> None:
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

        session.fit_scattering({"weighted_fit": True, "weight_range": [2, 20]})

        config = mock_fit.call_args.kwargs["config"]
        self.assertTrue(config.weighted_fit)
        self.assertEqual(config.weight_range, [2, 20])


if __name__ == "__main__":
    unittest.main()
