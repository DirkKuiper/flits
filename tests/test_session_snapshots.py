from __future__ import annotations

from dataclasses import replace
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from flits.models import AutoMaskRunSummary, FilterbankMetadata, SpectralAnalysisResult
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


def _snapshot_loader(path: str, *, dm: float, **_: object) -> BurstSession:
    return _synthetic_width_session(path=Path(path), applied_dm=dm)


class SessionSnapshotTest(unittest.TestCase):
    def test_compute_properties_uses_reduced_resolution(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = _synthetic_width_session(path=Path(tmpdir) / "reduced_properties.fil")
            session.set_time_factor(4)
            session.set_freq_factor(2)

            results = session.compute_properties()

            self.assertEqual(results.provenance.tsamp_ms, session.tsamp_ms * 4)
            self.assertEqual(results.provenance.freqres_mhz, abs(session.freqres) * 2)
            self.assertEqual(
                results.diagnostics.time_axis_ms.size,
                (session.crop_end - session.crop_start) // 4,
            )

    def test_snapshot_round_trip_restores_analysis_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "snapshot_source.fil"
            snapshot_path.write_bytes(b"snapshot-source")
            session = _synthetic_width_session(path=snapshot_path)
            session.set_time_factor(2)
            session.set_freq_factor(2)
            session.set_crop_ms(10.0, 220.0)
            session.set_event_ms(102.0, 142.0)
            session.add_region_ms(108.0, 136.0)
            session.add_peak_ms(120.0)
            session.mask_channel_freq(float(session.freqs[0]))
            session.set_spectral_extent_freq(float(session.freqs[1]), float(session.freqs[-2]))
            session.set_notes("phase-1 snapshot")
            session.last_auto_mask = AutoMaskRunSummary(
                profile="auto",
                profile_label="Auto",
                memory_budget_mb=96,
                candidate_time_bins=48,
                sampled_time_bins=32,
                eligible_channels=15,
                constant_channel_count=0,
                detected_channel_count=1,
                added_channel_count=1,
                test_used="stand-dev",
                tests_tried=("stand-dev",),
            )
            session.compute_properties()
            session.compute_widths()
            session.accept_width_result("gaussian_fwhm")
            session.optimize_dm(center_dm=0.0, half_range=2.0, step=0.5)
            session.spectral_analysis = SpectralAnalysisResult(
                status="ok",
                message=None,
                segment_length_ms=8.0,
                segment_bins=8,
                segment_count=5,
                normalization="none",
                event_window_ms=[102.0, 142.0],
                spectral_extent_mhz=[float(session.freqs[-2]), float(session.freqs[1])],
                tsamp_ms=session.tsamp_ms,
                frequency_resolution_hz=125.0,
                nyquist_hz=500.0,
                freq_hz=np.array([125.0, 250.0, 375.0], dtype=float),
                power=np.array([1.2, 0.8, 0.3], dtype=float),
            )

            snapshot = session.to_snapshot()
            restored = BurstSession.from_snapshot(snapshot, loader=_snapshot_loader)

            self.assertEqual(restored.crop_start, session.crop_start)
            self.assertEqual(restored.crop_end, session.crop_end)
            self.assertEqual(restored.event_start, session.event_start)
            self.assertEqual(restored.event_end, session.event_end)
            self.assertEqual(restored.time_factor, session.time_factor)
            self.assertEqual(restored.freq_factor, session.freq_factor)
            self.assertEqual(restored.offpulse_regions, session.offpulse_regions)
            self.assertEqual(restored.burst_regions, session.burst_regions)
            self.assertEqual(restored.notes, "phase-1 snapshot")
            self.assertEqual(np.flatnonzero(restored.channel_mask).tolist(), np.flatnonzero(session.channel_mask).tolist())
            self.assertIsNotNone(restored.last_auto_mask)
            self.assertIsNotNone(restored.width_analysis)
            self.assertEqual(restored.width_analysis.accepted_width.method, "gaussian_fwhm")
            self.assertIsNotNone(restored.results)
            self.assertEqual(restored.results.accepted_width.method, "gaussian_fwhm")
            self.assertIsNotNone(restored.dm_optimization)
            self.assertEqual(restored.dm_optimization.settings.metric, "integrated_event_snr")
            self.assertIsNotNone(restored.spectral_analysis)
            self.assertEqual(restored.spectral_analysis.status, "ok")
            self.assertEqual(restored.spectral_analysis.segment_bins, 8)
            self.assertTrue(np.allclose(restored.spectral_analysis.freq_hz, np.array([125.0, 250.0, 375.0])))

    def test_snapshot_import_fails_when_source_file_is_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "missing_after_save.fil"
            snapshot_path.write_bytes(b"snapshot-source")
            session = _synthetic_width_session(path=snapshot_path)
            snapshot = session.to_snapshot()
            snapshot_path.unlink()

            with self.assertRaises(FileNotFoundError):
                BurstSession.from_snapshot(snapshot, loader=_snapshot_loader)

    def test_snapshot_import_fails_on_metadata_mismatch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "mismatch_source.fil"
            snapshot_path.write_bytes(b"snapshot-source")
            session = _synthetic_width_session(path=snapshot_path)
            snapshot = session.to_snapshot()

            def _mismatched_loader(path: str, *, dm: float, **_: object) -> BurstSession:
                loaded = _synthetic_width_session(path=Path(path), applied_dm=dm)
                loaded.metadata = replace(loaded.metadata, tsamp=loaded.metadata.tsamp * 2.0)
                return loaded

            with self.assertRaises(ValueError):
                BurstSession.from_snapshot(snapshot, loader=_mismatched_loader)

    def test_snapshot_import_discards_cached_analyses_from_schema_v1_0(self) -> None:
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "legacy_snapshot_source.fil"
            snapshot_path.write_bytes(b"snapshot-source")
            session = _synthetic_width_session(path=snapshot_path)
            session.set_time_factor(2)
            session.set_freq_factor(2)
            session.compute_properties()
            session.compute_widths()
            session.optimize_dm(center_dm=0.0, half_range=2.0, step=0.5)
            session.spectral_analysis = SpectralAnalysisResult(
                status="ok",
                message=None,
                segment_length_ms=8.0,
                segment_bins=8,
                segment_count=5,
                normalization="none",
                event_window_ms=[102.0, 142.0],
                spectral_extent_mhz=[float(session.freqs[-2]), float(session.freqs[1])],
                tsamp_ms=session.tsamp_ms,
                frequency_resolution_hz=125.0,
                nyquist_hz=500.0,
                freq_hz=np.array([125.0, 250.0, 375.0], dtype=float),
                power=np.array([1.2, 0.8, 0.3], dtype=float),
            )

            legacy_payload = session.snapshot_dict()
            legacy_payload["schema_version"] = "1.0"
            restored = BurstSession.from_snapshot(legacy_payload, loader=_snapshot_loader)

            self.assertEqual(restored.time_factor, session.time_factor)
            self.assertEqual(restored.freq_factor, session.freq_factor)
            self.assertIsNone(restored.results)
            self.assertIsNone(restored.width_analysis)
            self.assertIsNone(restored.dm_optimization)
            self.assertIsNone(restored.spectral_analysis)


if __name__ == "__main__":
    unittest.main()
