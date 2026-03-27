from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from flits.analysis.morphology import compute_width_analysis as compute_width_analysis_impl
from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


def _synthetic_width_session(
    *,
    path: Path,
    applied_dm: float = 0.0,
    peak_bin: int = 120,
    temporal_sigma_bins: float = 3.0,
) -> BurstSession:
    rng = np.random.default_rng(4)
    freqs = np.linspace(1100.0, 1000.0, 16)
    tsamp = 1e-3
    num_time_bins = 256
    time = np.arange(num_time_bins, dtype=float)
    chans = np.arange(freqs.size, dtype=float)
    temporal = np.exp(-0.5 * ((time - peak_bin) / temporal_sigma_bins) ** 2)
    spectral = np.exp(-0.5 * ((chans - ((freqs.size - 1) / 2.0)) / 3.5) ** 2)
    signal = 8.0 * spectral[:, None] * temporal[None, :]
    noise = rng.normal(0.0, 0.22, size=signal.shape)
    data = signal + noise

    metadata = FilterbankMetadata(
        source_path=path,
        source_name=path.stem,
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
    config = ObservationConfig.from_preset(
        dm=applied_dm,
        preset_key="generic",
        sefd_jy=10.0,
        distance_mpc=150.0,
        redshift=0.05,
    )
    session = BurstSession(
        config=config,
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=num_time_bins,
        event_start=peak_bin - 10,
        event_end=peak_bin + 10,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )
    session.add_offpulse_ms(0.0, 70.0)
    session.add_offpulse_ms(185.0, 240.0)
    return session


class WidthAnalysisTest(unittest.TestCase):
    def test_compute_widths_reports_all_methods_and_preserves_acceptance(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = _synthetic_width_session(path=Path(tmpdir) / "widths.fil")

            width_analysis = session.compute_widths()

            methods = {result.method for result in width_analysis.results}
            self.assertEqual(
                methods,
                {"boxcar_equivalent", "gaussian_sigma", "gaussian_fwhm", "fluence_percentile"},
            )
            self.assertIsNotNone(width_analysis.accepted_width)
            self.assertEqual(width_analysis.accepted_width.method, "boxcar_equivalent")

            session.accept_width_result("gaussian_fwhm")
            recomputed = session.compute_widths()

            self.assertIsNotNone(recomputed.accepted_width)
            self.assertEqual(recomputed.accepted_width.method, "gaussian_fwhm")

    def test_gaussian_sigma_and_fwhm_stay_consistent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = _synthetic_width_session(path=Path(tmpdir) / "gaussian_widths.fil")

            width_analysis = session.compute_widths()

            sigma = next(result for result in width_analysis.results if result.method == "gaussian_sigma")
            fwhm = next(result for result in width_analysis.results if result.method == "gaussian_fwhm")
            self.assertIsNotNone(sigma.value)
            self.assertIsNotNone(fwhm.value)
            assert sigma.value is not None
            assert fwhm.value is not None
            self.assertAlmostEqual(fwhm.value, sigma.value * 2.354820045, delta=0.25)

    def test_offpulse_selection_changes_noise_basis_and_reports_uncertainties(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = _synthetic_width_session(path=Path(tmpdir) / "explicit_offpulse.fil")

            width_analysis = session.compute_widths()
            self.assertEqual(width_analysis.noise_summary.basis, "explicit")
            self.assertGreater(width_analysis.noise_summary.offpulse_bin_count, 0)
            uncertainties = [result.uncertainty for result in width_analysis.results if result.value is not None]
            self.assertTrue(any(value is not None for value in uncertainties))

            session.clear_offpulse()
            implicit = session.compute_widths()
            self.assertEqual(implicit.noise_summary.basis, "implicit_event_complement")
            self.assertIn("implicit_offpulse", implicit.noise_summary.warning_flags)

    @patch("flits.session.compute_width_analysis")
    def test_compute_widths_uses_reduced_profile_and_sampling(self, mock_compute_width_analysis: object) -> None:
        with TemporaryDirectory() as tmpdir:
            session = _synthetic_width_session(path=Path(tmpdir) / "reduced_widths.fil")
            session.set_time_factor(4)
            session.set_freq_factor(2)

            def _run(**kwargs: object):
                return compute_width_analysis_impl(**kwargs)

            mock_compute_width_analysis.side_effect = _run

            width_analysis = session.compute_widths()

            kwargs = mock_compute_width_analysis.call_args.kwargs
            self.assertEqual(len(kwargs["selected_profile"]), (session.crop_end - session.crop_start) // 4)
            self.assertEqual(kwargs["tsamp_ms"], session.tsamp_ms * 4)
            self.assertTrue(np.isfinite([result.value for result in width_analysis.results if result.value is not None]).any())


if __name__ == "__main__":
    unittest.main()
