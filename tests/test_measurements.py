from __future__ import annotations

import unittest

import numpy as np
from astropy import units as u

from flits.measurements import build_measurement_context, compute_burst_measurements
from flits.models import NoiseEstimateSettings
from flits.timing import ObservatoryLocation, TimingContext


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
            sefd_fractional_uncertainty=0.1,
            distance_fractional_uncertainty=0.2,
            masked_channels=[],
            offpulse_regions_rel=[(0, 36), (88, 120)],
        )

        expected_toa = 60000.0 + ((1.25 + peak_bin * 1e-3) / 86400.0)
        self.assertAlmostEqual(measurements.toa_peak_topo_mjd or 0.0, expected_toa, places=10)
        self.assertAlmostEqual(measurements.toa_topo_mjd or 0.0, expected_toa, places=10)
        self.assertEqual(measurements.mjd_at_peak, measurements.toa_topo_mjd)
        self.assertEqual(measurements.toa_peak_topo_mjd, measurements.toa_topo_mjd)
        self.assertEqual(measurements.toa_status, "peak_topo_only")
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
        self.assertIsNone(measurements.uncertainties.width_ms_acf)
        self.assertIsNone(measurements.uncertainties.toa_topo_mjd)
        self.assertIsNone(measurements.uncertainties.snr_peak)
        self.assertIsNone(measurements.uncertainties.snr_integrated)
        self.assertEqual(measurements.uncertainty_details["toa_topo_mjd"].classification, "resolution_limit")
        self.assertEqual(measurements.uncertainty_details["toa_peak_topo_mjd"].classification, "resolution_limit")
        self.assertEqual(measurements.uncertainty_details["width_ms_acf"].classification, "resolution_limit")
        self.assertEqual(measurements.uncertainty_details["spectral_width_mhz_acf"].classification, "resolution_limit")
        self.assertEqual(measurements.uncertainty_details["peak_flux_jy"].classification, "formal_1sigma")
        self.assertEqual(measurements.uncertainty_details["fluence_jyms"].classification, "formal_1sigma")
        self.assertEqual(measurements.uncertainty_details["iso_e"].classification, "formal_1sigma")

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
            sefd_fractional_uncertainty=None,
            distance_fractional_uncertainty=None,
            masked_channels=[1],
        )

        self.assertIn("low_sn", measurements.measurement_flags)
        self.assertIn("edge_clipped", measurements.measurement_flags)
        self.assertIn("missing_distance", measurements.measurement_flags)
        self.assertIn("missing_sefd", measurements.measurement_flags)
        self.assertIn("heavily_masked", measurements.measurement_flags)

    def test_noise_estimator_changes_measurement_context_statistics(self) -> None:
        masked = np.zeros((2, 24), dtype=float)
        masked[:, 10:14] = np.array([[5.0, 8.0, 8.0, 5.0], [4.5, 7.5, 7.5, 4.5]])
        masked[0, 0] = 40.0
        freqs = np.array([1100.0, 1099.0], dtype=float)
        offpulse_regions = [(0, 8), (16, 24)]

        mean_std = build_measurement_context(
            masked=masked,
            time_axis_ms=np.arange(masked.shape[1], dtype=float),
            freqs_mhz=freqs,
            event_rel_start=10,
            event_rel_end=14,
            spec_lo=0,
            spec_hi=1,
            freqres_mhz=1.0,
            offpulse_regions=offpulse_regions,
            noise_settings=NoiseEstimateSettings(estimator="mean_std"),
        )
        median_mad = build_measurement_context(
            masked=masked,
            time_axis_ms=np.arange(masked.shape[1], dtype=float),
            freqs_mhz=freqs,
            event_rel_start=10,
            event_rel_end=14,
            spec_lo=0,
            spec_hi=1,
            freqres_mhz=1.0,
            offpulse_regions=offpulse_regions,
            noise_settings=NoiseEstimateSettings(estimator="median_mad"),
        )

        self.assertEqual(mean_std.noise_summary.estimator, "mean_std")
        self.assertEqual(median_mad.noise_summary.estimator, "median_mad")
        self.assertGreater(mean_std.noise_summary.sigma, median_mad.noise_summary.sigma)
        self.assertNotAlmostEqual(
            float(mean_std.selected_profile_sn[11]),
            float(median_mad.selected_profile_sn[11]),
            places=6,
        )

    def test_automatic_toa_uses_event_window_peak_not_crop_wide_maximum(self) -> None:
        masked = np.zeros((4, 32), dtype=float)
        masked[:, 5] = 100.0
        masked[:, 20] = 10.0
        freqs = np.linspace(1100.0, 1000.0, 4)

        measurements = compute_burst_measurements(
            burst_name="event_peak",
            dm=0.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=1.0,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=16,
            event_rel_end=24,
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
            sefd_fractional_uncertainty=None,
            distance_fractional_uncertainty=None,
            masked_channels=[],
        )

        expected_toa = 60000.0 + (20.0e-3 / 86400.0)
        self.assertAlmostEqual(measurements.toa_peak_topo_mjd or 0.0, expected_toa, places=10)
        self.assertEqual(measurements.provenance.toa_peak_selection, "automatic_event_peak")

    def test_manual_peak_inside_event_window_defines_primary_toa(self) -> None:
        masked, freqs = _synthetic_burst(peak_bin=20)

        measurements = compute_burst_measurements(
            burst_name="manual_event_peak",
            dm=0.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=1.0,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=16,
            event_rel_end=28,
            spec_lo=0,
            spec_hi=7,
            peak_bins_abs=[18],
            burst_regions_abs=(),
            manual_selection=True,
            manual_peak_selection=True,
            sefd_jy=None,
            npol=1,
            distance_mpc=None,
            redshift=None,
            sefd_fractional_uncertainty=None,
            distance_fractional_uncertainty=None,
            masked_channels=[],
        )

        expected_toa = 60000.0 + (18.0e-3 / 86400.0)
        self.assertAlmostEqual(measurements.toa_peak_topo_mjd or 0.0, expected_toa, places=10)
        self.assertEqual(measurements.provenance.toa_peak_selection, "manual_event_peak")

    def test_manual_peak_outside_event_window_falls_back_to_automatic_event_peak(self) -> None:
        masked = np.zeros((4, 32), dtype=float)
        masked[:, 5] = 100.0
        masked[:, 20] = 10.0
        freqs = np.linspace(1100.0, 1000.0, 4)

        measurements = compute_burst_measurements(
            burst_name="manual_outside_event",
            dm=0.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=1.0,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=16,
            event_rel_end=24,
            spec_lo=0,
            spec_hi=3,
            peak_bins_abs=[5],
            burst_regions_abs=(),
            manual_selection=True,
            manual_peak_selection=True,
            sefd_jy=None,
            npol=1,
            distance_mpc=None,
            redshift=None,
            sefd_fractional_uncertainty=None,
            distance_fractional_uncertainty=None,
            masked_channels=[],
        )

        expected_toa = 60000.0 + (20.0e-3 / 86400.0)
        self.assertAlmostEqual(measurements.toa_peak_topo_mjd or 0.0, expected_toa, places=10)
        self.assertEqual(measurements.provenance.toa_peak_selection, "automatic_event_peak")

    def test_complete_timing_metadata_reports_infinite_frequency_and_barycentric_fields(self) -> None:
        masked, freqs = _synthetic_burst(peak_bin=20)
        context = TimingContext(
            dm=100.0,
            reference_frequency_mhz=float(np.max(freqs)),
            reference_frequency_basis="test",
            source_ra_deg=180.0,
            source_dec_deg=30.0,
            observatory=ObservatoryLocation(
                name="test",
                longitude_deg=-79.839722,
                latitude_deg=38.433056,
                height_m=807.0,
                basis="test",
            ),
        )

        measurements = compute_burst_measurements(
            burst_name="timed",
            dm=100.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=1.0,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=16,
            event_rel_end=28,
            spec_lo=0,
            spec_hi=7,
            peak_bins_abs=[],
            burst_regions_abs=(),
            manual_selection=False,
            manual_peak_selection=False,
            sefd_jy=None,
            npol=1,
            distance_mpc=None,
            redshift=None,
            sefd_fractional_uncertainty=None,
            distance_fractional_uncertainty=None,
            masked_channels=[],
            timing_context=context,
        )

        self.assertEqual(measurements.toa_status, "barycentric_tdb")
        self.assertIsNotNone(measurements.toa_inf_topo_mjd)
        self.assertIsNotNone(measurements.toa_inf_bary_mjd_tdb)
        self.assertIsNotNone(measurements.dispersion_to_infinite_frequency_ms)
        self.assertIsNotNone(measurements.barycentric_correction_ms)
        self.assertEqual(measurements.toa_reference_frequency_mhz, float(np.max(freqs)))
        self.assertIn("toa_inf_topo_mjd", measurements.uncertainty_details)
        self.assertIn("toa_inf_bary_mjd_tdb", measurements.uncertainty_details)

    def test_dm_zero_timing_context_still_reports_infinite_frequency_and_barycentric_toas(self) -> None:
        masked, freqs = _synthetic_burst(peak_bin=20)
        context = TimingContext(
            dm=0.0,
            reference_frequency_mhz=None,
            source_ra_deg=180.0,
            source_dec_deg=30.0,
            observatory=ObservatoryLocation(
                name="test",
                longitude_deg=-79.839722,
                latitude_deg=38.433056,
                height_m=807.0,
                basis="test",
            ),
        )

        measurements = compute_burst_measurements(
            burst_name="timed_dm_zero",
            dm=0.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=1.0,
            freqres_mhz=abs(freqs[1] - freqs[0]),
            freqs_mhz=freqs,
            masked=masked,
            event_rel_start=16,
            event_rel_end=28,
            spec_lo=0,
            spec_hi=7,
            peak_bins_abs=[],
            burst_regions_abs=(),
            manual_selection=False,
            manual_peak_selection=False,
            sefd_jy=None,
            npol=1,
            distance_mpc=None,
            redshift=None,
            sefd_fractional_uncertainty=None,
            distance_fractional_uncertainty=None,
            masked_channels=[],
            timing_context=context,
        )

        self.assertEqual(measurements.toa_status, "barycentric_tdb")
        self.assertIsNotNone(measurements.toa_peak_topo_mjd)
        self.assertEqual(measurements.toa_inf_topo_mjd, measurements.toa_peak_topo_mjd)
        self.assertIsNotNone(measurements.toa_inf_bary_mjd_tdb)
        self.assertEqual(measurements.dispersion_to_infinite_frequency_ms, 0.0)
        self.assertIn("toa_inf_topo_mjd", measurements.uncertainty_details)
        self.assertIn("toa_inf_bary_mjd_tdb", measurements.uncertainty_details)
        self.assertIn("Assuming DM 0 input is already referenced to infinite frequency", measurements.toa_status_reason)


if __name__ == "__main__":
    unittest.main()
