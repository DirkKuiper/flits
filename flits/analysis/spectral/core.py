from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any

import numpy as np

from flits.models import SpectralAnalysisResult
from flits.analysis.temporal.core import _fit_power_law_model, quantize_segment_bins


MIN_EVENT_BINS = 4
MIN_SEGMENT_BINS = 2


def default_segment_bins(event_bin_count: int) -> int:
    event_bin_count = max(0, int(event_bin_count))
    if event_bin_count <= 0:
        return 0
    preferred = max(4, event_bin_count // 4)
    max_bins = max(1, event_bin_count // 2)
    return int(min(preferred, max_bins))


def _failure(
    status: str,
    message: str,
    *,
    segment_length_ms: float | None,
    segment_bins: int | None,
    segment_count: int | None,
    event_window_ms: tuple[float, float],
    spectral_extent_mhz: tuple[float, float],
    tsamp_ms: float,
) -> SpectralAnalysisResult:
    return SpectralAnalysisResult(
        status=status,
        message=message,
        segment_length_ms=None if segment_length_ms is None else float(segment_length_ms),
        segment_bins=segment_bins,
        segment_count=segment_count,
        normalization="none",
        event_window_ms=[float(event_window_ms[0]), float(event_window_ms[1])],
        spectral_extent_mhz=[float(spectral_extent_mhz[0]), float(spectral_extent_mhz[1])],
        tsamp_ms=float(tsamp_ms),
        frequency_resolution_hz=None,
        nyquist_hz=None,
        freq_hz=np.array([], dtype=float),
        power=np.array([], dtype=float),
    )


def _load_stingray_backend() -> tuple[type[Any] | None, type[Any] | None, str | None]:
    try:
        Lightcurve = import_module("stingray.lightcurve").Lightcurve
        AveragedPowerspectrum = import_module("stingray.powerspectrum").AveragedPowerspectrum
    except Exception as exc:  # pragma: no cover - environment dependent
        return None, None, str(exc)
    return Lightcurve, AveragedPowerspectrum, None


def run_averaged_spectral_analysis(
    *,
    event_series: np.ndarray,
    tsamp_ms: float,
    segment_length_ms: float,
    event_window_ms: tuple[float, float],
    spectral_extent_mhz: tuple[float, float],
    backend_loader: Callable[[], tuple[type[Any] | None, type[Any] | None, str | None]] = _load_stingray_backend,
) -> SpectralAnalysisResult:
    series = np.asarray(event_series, dtype=float)
    tsamp_ms = float(tsamp_ms)
    segment_length_ms = float(segment_length_ms)

    if series.size == 0 or not np.isfinite(series).any():
        return _failure(
            "insufficient_data",
            "The selected event window does not contain finite selected-band samples.",
            segment_length_ms=segment_length_ms,
            segment_bins=None,
            segment_count=None,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    if series.size < MIN_EVENT_BINS:
        return _failure(
            "insufficient_time_bins",
            "The selected event window is too short for an averaged power spectrum. Use at least 4 time bins.",
            segment_length_ms=segment_length_ms,
            segment_bins=None,
            segment_count=None,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    if not np.isfinite(segment_length_ms) or segment_length_ms <= 0 or not np.isfinite(tsamp_ms) or tsamp_ms <= 0:
        return _failure(
            "invalid_segment_length",
            "Segment length must be a positive finite number.",
            segment_length_ms=segment_length_ms,
            segment_bins=None,
            segment_count=None,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    segment_bins = quantize_segment_bins(segment_length_ms, tsamp_ms)
    effective_segment_length_ms = float(segment_bins * tsamp_ms)

    if segment_bins < MIN_SEGMENT_BINS:
        return _failure(
            "invalid_segment_length",
            "Segment length must span at least 2 time bins.",
            segment_length_ms=effective_segment_length_ms,
            segment_bins=segment_bins,
            segment_count=None,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    segment_count = int(series.size // segment_bins)
    if segment_count < 1:
        return _failure(
            "invalid_segment_length",
            "Segment length must fit at least 1 full segment inside the selected event window.",
            segment_length_ms=effective_segment_length_ms,
            segment_bins=segment_bins,
            segment_count=segment_count,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    Lightcurve, AveragedPowerspectrum, load_error = backend_loader()
    if Lightcurve is None or AveragedPowerspectrum is None:
        return _failure(
            "stingray_unavailable",
            f"Stingray is unavailable in the active Python environment: {load_error}",
            segment_length_ms=effective_segment_length_ms,
            segment_bins=segment_bins,
            segment_count=segment_count,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    dt_sec = float(tsamp_ms / 1e3)
    segment_size_sec = float(segment_bins * dt_sec)
    time_sec = np.arange(series.size, dtype=float) * dt_sec
    counts = np.nan_to_num(series, nan=0.0, posinf=0.0, neginf=0.0)

    try:
        lightcurve = Lightcurve(
            time=time_sec,
            counts=counts,
            dt=dt_sec,
            err_dist="gauss",
            skip_checks=True,
        )
        spectrum = AveragedPowerspectrum(
            lightcurve,
            segment_size=segment_size_sec,
            norm="none",
            silent=True,
            skip_checks=True,
        )
    except Exception as exc:
        return _failure(
            "stingray_failed",
            f"Stingray failed to compute the averaged power spectrum: {exc}",
            segment_length_ms=effective_segment_length_ms,
            segment_bins=segment_bins,
            segment_count=segment_count,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
        )

    freq_hz = np.asarray(getattr(spectrum, "freq", []), dtype=float)
    power = np.asarray(getattr(spectrum, "power", []), dtype=float)
    resolved_segment_count = int(getattr(spectrum, "m", segment_count) or segment_count)
    df = None
    if freq_hz.size > 1:
        df = float(freq_hz[1] - freq_hz[0])
    elif hasattr(spectrum, "df") and np.isfinite(getattr(spectrum, "df")):
        df = float(getattr(spectrum, "df"))

    nyquist_hz = float(0.5 / dt_sec)

    power_law = _fit_power_law_model(freq_hz, power, resolved_segment_count)

    return SpectralAnalysisResult(
        status="ok",
        message=power_law["fit_message"],
        segment_length_ms=effective_segment_length_ms,
        segment_bins=segment_bins,
        segment_count=resolved_segment_count,
        normalization="none",
        event_window_ms=[float(event_window_ms[0]), float(event_window_ms[1])],
        spectral_extent_mhz=[float(spectral_extent_mhz[0]), float(spectral_extent_mhz[1])],
        tsamp_ms=tsamp_ms,
        frequency_resolution_hz=df,
        nyquist_hz=nyquist_hz,
        freq_hz=np.asarray(freq_hz, dtype=float),
        power=np.asarray(power, dtype=float),
        power_law_a=power_law["power_law_a"],
        power_law_alpha=power_law["power_law_alpha"],
        power_law_c=power_law["power_law_c"],
        power_law_a_err=power_law["power_law_a_err"],
        power_law_alpha_err=power_law["power_law_alpha_err"],
        power_law_c_err=power_law["power_law_c_err"],
    )
