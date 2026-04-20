from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from flits.io.filterbank import FilterbankInspection, load_filterbank_data
from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


DM_CONST = 1 / (2.41 * 10 ** -4)


def _synthetic_dispersed_raw(freqs_mhz: np.ndarray, dm: float, tsamp: float, num_time_bins: int, aligned_bin: int) -> np.ndarray:
    freqs = np.asarray(freqs_mhz, dtype=float)
    reffreq = float(np.max(freqs))
    time_shift = DM_CONST * float(dm) * (reffreq ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / float(tsamp)).astype(int)

    raw = np.zeros((num_time_bins, freqs.size), dtype=float)
    for chan, shift in enumerate(bin_shift):
        raw[aligned_bin - shift, chan] = 10.0
    return raw


def _descending_frequency_session() -> BurstSession:
    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=10.0)
    metadata = FilterbankMetadata(
        source_path=Path("descending.fil"),
        source_name="descending",
        tsamp=1e-3,
        freqres=1.0,
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=4.0,
        npol=1,
        freqs_mhz=np.array([103.0, 102.0, 101.0, 100.0]),
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
            [0.0, 0.0, 10.0, 10.0, 0.0, 0.0],
        ],
        dtype=float,
    )
    return BurstSession(
        config=config,
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=data.shape[1],
        event_start=2,
        event_end=4,
        spec_ex_lo=0,
        spec_ex_hi=3,
        channel_mask=np.zeros(data.shape[0], dtype=bool),
    )


class FilterbankFrequencyOrderTest(unittest.TestCase):
    @patch("flits.io.your_reader.your.Your")
    def test_load_filterbank_preserves_native_order_for_negative_foff(self, mock_your: object) -> None:
        header = SimpleNamespace(
            tsamp=0.01,
            foff=-200.0,
            tstart=60000.0,
            bw=800.0,
            npol=1,
            fch1=2000.0,
            nchans=4,
            nspectra=64,
        )
        native_freqs = np.array([2000.0, 1800.0, 1600.0, 1400.0])
        raw = _synthetic_dispersed_raw(native_freqs, dm=100.0, tsamp=header.tsamp, num_time_bins=64, aligned_bin=20)

        mock_your.return_value = SimpleNamespace(
            your_header=header,
            telescope_id=None,
            machine_id=None,
            source_name="synthetic",
            fp=None,
            get_data=lambda nstart, nread, npoln=1: raw[nstart : nstart + nread],
        )
        inspection = FilterbankInspection(
            source_path=Path("synthetic-negative-foff.fil"),
            source_name="synthetic",
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="synthetic",
        )
        config = ObservationConfig.from_preset(dm=100.0, preset_key="generic", sefd_jy=1.0)

        data, metadata = load_filterbank_data("synthetic-negative-foff.fil", config, inspection=inspection)

        self.assertTrue(np.array_equal(metadata.freqs_mhz, native_freqs))
        self.assertEqual(np.argmax(data, axis=1).tolist(), [20, 20, 20, 20])


class SessionFrequencyOrderTest(unittest.TestCase):
    def test_descending_frequency_session_keeps_ranges_sorted_and_slices_contiguous(self) -> None:
        session = _descending_frequency_session()

        initial_view = session.get_view()
        self.assertEqual(initial_view["meta"]["freq_range_mhz"], [100.0, 103.0])
        self.assertEqual(initial_view["plot"]["heatmap"]["y_mhz"], [103.0, 102.0, 101.0, 100.0])

        session.mask_range_freq(100.2, 102.2)
        self.assertEqual(np.flatnonzero(session.channel_mask).tolist(), [1, 2, 3])

        session.reset_mask()
        session.set_spectral_extent_freq(100.2, 101.8)
        self.assertEqual((session.spec_ex_lo, session.spec_ex_hi), (1, 3))

        view = session.get_view()
        self.assertEqual(view["state"]["spectral_extent_mhz"], [100.0, 102.0])

        measurements = session.compute_properties()
        self.assertAlmostEqual(measurements.spectral_extent_mhz, 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
