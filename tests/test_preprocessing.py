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


def _mock_reader(
    raw: np.ndarray,
    *,
    tsamp: float = 1e-3,
    fch1: float = 1000.0,
    foff: float = -1.0,
    npol: int = 1,
    poln_order: str | None = None,
) -> SimpleNamespace:
    nchan = raw.shape[-1] if raw.ndim == 3 else raw.shape[1]
    header = SimpleNamespace(
        tsamp=tsamp,
        foff=foff,
        tstart=60000.0,
        bw=abs(foff) * nchan,
        npol=npol,
        poln_order=poln_order,
        fch1=fch1,
        nchans=nchan,
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
    @patch("flits.io.your_reader.your.Your")
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

    @patch("flits.io.your_reader.your.Your")
    def test_load_filterbank_data_applies_npol_override_to_metadata(self, mock_your: object) -> None:
        raw = np.array(
            [
                [[1.0, 2.0], [3.0, 4.0]],
                [[2.0, 3.0], [4.0, 5.0]],
                [[3.0, 4.0], [5.0, 6.0]],
                [[4.0, 5.0], [6.0, 7.0]],
            ],
            dtype=float,
        )
        mock_your.return_value = _mock_reader(raw, npol=2)
        config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0, npol_override=1)

        _, metadata = load_filterbank_data(
            "synthetic-npol-override.fil",
            config,
            inspection=_synthetic_inspection("synthetic-npol-override.fil"),
        )

        self.assertEqual(metadata.header_npol, 2)
        self.assertEqual(metadata.npol, 1)

    @patch("flits.io.your_reader.your.Your")
    def test_load_filterbank_data_uses_stokes_i_plane_for_iquv(self, mock_your: object) -> None:
        stokes_i = np.array(
            [
                [10.0, 20.0, 30.0],
                [11.0, 18.0, 31.0],
                [10.0, 19.0, 32.0],
                [10.0, 20.0, 33.0],
                [12.0, 22.0, 34.0],
                [12.0, 21.0, 35.0],
                [11.0, 22.0, 36.0],
                [10.0, 23.0, 37.0],
            ],
            dtype=float,
        )
        raw = np.zeros((stokes_i.shape[0], 4, stokes_i.shape[1]), dtype=float)
        raw[:, 0, :] = stokes_i
        raw[:, 1, :] = 100.0 + np.arange(stokes_i.size, dtype=float).reshape(stokes_i.shape)
        raw[:, 2, :] = -5.0
        raw[:, 3, :] = 2.0
        mock_your.return_value = _mock_reader(raw, npol=4, poln_order="IQUV")
        config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)

        data, metadata = load_filterbank_data(
            "synthetic-iquv.fil",
            config,
            inspection=_synthetic_inspection("synthetic-iquv.fil"),
        )

        stokes_i_channels_time = stokes_i.T
        tail_fraction = float(np.clip(config.normalization_tail_fraction, 0.05, 0.95))
        offpulse_start = min(stokes_i_channels_time.shape[1] - 1, int((1 - tail_fraction) * stokes_i_channels_time.shape[1]))
        expected = normalize(stokes_i_channels_time, stokes_i_channels_time[:, offpulse_start:])
        old_i_plus_q = (raw[:, 0, :] + raw[:, 1, :]).T
        old_expected = normalize(old_i_plus_q, old_i_plus_q[:, offpulse_start:])
        np.testing.assert_allclose(data, expected)
        self.assertFalse(np.allclose(data, old_expected))
        self.assertEqual(metadata.header_npol, 4)
        self.assertEqual(metadata.npol, 2)
        self.assertEqual(metadata.polarization_order, "IQUV")

    @patch("flits.io.your_reader.your.Your")
    def test_load_filterbank_data_preserves_non_iquv_first_two_plane_sum(self, mock_your: object) -> None:
        raw = np.array(
            [
                [[1.0, 2.0, 3.0], [10.0, 20.0, 30.0], [100.0, 200.0, 300.0], [5.0, 5.0, 5.0]],
                [[2.0, 3.0, 4.0], [11.0, 21.0, 31.0], [101.0, 201.0, 301.0], [5.0, 5.0, 5.0]],
                [[3.0, 4.0, 5.0], [12.0, 22.0, 32.0], [102.0, 202.0, 302.0], [5.0, 5.0, 5.0]],
                [[4.0, 5.0, 6.0], [13.0, 23.0, 33.0], [103.0, 203.0, 303.0], [5.0, 5.0, 5.0]],
                [[5.0, 6.0, 7.0], [14.0, 24.0, 34.0], [104.0, 204.0, 304.0], [5.0, 5.0, 5.0]],
                [[6.0, 7.0, 8.0], [15.0, 25.0, 35.0], [105.0, 205.0, 305.0], [5.0, 5.0, 5.0]],
                [[7.0, 8.0, 9.0], [16.0, 26.0, 36.0], [106.0, 206.0, 306.0], [5.0, 5.0, 5.0]],
                [[8.0, 9.0, 10.0], [17.0, 27.0, 37.0], [107.0, 207.0, 307.0], [5.0, 5.0, 5.0]],
            ],
            dtype=float,
        )
        mock_your.return_value = _mock_reader(raw, npol=4, poln_order="AABB")
        config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)

        data, metadata = load_filterbank_data(
            "synthetic-aabb.fil",
            config,
            inspection=_synthetic_inspection("synthetic-aabb.fil"),
        )

        summed = (raw[:, 0, :] + raw[:, 1, :]).T
        tail_fraction = float(np.clip(config.normalization_tail_fraction, 0.05, 0.95))
        offpulse_start = min(summed.shape[1] - 1, int((1 - tail_fraction) * summed.shape[1]))
        expected = normalize(summed, summed[:, offpulse_start:])
        np.testing.assert_allclose(data, expected)
        self.assertEqual(metadata.header_npol, 4)
        self.assertEqual(metadata.npol, 2)
        self.assertEqual(metadata.polarization_order, "AABB")

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
