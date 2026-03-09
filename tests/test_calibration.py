from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


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


if __name__ == "__main__":
    unittest.main()
