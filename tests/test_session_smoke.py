from __future__ import annotations

import unittest
from pathlib import Path

from flits.session import BurstSession


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "blc_s_guppi_60385_53711_DIAG_FRB20240114A_0057_40.265_41.518_b32_I0_D527_851_F192D_K_t30_d1.fil"


@unittest.skipUnless(SAMPLE.exists(), "Sample filterbank file is not available")
class BurstSessionSmokeTest(unittest.TestCase):
    def test_session_auto_detects_gbt_when_telescope_is_omitted(self) -> None:
        session = BurstSession.from_file(str(SAMPLE), dm=527.851)
        view = session.get_view()

        self.assertEqual(view["meta"]["preset_key"], "gbt")
        self.assertEqual(view["meta"]["detected_preset_key"], "gbt")
        self.assertEqual(view["meta"]["detected_telescope"], "GBT")
        self.assertEqual(view["meta"]["telescope_id"], 6)
        self.assertEqual(view["meta"]["shape"][0], 192)
        self.assertGreater(view["meta"]["shape"][1], 1000)
        self.assertEqual(view["meta"]["sefd_jy"], 10.0)

    def test_explicit_override_keeps_generic_while_reporting_detected_gbt(self) -> None:
        session = BurstSession.from_file(str(SAMPLE), dm=527.851, telescope="generic")
        view = session.get_view()

        self.assertEqual(view["meta"]["preset_key"], "generic")
        self.assertEqual(view["meta"]["telescope"], "Generic Filterbank")
        self.assertEqual(view["meta"]["detected_preset_key"], "gbt")
        self.assertEqual(view["meta"]["detected_telescope"], "GBT")

    def test_compute_properties_with_auto_calibration(self) -> None:
        session = BurstSession.from_file(str(SAMPLE), dm=527.851)
        peak_ms = session.get_view()["state"]["peak_ms"][0]
        session.set_event_ms(peak_ms - 2.0, peak_ms + 2.0)
        session.add_region_ms(peak_ms - 0.5, peak_ms + 0.5)
        measurements = session.compute_properties()

        self.assertIsNotNone(measurements.fluence_jyms)
        self.assertIsNotNone(measurements.peak_flux_jy)
        self.assertGreaterEqual(len(measurements.gaussian_fits), 1)
        payload = measurements.to_dict()
        self.assertNotIn("mjd_offset_ms", payload)
        self.assertNotIn("burst_time_from_filename", payload)


if __name__ == "__main__":
    unittest.main()
