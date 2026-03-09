from __future__ import annotations

import unittest
from pathlib import Path

from flits.session import BurstSession


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "blc_s_guppi_60385_53711_DIAG_FRB20240114A_0057_40.265_41.518_b32_I0_D527_851_F192D_K_t30_d1.fil"


@unittest.skipUnless(SAMPLE.exists(), "Sample filterbank file is not available")
class BurstSessionSmokeTest(unittest.TestCase):
    def test_generic_session_loads_without_nrt_assumptions(self) -> None:
        session = BurstSession.from_file(str(SAMPLE), dm=527.851, telescope="generic")
        view = session.get_view()

        self.assertEqual(view["meta"]["preset_key"], "generic")
        self.assertEqual(view["meta"]["shape"][0], 192)
        self.assertGreater(view["meta"]["shape"][1], 1000)
        self.assertEqual(view["meta"]["sefd_jy"], None)

    def test_compute_properties_with_manual_calibration(self) -> None:
        session = BurstSession.from_file(str(SAMPLE), dm=527.851, telescope="generic", sefd_jy=25.0)
        peak_ms = session.get_view()["state"]["peak_ms"][0]
        session.set_event_ms(peak_ms - 2.0, peak_ms + 2.0)
        session.add_region_ms(peak_ms - 0.5, peak_ms + 0.5)
        measurements = session.compute_properties()

        self.assertIsNotNone(measurements.fluence_jyms)
        self.assertIsNotNone(measurements.peak_flux_jy)
        self.assertGreaterEqual(len(measurements.gaussian_fits), 1)


if __name__ == "__main__":
    unittest.main()
