from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from flits.io.filterbank import FilterbankInspection, load_filterbank_data
from flits.models import FilterbankMetadata
from flits.session import BurstSession, _adaptive_jess_time_bin_limit
from flits.settings import ObservationConfig, get_auto_mask_profile
from flits.signal import normalize


def _mock_reader(raw: np.ndarray, *, tsamp: float = 1e-3, fch1: float = 1000.0, foff: float = -1.0) -> SimpleNamespace:
    header = SimpleNamespace(
        tsamp=tsamp,
        foff=foff,
        tstart=60000.0,
        bw=abs(foff) * raw.shape[1],
        npol=1,
        fch1=fch1,
        nchans=raw.shape[1],
        nspectra=raw.shape[0],
    )
    return SimpleNamespace(
        your_header=header,
        telescope_id=None,
        machine_id=None,
        source_name="synthetic",
        fp=None,
        get_data=lambda nstart, nread, npoln=1: raw[nstart : nstart + nread],
    )


def _synthetic_inspection(path: str) -> FilterbankInspection:
    return FilterbankInspection(
        source_path=Path(path),
        source_name="synthetic",
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="synthetic",
    )


class ViewerAndMaskingTest(unittest.TestCase):
    @patch("flits.io.filterbank.your.Your")
    def test_legacy_mode_matches_existing_fixed_tail_normalization(self, mock_your: object) -> None:
        raw = np.array(
            [
                [10.0, 20.0, 30.0, 40.0],
                [11.0, 18.0, 31.0, 39.0],
                [10.0, 19.0, 32.0, 38.0],
                [10.0, 20.0, 33.0, 37.0],
                [12.0, 22.0, 34.0, 36.0],
                [12.0, 21.0, 35.0, 35.0],
                [11.0, 22.0, 36.0, 34.0],
                [10.0, 23.0, 37.0, 33.0],
            ],
            dtype=float,
        )
        mock_your.return_value = _mock_reader(raw)
        config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)

        data, _ = load_filterbank_data("synthetic-legacy.fil", config, inspection=_synthetic_inspection("synthetic-legacy.fil"))

        stokes_i = raw.T
        tail_fraction = float(np.clip(config.normalization_tail_fraction, 0.05, 0.95))
        offpulse_start = min(stokes_i.shape[1] - 1, int((1 - tail_fraction) * stokes_i.shape[1]))
        expected = normalize(stokes_i, stokes_i[:, offpulse_start:])
        self.assertEqual(data.dtype, np.float32)
        np.testing.assert_allclose(data, expected)

    def test_view_flattens_static_channel_offsets_for_display(self) -> None:
        config = ObservationConfig.from_preset(dm=0.0, preset_key="generic")
        data = np.array(
            [
                [5.0, 5.0, 8.0, 5.0],
                [20.0, 20.0, 24.0, 20.0],
                [-7.0, -7.0, -5.0, -7.0],
            ],
            dtype=float,
        )
        metadata = FilterbankMetadata(
            source_path=Path("display-flat.fil"),
            source_name="synthetic",
            tsamp=1e-3,
            freqres=1.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            sefd_jy=None,
            bandwidth_mhz=3.0,
            npol=1,
            freqs_mhz=np.array([1002.0, 1001.0, 1000.0]),
            header_npol=1,
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="synthetic",
        )
        session = BurstSession(
            config=config,
            metadata=metadata,
            data=data,
            crop_start=0,
            crop_end=data.shape[1],
            event_start=1,
            event_end=2,
            spec_ex_lo=0,
            spec_ex_hi=data.shape[0] - 1,
            channel_mask=np.zeros(data.shape[0], dtype=bool),
        )

        view = session.get_view()

        self.assertEqual(view["plot"]["time_profile"]["y"], [-2.25, -2.25, 6.75, -2.25])
        self.assertEqual(view["plot"]["spectrum"]["x"], [0.0, 0.0, 0.0])

    @patch("flits.session.jess.channel_masks.channel_masker")
    def test_auto_mask_jess_uses_profile_budget_for_sampling(self, mock_channel_masker: object) -> None:
        num_channels = 1024
        num_time_bins = 10_000
        data = np.random.default_rng(1234).normal(size=(num_channels, num_time_bins)).astype(np.float32)
        metadata = FilterbankMetadata(
            source_path=Path("jess-large.fil"),
            source_name="synthetic",
            tsamp=1e-3,
            freqres=1.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            sefd_jy=None,
            bandwidth_mhz=float(num_channels),
            npol=1,
            freqs_mhz=np.linspace(1003.0, 1000.0, num_channels),
            header_npol=1,
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="synthetic",
        )
        session = BurstSession(
            config=ObservationConfig.from_preset(dm=0.0, preset_key="generic"),
            metadata=metadata,
            data=data,
            crop_start=0,
            crop_end=num_time_bins,
            event_start=4_500,
            event_end=5_500,
            spec_ex_lo=0,
            spec_ex_hi=num_channels - 1,
            channel_mask=np.zeros(num_channels, dtype=bool),
        )
        fast_profile = get_auto_mask_profile("fast")
        expected_samples = _adaptive_jess_time_bin_limit(num_channels, fast_profile.memory_budget_mb)

        def _fake_masker(dynamic_spectra: np.ndarray, **_: object) -> np.ndarray:
            self.assertEqual(dynamic_spectra.shape[1], num_channels)
            self.assertEqual(dynamic_spectra.shape[0], expected_samples)
            return np.zeros(num_channels, dtype=bool)

        mock_channel_masker.side_effect = _fake_masker

        session.auto_mask_jess("fast")

        self.assertEqual(np.flatnonzero(session.channel_mask).tolist(), [])
        self.assertIsNotNone(session.last_auto_mask)
        self.assertEqual(session.last_auto_mask.profile, "fast")
        self.assertEqual(session.last_auto_mask.sampled_time_bins, expected_samples)
        self.assertEqual(session.last_auto_mask.candidate_time_bins, 9_000)
        self.assertEqual(session.last_auto_mask.test_used, "stand-dev")

    @patch("flits.session.jess.channel_masks.channel_masker")
    def test_auto_mask_jess_falls_back_from_skew_and_masks_constant_channels(self, mock_channel_masker: object) -> None:
        data = np.array(
            [
                np.full(128, 7.0),
                np.linspace(-1.0, 1.0, 128),
                np.sin(np.linspace(0.0, 6.0, 128)),
                np.cos(np.linspace(0.0, 6.0, 128)),
            ],
            dtype=float,
        )
        metadata = FilterbankMetadata(
            source_path=Path("jess-fallback.fil"),
            source_name="synthetic",
            tsamp=1e-3,
            freqres=1.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            sefd_jy=None,
            bandwidth_mhz=4.0,
            npol=1,
            freqs_mhz=np.array([1003.0, 1002.0, 1001.0, 1000.0]),
            header_npol=1,
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="synthetic",
        )
        session = BurstSession(
            config=ObservationConfig.from_preset(dm=0.0, preset_key="generic"),
            metadata=metadata,
            data=data,
            crop_start=0,
            crop_end=data.shape[1],
            event_start=48,
            event_end=80,
            spec_ex_lo=0,
            spec_ex_hi=data.shape[0] - 1,
            channel_mask=np.zeros(data.shape[0], dtype=bool),
        )
        mock_channel_masker.side_effect = [
            np.array([False, False, False], dtype=bool),
            np.array([False, True, False], dtype=bool),
        ]

        session.auto_mask_jess("auto")

        self.assertEqual(np.flatnonzero(session.channel_mask).tolist(), [0, 2])
        self.assertIsNotNone(session.last_auto_mask)
        self.assertEqual(session.last_auto_mask.test_used, "stand-dev")
        self.assertEqual(session.last_auto_mask.tests_tried, ("skew", "stand-dev"))
        self.assertEqual(session.last_auto_mask.constant_channel_count, 1)
        self.assertEqual(session.last_auto_mask.added_channel_count, 2)


if __name__ == "__main__":
    unittest.main()
