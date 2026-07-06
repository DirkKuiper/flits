from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from flits.measurements import compute_burst_measurements, saturation_diagnostic
from flits.models import BurstMeasurements, FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


def _saturation_test_measurements(profile: np.ndarray, *, event_start: int, event_end: int) -> BurstMeasurements:
    profile = np.asarray(profile, dtype=float)
    data = np.repeat(profile[None, :], 4, axis=0)
    freqs = np.array([1000.0, 1001.0, 1002.0, 1003.0], dtype=float)
    return compute_burst_measurements(
        burst_name="saturation_synthetic",
        dm=0.0,
        start_mjd=60000.0,
        read_start_sec=0.0,
        crop_start_bin=0,
        tsamp_ms=1.0,
        freqres_mhz=1.0,
        freqs_mhz=freqs,
        masked=data,
        event_rel_start=event_start,
        event_rel_end=event_end,
        spec_lo=0,
        spec_hi=3,
        peak_bins_abs=[int(np.nanargmax(profile))],
        burst_regions_abs=((event_start, event_end),),
        manual_selection=True,
        manual_peak_selection=True,
        sefd_jy=10.0,
        npol=1,
        distance_mpc=None,
        redshift=None,
        sefd_fractional_uncertainty=0.2,
        distance_fractional_uncertainty=None,
        masked_channels=[],
        offpulse_regions_rel=[(0, 32), (100, 128)],
    )


class CalibrationTest(unittest.TestCase):
    def test_flux_and_fluence_use_selected_effective_bandwidth(self) -> None:
        config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=10.0)
        metadata = FilterbankMetadata(
            source_path=Path("synthetic.fil"),
            source_name="synthetic",
            tsamp=1e-3,
            freqres=1.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            sefd_jy=10.0,
            bandwidth_mhz=4.0,
            npol=1,
            freqs_mhz=np.array([1000.0, 1001.0, 1002.0, 1003.0]),
            header_npol=1,
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="synthetic",
        )
        data = np.array(
            [
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 10.0, 10.0, 0.0, 0.0],
                [0.0, 0.0, 10.0, 10.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ],
            dtype=float,
        )
        session = BurstSession(
            config=config,
            metadata=metadata,
            data=data,
            crop_start=0,
            crop_end=data.shape[1],
            event_start=2,
            event_end=4,
            spec_ex_lo=1,
            spec_ex_hi=2,
            channel_mask=np.zeros(data.shape[0], dtype=bool),
        )

        measurements = session.compute_properties()

        self.assertAlmostEqual(measurements.peak_flux_jy, 2.2360679775, places=6)
        self.assertAlmostEqual(measurements.fluence_jyms, 4.472135955, places=6)

    def test_saturation_clean_burst_has_no_warning_flags(self) -> None:
        profile = np.zeros(128, dtype=float)
        profile[54:66] = 10.0

        measurements = _saturation_test_measurements(profile, event_start=50, event_end=70)

        self.assertNotIn("saturation_suspected", measurements.measurement_flags)
        self.assertNotIn("negative_recovery_wing", measurements.measurement_flags)
        self.assertEqual(measurements.diagnostics.saturation.status, "ok")
        self.assertTrue(measurements.uncertainty_details["fluence_jyms"].publishable)

    def test_saturation_adjacent_negative_recovery_sets_warning_flags(self) -> None:
        profile = np.zeros(128, dtype=float)
        profile[54:66] = 10.0
        profile[70:84] = -7.0

        measurements = _saturation_test_measurements(profile, event_start=50, event_end=70)

        self.assertIsNotNone(measurements.fluence_jyms)
        self.assertIn("saturation_suspected", measurements.measurement_flags)
        self.assertIn("negative_recovery_wing", measurements.measurement_flags)
        self.assertEqual(measurements.diagnostics.saturation.status, "suspected")
        self.assertGreaterEqual(measurements.diagnostics.saturation.negative_wing_excess_significance or 0.0, 5.0)
        self.assertIn(
            "saturation_suspected",
            measurements.uncertainty_details["fluence_jyms"].warning_flags,
        )
        self.assertIn(
            "negative_recovery_wing",
            measurements.uncertainty_details["peak_flux_jy"].warning_flags,
        )
        self.assertFalse(measurements.uncertainty_details["fluence_jyms"].publishable)
        self.assertFalse(measurements.uncertainty_details["peak_flux_jy"].publishable)

    def test_saturation_event_negative_tail_sets_warning_flags(self) -> None:
        profile = np.zeros(128, dtype=float)
        profile[48:58] = 10.0
        profile[60:70] = -5.0

        measurements = _saturation_test_measurements(profile, event_start=45, event_end=75)

        self.assertIn("saturation_suspected", measurements.measurement_flags)
        self.assertIn("negative_event_tail", measurements.measurement_flags)
        self.assertGreaterEqual(measurements.diagnostics.saturation.event_negative_fraction or 0.0, 0.15)
        self.assertFalse(measurements.uncertainty_details["fluence_jyms"].publishable)

    def test_single_noise_excursion_does_not_trigger_saturation(self) -> None:
        profile = np.zeros(128, dtype=float)
        profile[54:66] = 10.0
        profile[72] = -6.0

        measurements = _saturation_test_measurements(profile, event_start=50, event_end=70)

        self.assertNotIn("saturation_suspected", measurements.measurement_flags)
        self.assertNotIn("negative_recovery_wing", measurements.measurement_flags)

    def test_old_measurement_payload_without_saturation_diagnostic_deserializes(self) -> None:
        profile = np.zeros(128, dtype=float)
        profile[54:66] = 10.0
        measurements = _saturation_test_measurements(profile, event_start=50, event_end=70)
        payload = measurements.to_dict()
        payload["diagnostics"].pop("saturation", None)

        restored = BurstMeasurements.from_dict(payload)

        self.assertEqual(restored.diagnostics.saturation.status, "ok")


class SaturationStatisticTest(unittest.TestCase):
    """Unit tests for the saturation statistic on synthetic S/N profiles."""

    def test_wide_noise_wings_with_one_deep_bin_do_not_trigger(self) -> None:
        # Regression: the uncorrected negative-area statistic grows as
        # 0.4*sqrt(N) for pure noise, so wide windows plus a single deep
        # noise bin used to flag healthy bursts.
        rng = np.random.default_rng(1234)
        profile = rng.standard_normal(4096)
        profile[2000:2060] = 30.0
        profile[2500] = -5.5

        # Wide event window (as over-generous pipelines produce) makes the
        # wings 500 bins each; the deep bin sits inside the right wing.
        result = saturation_diagnostic(profile, 1900, 2400)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.warning_flags, [])

    def test_pure_noise_wings_do_not_trigger(self) -> None:
        for seed in range(12):
            rng = np.random.default_rng(seed)
            profile = rng.standard_normal(2048)
            profile[1000:1030] = 25.0
            result = saturation_diagnostic(profile, 900, 1200)
            self.assertEqual(result.status, "ok", f"false positive for seed={seed}")

    def test_broad_shallow_recovery_trough_triggers_run_arm(self) -> None:
        # A -2.5 sigma trough over 60 bins never reaches the deep-dip gate
        # but is an unmistakable AGC-recovery signature.
        rng = np.random.default_rng(42)
        profile = 0.1 * rng.standard_normal(2048)
        profile[1000:1030] = 25.0
        profile[1045:1105] = -2.5

        result = saturation_diagnostic(profile, 995, 1040)

        self.assertEqual(result.status, "suspected")
        self.assertIn("negative_recovery_wing", result.warning_flags)
        self.assertGreaterEqual(
            result.negative_wing_max_run_bins,
            result.negative_wing_run_threshold_bins,
        )

    def test_one_sided_dip_is_not_diluted_by_clean_wing(self) -> None:
        rng = np.random.default_rng(7)
        profile = 0.1 * rng.standard_normal(4096)
        profile[2000:2050] = 30.0
        profile[2060:2140] = -3.5

        result = saturation_diagnostic(profile, 1990, 2055)

        self.assertEqual(result.status, "suspected")
        self.assertIn("negative_recovery_wing", result.warning_flags)
        self.assertLessEqual(result.right_wing_min_sn or 0.0, -3.0)
        self.assertGreater(result.left_wing_min_sn or -10.0, -3.0)

    def test_run_threshold_scales_with_wing_size(self) -> None:
        from flits.measurements import _negative_run_threshold

        self.assertGreaterEqual(_negative_run_threshold(64), 5)
        self.assertLess(_negative_run_threshold(64), _negative_run_threshold(100000))
        self.assertLessEqual(_negative_run_threshold(4096), 10)


if __name__ == "__main__":
    unittest.main()
