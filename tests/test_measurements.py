from __future__ import annotations

import unittest

import numpy as np
from astropy import units as u

from flits.measurements import build_measurement_context, compute_burst_measurements


def _synthetic_burst(
    *,
    num_channels: int = 8,
    num_time_bins: int = 128,
    peak_bin: int = 60,
    temporal_sigma_bins: float = 2.5,
    spectral_sigma_channels: float = 1.2,
    amplitude: float = 12.0,
) -> tuple[np.ndarray, np.ndarray]:
    time = np.arange(num_time_bins, dtype=float)
    chans = np.arange(num_channels, dtype=float)
    temporal = np.exp(-0.5 * ((time - peak_bin) / temporal_sigma_bins) ** 2)
    spectral = np.exp(-0.5 * ((chans - ((num_channels - 1) / 2.0)) / spectral_sigma_channels) ** 2)
    return amplitude * spectral[:, None] * temporal[None, :], np.linspace(1100.0, 1000.0, num_channels)


class MeasurementEngineTest(unittest.TestCase):
    def test_compute_burst_measurements_reports_primary_metrics_and_nested_metadata(self) -> None:
        masked, freqs = _synthetic_burst()
        peak_bin = 60
        tsamp_ms = 1.0

        measurements = compute_burst_measurements(
            burst_name="synthetic",
            dm=100.0,
            start_mjd=60000.0,
            read_start_sec=1.25,
            crop_start_bin=0,
            tsamp_ms=tsamp_ms,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=peak_bin - 8,
            event_rel_end=peak_bin + 8,
            spec_lo=1,
            spec_hi=6,
            peak_bins_abs=[peak_bin],
            burst_regions_abs=((peak_bin - 6, peak_bin + 6),),
            manual_selection=True,
            manual_peak_selection=True,
            sefd_jy=10.0,
            npol=1,
            distance_mpc=150.0,
            redshift=0.05,
            masked_channels=[],
        )

        expected_toa = 60000.0 + ((1.25 + peak_bin * 1e-3) / 86400.0)
        self.assertAlmostEqual(measurements.toa_topo_mjd or 0.0, expected_toa, places=10)
        self.assertEqual(measurements.mjd_at_peak, measurements.toa_topo_mjd)
        self.assertGreater(measurements.snr_peak or 0.0, 1.0)
        self.assertGreater(measurements.snr_integrated or 0.0, 1.0)
        self.assertIsNotNone(measurements.width_ms_acf)
        self.assertIsNotNone(measurements.spectral_width_mhz_acf)
        self.assertIsNotNone(measurements.peak_flux_jy)
        self.assertIsNotNone(measurements.fluence_jyms)
        self.assertIsNotNone(measurements.iso_e)
        expected_iso_e = (
            4
            * np.pi
            * measurements.fluence_jyms
            * u.Jy
            * u.ms
            * measurements.provenance.effective_bandwidth_mhz
            * u.MHz
            * (150.0 * u.megaparsec) ** 2
            / (1 + 0.05)
        ).to_value(u.erg)
        self.assertAlmostEqual(measurements.iso_e or 0.0, expected_iso_e, places=6)
        self.assertIn("acf", measurements.measurement_flags)
        self.assertIn("calibrated", measurements.measurement_flags)
        self.assertIn("manual", measurements.measurement_flags)
        self.assertIn("fit", measurements.measurement_flags)
        self.assertGreaterEqual(len(measurements.diagnostics.gaussian_fits), 1)
        self.assertEqual(measurements.provenance.calibration_method, "radiometer_equation")
        self.assertEqual(measurements.provenance.energy_unit, "erg")
        self.assertIsNotNone(measurements.uncertainties.width_ms_acf)
        self.assertIsNone(measurements.uncertainties.snr_peak)
        self.assertIsNone(measurements.uncertainties.snr_integrated)

    def test_measurement_context_tracks_effective_bandwidth_after_masking(self) -> None:
        masked, freqs = _synthetic_burst(num_channels=6, num_time_bins=64)
        masked[[1, 4], :] = np.nan

        context = build_measurement_context(
            masked=masked,
            time_axis_ms=np.arange(masked.shape[1], dtype=float),
            freqs_mhz=freqs,
            event_rel_start=24,
            event_rel_end=40,
            spec_lo=0,
            spec_hi=5,
            freqres_mhz=abs(freqs[1] - freqs[0]),
        )

        self.assertEqual(context.selected_channel_count, 6)
        self.assertEqual(context.active_channel_count, 4)
        self.assertAlmostEqual(context.effective_bandwidth_mhz, 4 * abs(freqs[1] - freqs[0]), places=6)

    def test_low_sn_edge_clipped_and_missing_inputs_raise_quality_flags(self) -> None:
        masked = np.zeros((4, 32), dtype=float)
        masked[:, 2] = 0.05
        freqs = np.linspace(1100.0, 1000.0, 4)
        masked[1, :] = np.nan

        measurements = compute_burst_measurements(
            burst_name="low_sn",
            dm=0.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=1.0,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=0,
            event_rel_end=8,
            spec_lo=0,
            spec_hi=3,
            peak_bins_abs=[],
            burst_regions_abs=(),
            manual_selection=False,
            manual_peak_selection=False,
            sefd_jy=None,
            npol=1,
            distance_mpc=None,
            redshift=None,
            masked_channels=[1],
        )

        self.assertIn("low_sn", measurements.measurement_flags)
        self.assertIn("edge_clipped", measurements.measurement_flags)
        self.assertIn("missing_distance", measurements.measurement_flags)
        self.assertIn("missing_sefd", measurements.measurement_flags)
        self.assertIn("heavily_masked", measurements.measurement_flags)


if __name__ == "__main__":
    unittest.main()
