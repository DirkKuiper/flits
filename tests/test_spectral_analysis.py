from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from flits.analysis.spectral.core import run_averaged_spectral_analysis
from flits.analysis.temporal.core import _fit_power_law_model, quantize_segment_bins, run_temporal_structure_analysis
from flits.models import FilterbankMetadata, SpectralAnalysisResult, TemporalStructureResult
from flits.session import BurstSession
from flits.settings import ObservationConfig


class FakeLightcurve:
    def __init__(self, time: np.ndarray, counts: np.ndarray, dt: float, **_: object) -> None:
        self.time = np.asarray(time, dtype=float)
        self.counts = np.asarray(counts, dtype=float)
        self.dt = float(dt)


class FakeAveragedPowerspectrum:
    def __init__(self, lightcurve: FakeLightcurve, segment_size: float, **_: object) -> None:
        samples_per_segment = int(round(float(segment_size) / lightcurve.dt))
        if samples_per_segment < 2:
            raise ValueError("segment_size must span at least 2 samples")
        segment_count = lightcurve.counts.size // samples_per_segment
        if segment_count < 2:
            raise ValueError("segment_size must fit at least 2 complete segments")

        trimmed = lightcurve.counts[: segment_count * samples_per_segment].reshape(segment_count, samples_per_segment)
        transformed = np.fft.rfft(trimmed, axis=1)
        power = np.mean(np.abs(transformed) ** 2, axis=0)
        freq = np.fft.rfftfreq(samples_per_segment, d=lightcurve.dt)

        self.freq = freq[1:]
        self.power = power[1:]
        self.m = int(segment_count)
        self.df = float(freq[1] - freq[0]) if freq.size > 1 else np.nan


def _fake_backend_loader() -> tuple[type[FakeLightcurve], type[FakeAveragedPowerspectrum], None]:
    return FakeLightcurve, FakeAveragedPowerspectrum, None


def _synthetic_spectral_session(
    *,
    modulation_hz: float = 62.5,
    event_start: int = 64,
    event_end: int = 192,
) -> BurstSession:
    rng = np.random.default_rng(7)
    freqs = np.linspace(1100.0, 1000.0, 8)
    tsamp = 1e-3
    num_time_bins = 256
    data = rng.normal(0.0, 0.02, size=(freqs.size, num_time_bins))

    event_bins = event_end - event_start
    time_sec = np.arange(event_bins, dtype=float) * tsamp
    modulation = np.sin(2.0 * np.pi * modulation_hz * time_sec)
    channel_weights = np.linspace(0.8, 1.2, freqs.size)
    data[:, event_start:event_end] += channel_weights[:, None] * modulation[None, :]

    metadata = FilterbankMetadata(
        source_path="synthetic_spectral.fil",
        source_name="synthetic_spectral",
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
        dm=0.0,
        preset_key="generic",
        sefd_jy=10.0,
    )
    session = BurstSession(
        config=config,
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=num_time_bins,
        event_start=event_start,
        event_end=event_end,
        spec_ex_lo=0,
        spec_ex_hi=freqs.size - 1,
        channel_mask=np.zeros(freqs.size, dtype=bool),
    )
    session.add_offpulse_ms(0.0, 40.0)
    session.add_offpulse_ms(210.0, 250.0)
    return session


class SpectralAnalysisTest(unittest.TestCase):
    def test_run_averaged_spectral_analysis_detects_injected_modulation(self) -> None:
        session = _synthetic_spectral_session(modulation_hz=62.5)
        _, context = session._build_measurement_context_for_data()
        event_series = np.asarray(
            context.selected_profile_baselined[context.event_rel_start:context.event_rel_end],
            dtype=float,
        )

        result = run_averaged_spectral_analysis(
            event_series=event_series,
            tsamp_ms=session.tsamp_ms,
            segment_length_ms=32.0,
            event_window_ms=(session.bin_to_ms(session.event_start), session.bin_to_ms(session.event_end)),
            spectral_extent_mhz=session._selected_frequency_bounds_mhz(),
            backend_loader=_fake_backend_loader,
        )

        self.assertEqual(result.status, "ok")
        self.assertTrue(np.isfinite(result.freq_hz).all())
        self.assertTrue(np.isfinite(result.power).all())
        self.assertGreater(result.segment_count, 1)
        peak_index = int(np.argmax(result.power))
        self.assertAlmostEqual(result.freq_hz[peak_index], 62.5, delta=result.frequency_resolution_hz or 0.0)

    def test_run_averaged_spectral_analysis_returns_structured_failures(self) -> None:
        short_result = run_averaged_spectral_analysis(
            event_series=np.array([0.1, -0.2, 0.3], dtype=float),
            tsamp_ms=1.0,
            segment_length_ms=2.0,
            event_window_ms=(10.0, 13.0),
            spectral_extent_mhz=(1000.0, 1100.0),
            backend_loader=_fake_backend_loader,
        )
        self.assertEqual(short_result.status, "insufficient_time_bins")
        self.assertIn("too short", short_result.message or "")

        invalid_segment = run_averaged_spectral_analysis(
            event_series=np.linspace(-1.0, 1.0, 24, dtype=float),
            tsamp_ms=1.0,
            segment_length_ms=20.0,
            event_window_ms=(64.0, 88.0),
            spectral_extent_mhz=(1000.0, 1100.0),
            backend_loader=_fake_backend_loader,
        )
        self.assertIn(invalid_segment.status, {"invalid_segment_length", "stingray_failed"})
        self.assertTrue(invalid_segment.message)

    def test_run_averaged_spectral_analysis_omits_underconstrained_power_law_fit(self) -> None:
        event_series = np.linspace(-0.25, 1.0, 16, dtype=float)
        result = run_averaged_spectral_analysis(
            event_series=event_series,
            tsamp_ms=1.0,
            segment_length_ms=8.0,
            event_window_ms=(0.0, 16.0),
            spectral_extent_mhz=(1000.0, 1100.0),
            backend_loader=_fake_backend_loader,
        )

        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.power_law_alpha)
        self.assertIsNone(result.power_law_c)
        self.assertIn("Power-law fit omitted", result.message or "")

    def test_quantize_segment_bins_uses_half_up_rounding(self) -> None:
        self.assertEqual(quantize_segment_bins(20.49, 1.0), 20)
        self.assertEqual(quantize_segment_bins(20.50, 1.0), 21)

    def test_power_law_uncertainty_shrinks_with_segment_count(self) -> None:
        rng = np.random.default_rng(123)
        freq_hz = np.arange(1, 17, dtype=float) * 125.0
        model_power = 2.5e8 * freq_hz ** (-2.6) + 0.2
        low_m = rng.gamma(shape=15.0, scale=model_power / 15.0)
        high_m = rng.gamma(shape=60.0, scale=model_power / 60.0)

        low_fit = _fit_power_law_model(freq_hz, low_m, 15)
        high_fit = _fit_power_law_model(freq_hz, high_m, 60)

        self.assertEqual(low_fit["fit_status"], "ok")
        self.assertEqual(high_fit["fit_status"], "ok")
        self.assertLess(high_fit["power_law_alpha_err"], low_fit["power_law_alpha_err"])

    def test_run_temporal_structure_analysis_reports_minimum_structure_and_psd(self) -> None:
        rng = np.random.default_rng(99)
        time_bins = np.arange(128, dtype=float)
        broad = 0.6 * np.exp(-0.5 * ((time_bins - 60.0) / 8.0) ** 2)
        spike = 1.1 * np.exp(-0.5 * ((time_bins - 61.0) / 1.0) ** 2)
        event_series = broad + spike + rng.normal(0.0, 0.05, size=time_bins.size)

        result = run_temporal_structure_analysis(
            event_series=event_series,
            tsamp_ms=1.0,
            segment_length_ms=16.0,
            noise_sigma=0.05,
            event_window_ms=(0.0, 128.0),
            spectral_extent_mhz=(1000.0, 1100.0),
            backend_loader=_fake_backend_loader,
        )

        self.assertEqual(result.status, "ok")
        self.assertTrue(np.isfinite(result.raw_periodogram_freq_hz).all())
        self.assertTrue(np.isfinite(result.averaged_psd_power).all())
        self.assertIsNotNone(result.min_structure_ms_primary)
        self.assertLessEqual(result.min_structure_ms_primary or 0.0, 4.0)
        self.assertIsNotNone(result.min_structure_ms_wavelet)

    def test_run_temporal_structure_analysis_avoids_false_low_sn_minimum_scale(self) -> None:
        rng = np.random.default_rng(101)
        time_bins = np.arange(128, dtype=float)
        event_series = 0.08 * np.exp(-0.5 * ((time_bins - 64.0) / 10.0) ** 2) + rng.normal(0.0, 0.05, size=time_bins.size)

        result = run_temporal_structure_analysis(
            event_series=event_series,
            tsamp_ms=1.0,
            segment_length_ms=16.0,
            noise_sigma=0.05,
            event_window_ms=(0.0, 128.0),
            spectral_extent_mhz=(1000.0, 1100.0),
            backend_loader=_fake_backend_loader,
        )

        self.assertEqual(result.status, "ok")
        self.assertTrue(
            result.min_structure_ms_primary is None or result.min_structure_ms_primary >= 8.0
        )
        self.assertIsNone(result.min_structure_ms_wavelet)

    @patch("flits.session.run_temporal_structure_analysis")
    def test_session_run_spectral_analysis_uses_reduced_event_slice(self, mock_run: object) -> None:
        session = _synthetic_spectral_session()
        session.set_time_factor(4)
        session.set_freq_factor(2)
        grid, context = session._build_measurement_context_for_data()
        expected_series = np.asarray(
            context.selected_profile_baselined[context.event_rel_start:context.event_rel_end],
            dtype=float,
        )
        mock_run.return_value = TemporalStructureResult(
            status="ok",
            message=None,
            segment_length_ms=32.0,
            segment_bins=32,
            segment_count=4,
            normalization="none",
            event_window_ms=[64.0, 192.0],
            spectral_extent_mhz=[1000.0, 1100.0],
            tsamp_ms=1.0,
            frequency_resolution_hz=31.25,
            nyquist_hz=500.0,
            raw_periodogram_freq_hz=np.array([31.25, 62.5], dtype=float),
            raw_periodogram_power=np.array([0.4, 0.8], dtype=float),
            averaged_psd_freq_hz=np.array([31.25, 62.5], dtype=float),
            averaged_psd_power=np.array([0.8, 1.6], dtype=float),
        )

        session.run_spectral_analysis(32.0)

        kwargs = mock_run.call_args.kwargs
        np.testing.assert_allclose(kwargs["event_series"], expected_series)
        self.assertEqual(kwargs["tsamp_ms"], grid.effective_tsamp_ms)
        self.assertEqual(kwargs["segment_length_ms"], 32.0)
        self.assertEqual(kwargs["noise_sigma"], context.noise_summary.sigma)
        self.assertEqual(kwargs["event_window_ms"], (64.0, 192.0))
        self.assertEqual(kwargs["spectral_extent_mhz"], [1007.1428571428571, 1092.857142857143])
        self.assertEqual(session.spectral_analysis.freq_hz.tolist(), [31.25, 62.5])
        self.assertIsNotNone(session.temporal_structure)

    def test_spectral_results_clear_on_resolution_and_primary_selection_changes_only(self) -> None:
        session = _synthetic_spectral_session()
        result = SpectralAnalysisResult(
            status="ok",
            message=None,
            segment_length_ms=32.0,
            segment_bins=32,
            segment_count=4,
            normalization="none",
            event_window_ms=[64.0, 192.0],
            spectral_extent_mhz=[1000.0, 1100.0],
            tsamp_ms=1.0,
            frequency_resolution_hz=31.25,
            nyquist_hz=500.0,
            freq_hz=np.array([31.25, 62.5], dtype=float),
            power=np.array([0.8, 1.6], dtype=float),
        )
        temporal_result = TemporalStructureResult(
            status="ok",
            message=None,
            segment_length_ms=32.0,
            segment_bins=32,
            segment_count=4,
            normalization="none",
            event_window_ms=[64.0, 192.0],
            spectral_extent_mhz=[1000.0, 1100.0],
            tsamp_ms=1.0,
            frequency_resolution_hz=31.25,
            nyquist_hz=500.0,
            raw_periodogram_freq_hz=np.array([31.25, 62.5], dtype=float),
            raw_periodogram_power=np.array([0.4, 0.8], dtype=float),
            averaged_psd_freq_hz=np.array([31.25, 62.5], dtype=float),
            averaged_psd_power=np.array([0.8, 1.6], dtype=float),
        )

        session.spectral_analysis = result
        session.temporal_structure = temporal_result
        session.set_time_factor(2)
        self.assertIsNone(session.spectral_analysis)
        self.assertIsNone(session.temporal_structure)

        session.spectral_analysis = result
        session.temporal_structure = temporal_result
        session.set_freq_factor(2)
        self.assertIsNone(session.spectral_analysis)
        self.assertIsNone(session.temporal_structure)

        session.spectral_analysis = result
        session.temporal_structure = temporal_result
        session.add_region_ms(80.0, 120.0)
        self.assertIs(session.spectral_analysis, result)
        self.assertIs(session.temporal_structure, temporal_result)
        session.add_peak_ms(96.0)
        self.assertIs(session.spectral_analysis, result)
        self.assertIs(session.temporal_structure, temporal_result)

        session.set_event_ms(72.0, 184.0)
        self.assertIsNone(session.spectral_analysis)
        self.assertIsNone(session.temporal_structure)

        session.spectral_analysis = result
        session.temporal_structure = temporal_result
        session.add_offpulse_ms(40.0, 56.0)
        self.assertIsNone(session.spectral_analysis)
        self.assertIsNone(session.temporal_structure)

        session.spectral_analysis = result
        session.temporal_structure = temporal_result
        session.set_spectral_extent_freq(float(session.freqs[-2]), float(session.freqs[1]))
        self.assertIsNone(session.spectral_analysis)
        self.assertIsNone(session.temporal_structure)

        session.spectral_analysis = result
        session.temporal_structure = temporal_result
        session.mask_channel_freq(float(session.freqs[0]))
        self.assertIsNone(session.spectral_analysis)
        self.assertIsNone(session.temporal_structure)


if __name__ == "__main__":
    unittest.main()
