from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from math import ceil, floor
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from flits.models import SpectralAnalysisResult, TemporalStructureResult


MIN_EVENT_BINS = 4
MIN_SEGMENT_BINS = 2
MIN_POWER_LAW_BINS = 12
DEFAULT_SIGNIFICANCE_SIGMA = 5.0
FWHM_PER_SIGMA = float(2.0 * np.sqrt(2.0 * np.log(2.0)))


def default_segment_bins(event_bin_count: int) -> int:
    event_bin_count = max(0, int(event_bin_count))
    if event_bin_count <= 0:
        return 0
    preferred = max(4, event_bin_count // 4)
    max_bins = max(1, event_bin_count // 2)
    return int(min(preferred, max_bins))


def quantize_segment_bins(segment_length_ms: float, tsamp_ms: float) -> int:
    return max(1, int(floor((float(segment_length_ms) / float(tsamp_ms)) + 0.5)))


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
    min_structure_ms_primary: float | None = None,
    min_structure_ms_wavelet: float | None = None,
    fitburst_min_component_ms: float | None = None,
    raw_periodogram_freq_hz: np.ndarray | None = None,
    raw_periodogram_power: np.ndarray | None = None,
    matched_filter_scales_ms: np.ndarray | None = None,
    matched_filter_boxcar_sigma: np.ndarray | None = None,
    matched_filter_gaussian_sigma: np.ndarray | None = None,
    matched_filter_threshold_sigma: float | None = None,
    wavelet_scales_ms: np.ndarray | None = None,
    wavelet_sigma: np.ndarray | None = None,
    wavelet_threshold_sigma: float | None = None,
) -> TemporalStructureResult:
    return TemporalStructureResult(
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
        min_structure_ms_primary=min_structure_ms_primary,
        min_structure_ms_wavelet=min_structure_ms_wavelet,
        fitburst_min_component_ms=fitburst_min_component_ms,
        power_law_fit_status="unavailable",
        power_law_fit_message=message,
        raw_periodogram_freq_hz=np.asarray(
            [] if raw_periodogram_freq_hz is None else raw_periodogram_freq_hz, dtype=float
        ),
        raw_periodogram_power=np.asarray([] if raw_periodogram_power is None else raw_periodogram_power, dtype=float),
        averaged_psd_freq_hz=np.array([], dtype=float),
        averaged_psd_power=np.array([], dtype=float),
        matched_filter_scales_ms=np.asarray(
            [] if matched_filter_scales_ms is None else matched_filter_scales_ms, dtype=float
        ),
        matched_filter_boxcar_sigma=np.asarray(
            [] if matched_filter_boxcar_sigma is None else matched_filter_boxcar_sigma, dtype=float
        ),
        matched_filter_gaussian_sigma=np.asarray(
            [] if matched_filter_gaussian_sigma is None else matched_filter_gaussian_sigma, dtype=float
        ),
        matched_filter_threshold_sigma=matched_filter_threshold_sigma,
        wavelet_scales_ms=np.asarray([] if wavelet_scales_ms is None else wavelet_scales_ms, dtype=float),
        wavelet_sigma=np.asarray([] if wavelet_sigma is None else wavelet_sigma, dtype=float),
        wavelet_threshold_sigma=wavelet_threshold_sigma,
    )


def _load_stingray_backend() -> tuple[type[Any] | None, type[Any] | None, str | None]:
    try:
        Lightcurve = import_module("stingray.lightcurve").Lightcurve
        AveragedPowerspectrum = import_module("stingray.powerspectrum").AveragedPowerspectrum
    except Exception as exc:  # pragma: no cover - environment dependent
        return None, None, str(exc)
    return Lightcurve, AveragedPowerspectrum, None


def _raw_periodogram(event_series: np.ndarray, tsamp_ms: float) -> tuple[np.ndarray, np.ndarray, float | None, float | None]:
    series = np.asarray(event_series, dtype=float)
    if series.size < 2 or not np.isfinite(series).any():
        return np.array([], dtype=float), np.array([], dtype=float), None, None
    dt_sec = float(tsamp_ms / 1e3)
    transformed = np.fft.rfft(np.nan_to_num(series, nan=0.0, posinf=0.0, neginf=0.0))
    power = np.abs(transformed) ** 2
    freq_hz = np.fft.rfftfreq(series.size, d=dt_sec)
    if freq_hz.size <= 1:
        return np.array([], dtype=float), np.array([], dtype=float), None, float(0.5 / dt_sec)
    return (
        np.asarray(freq_hz[1:], dtype=float),
        np.asarray(power[1:], dtype=float),
        float(freq_hz[1] - freq_hz[0]),
        float(0.5 / dt_sec),
    )


def _build_scale_ladder(event_bin_count: int) -> np.ndarray:
    max_scale_bins = max(1, int(event_bin_count // 4))
    if max_scale_bins <= 1:
        return np.array([1], dtype=int)
    num_scales = min(24, max_scale_bins)
    bins = np.geomspace(1.0, float(max_scale_bins), num=num_scales)
    return np.unique(np.clip(np.rint(bins).astype(int), 1, max_scale_bins))


def _boxcar_kernel(scale_bins: int) -> np.ndarray:
    kernel = np.ones(max(1, int(scale_bins)), dtype=float)
    kernel /= np.sqrt(np.sum(kernel**2))
    return kernel


def _gaussian_kernel(scale_bins: int) -> np.ndarray:
    sigma_bins = max(float(scale_bins) / FWHM_PER_SIGMA, 0.5)
    radius = max(1, int(ceil(3.0 * sigma_bins)))
    x = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-0.5 * (x / sigma_bins) ** 2)
    kernel /= np.sqrt(np.sum(kernel**2))
    return kernel


def _mexican_hat_kernel(scale_bins: int) -> np.ndarray:
    sigma_bins = max(float(scale_bins) / FWHM_PER_SIGMA, 0.5)
    radius = max(1, int(ceil(5.0 * sigma_bins)))
    x = np.arange(-radius, radius + 1, dtype=float)
    kernel = (1.0 - (x / sigma_bins) ** 2) * np.exp(-0.5 * (x / sigma_bins) ** 2)
    kernel -= float(np.mean(kernel))
    norm_value = np.sqrt(np.sum(kernel**2))
    if norm_value <= 0 or not np.isfinite(norm_value):
        return np.array([1.0], dtype=float)
    return kernel / norm_value


def _familywise_threshold_sigma(trials: int, *, base_sigma: float = DEFAULT_SIGNIFICANCE_SIGMA) -> float:
    one_sided_alpha = float(norm.sf(base_sigma))
    per_test_alpha = max(np.finfo(float).tiny, one_sided_alpha / max(1, int(trials)))
    return float(norm.isf(per_test_alpha))


def _scan_minimum_structure(
    event_profile: np.ndarray,
    *,
    tsamp_ms: float,
    noise_sigma: float,
    base_sigma: float = DEFAULT_SIGNIFICANCE_SIGMA,
) -> dict[str, Any]:
    profile = np.asarray(event_profile, dtype=float)
    if profile.size == 0 or not np.isfinite(noise_sigma) or noise_sigma <= 0:
        return {
            "primary_ms": None,
            "wavelet_ms": None,
            "scales_ms": np.array([], dtype=float),
            "boxcar_sigma": np.array([], dtype=float),
            "gaussian_sigma": np.array([], dtype=float),
            "matched_threshold": None,
            "wavelet_sigma": np.array([], dtype=float),
            "wavelet_threshold": None,
        }

    scales_bins = _build_scale_ladder(profile.size)
    scales_ms = np.asarray(scales_bins, dtype=float) * float(tsamp_ms)
    matched_trials = int(profile.size * scales_bins.size * 2)
    wavelet_trials = int(profile.size * scales_bins.size)
    matched_threshold = _familywise_threshold_sigma(matched_trials, base_sigma=base_sigma)
    wavelet_threshold = _familywise_threshold_sigma(wavelet_trials, base_sigma=base_sigma)

    boxcar_sigma: list[float] = []
    gaussian_sigma: list[float] = []
    wavelet_sigma: list[float] = []

    primary_ms = None
    wavelet_ms = None

    finite_profile = np.where(np.isfinite(profile), profile, 0.0)
    for scale_bins, scale_ms in zip(scales_bins, scales_ms, strict=False):
        boxcar_response = np.convolve(finite_profile, _boxcar_kernel(int(scale_bins)), mode="same")
        gaussian_response = np.convolve(finite_profile, _gaussian_kernel(int(scale_bins)), mode="same")
        wavelet_response = np.convolve(finite_profile, _mexican_hat_kernel(int(scale_bins)), mode="same")

        current_boxcar = float(np.max(boxcar_response) / noise_sigma)
        current_gaussian = float(np.max(gaussian_response) / noise_sigma)
        current_wavelet = float(np.max(np.abs(wavelet_response)) / noise_sigma)

        boxcar_sigma.append(current_boxcar)
        gaussian_sigma.append(current_gaussian)
        wavelet_sigma.append(current_wavelet)

        if primary_ms is None and max(current_boxcar, current_gaussian) >= matched_threshold:
            primary_ms = float(scale_ms)
        if wavelet_ms is None and current_wavelet >= wavelet_threshold:
            wavelet_ms = float(scale_ms)

    return {
        "primary_ms": primary_ms,
        "wavelet_ms": wavelet_ms,
        "scales_ms": scales_ms,
        "boxcar_sigma": np.asarray(boxcar_sigma, dtype=float),
        "gaussian_sigma": np.asarray(gaussian_sigma, dtype=float),
        "matched_threshold": matched_threshold,
        "wavelet_sigma": np.asarray(wavelet_sigma, dtype=float),
        "wavelet_threshold": wavelet_threshold,
    }


def _fit_power_law_model(
    freq_hz: np.ndarray,
    power: np.ndarray,
    segment_count: int,
    *,
    min_valid_bins: int = MIN_POWER_LAW_BINS,
) -> dict[str, Any]:
    valid_mask = (np.asarray(freq_hz, dtype=float) > 0) & (np.asarray(power, dtype=float) > 0)
    valid_count = int(np.count_nonzero(valid_mask))
    if valid_count < min_valid_bins:
        return {
            "fit_status": "underconstrained",
            "fit_message": (
                f"Power-law fit omitted: only {valid_count} positive-power bins are available; "
                f"need at least {min_valid_bins} for a stable 3-parameter fit."
            ),
            "power_law_a": None,
            "power_law_alpha": None,
            "power_law_c": None,
            "power_law_a_err": None,
            "power_law_alpha_err": None,
            "power_law_c_err": None,
        }

    fit_freq = np.asarray(freq_hz[valid_mask], dtype=float)
    fit_power = np.asarray(power[valid_mask], dtype=float)
    fit_f = np.log10(fit_freq)
    fit_p = np.log10(fit_power)
    try:
        popt, _pcov = np.polyfit(fit_f, fit_p, 1, cov=True)
    except Exception as exc:
        return {
            "fit_status": "fit_failed",
            "fit_message": f"Power-law fit failed during initialization: {exc}",
            "power_law_a": None,
            "power_law_alpha": None,
            "power_law_c": None,
            "power_law_a_err": None,
            "power_law_alpha_err": None,
            "power_law_c_err": None,
        }

    alpha_guess = float(-popt[0])
    a_guess = float(10 ** float(popt[1]))
    f_ref = float(np.exp(np.mean(np.log(fit_freq))))

    tail_count = max(3, int(ceil(valid_count / 4.0)))
    c_guess = float(np.median(fit_power[-tail_count:]))
    if not np.isfinite(c_guess) or c_guess <= 0:
        c_guess = max(float(np.min(fit_power)), np.finfo(float).tiny)
    a_ref_guess = max(a_guess * (f_ref ** -alpha_guess), np.finfo(float).tiny)
    multiplier = float(max(1, int(segment_count)))

    def nll(params: np.ndarray) -> float:
        log_a_ref, alpha_param, log_c = [float(value) for value in params]
        log_model = log_a_ref - alpha_param * np.log(fit_freq / f_ref)
        model = np.exp(np.clip(log_model, -100, 100)) + np.exp(np.clip(log_c, -100, 100))
        return float(np.sum(fit_power / model + np.log(model)))

    res = minimize(
        nll,
        np.asarray([np.log(a_ref_guess), alpha_guess, np.log(c_guess)], dtype=float),
        method="BFGS",
    )
    if not bool(getattr(res, "success", False)) or getattr(res, "x", None) is None or not np.all(np.isfinite(res.x)):
        return {
            "fit_status": "fit_failed",
            "fit_message": "Power-law fit did not converge to a stable solution.",
            "power_law_a": None,
            "power_law_alpha": None,
            "power_law_c": None,
            "power_law_a_err": None,
            "power_law_alpha_err": None,
            "power_law_c_err": None,
        }

    cov = np.asarray(getattr(res, "hess_inv", np.empty((0, 0))), dtype=float)
    if cov.shape != (3, 3) or not np.all(np.isfinite(cov)) or np.any(np.diag(cov) < 0):
        return {
            "fit_status": "unstable_covariance",
            "fit_message": "Power-law fit converged, but the covariance estimate was unstable; uncertainties omitted.",
            "power_law_a": None,
            "power_law_alpha": None,
            "power_law_c": None,
            "power_law_a_err": None,
            "power_law_alpha_err": None,
            "power_law_c_err": None,
        }
    cov = cov / multiplier

    log_a_ref_opt, alpha_opt, log_c_opt = [float(value) for value in res.x]
    a_ref_opt = float(np.exp(log_a_ref_opt))
    a_opt = float(a_ref_opt * (f_ref ** alpha_opt))
    c_opt = float(np.exp(log_c_opt))
    grad = np.asarray([a_opt, a_opt * np.log(f_ref)], dtype=float)
    a_err = float(np.sqrt(np.dot(grad, np.dot(cov[:2, :2], grad))))
    alpha_err = float(np.sqrt(np.diag(cov)[1]))
    c_err = float(c_opt * np.sqrt(np.diag(cov)[2]))

    if not all(np.isfinite(value) for value in (a_opt, alpha_opt, c_opt, a_err, alpha_err, c_err)):
        return {
            "fit_status": "unstable_covariance",
            "fit_message": "Power-law fit converged, but the parameter covariance was not finite.",
            "power_law_a": None,
            "power_law_alpha": None,
            "power_law_c": None,
            "power_law_a_err": None,
            "power_law_alpha_err": None,
            "power_law_c_err": None,
        }

    return {
        "fit_status": "ok",
        "fit_message": None,
        "power_law_a": a_opt,
        "power_law_alpha": alpha_opt,
        "power_law_c": c_opt,
        "power_law_a_err": a_err,
        "power_law_alpha_err": alpha_err,
        "power_law_c_err": c_err,
    }


def temporal_to_spectral_result(result: TemporalStructureResult) -> SpectralAnalysisResult:
    return SpectralAnalysisResult(
        status=result.status,
        message=result.power_law_fit_message if result.message is None else result.message,
        segment_length_ms=result.segment_length_ms,
        segment_bins=result.segment_bins,
        segment_count=result.segment_count,
        normalization=result.normalization,
        event_window_ms=list(result.event_window_ms),
        spectral_extent_mhz=list(result.spectral_extent_mhz),
        tsamp_ms=result.tsamp_ms,
        frequency_resolution_hz=result.frequency_resolution_hz,
        nyquist_hz=result.nyquist_hz,
        freq_hz=np.asarray(result.averaged_psd_freq_hz, dtype=float),
        power=np.asarray(result.averaged_psd_power, dtype=float),
        power_law_a=result.power_law_a,
        power_law_alpha=result.power_law_alpha,
        power_law_c=result.power_law_c,
        power_law_a_err=result.power_law_a_err,
        power_law_alpha_err=result.power_law_alpha_err,
        power_law_c_err=result.power_law_c_err,
    )


def run_temporal_structure_analysis(
    *,
    event_series: np.ndarray,
    tsamp_ms: float,
    segment_length_ms: float,
    noise_sigma: float,
    event_window_ms: tuple[float, float],
    spectral_extent_mhz: tuple[float, float],
    fitburst_widths_ms: np.ndarray | None = None,
    backend_loader: Callable[[], tuple[type[Any] | None, type[Any] | None, str | None]] = _load_stingray_backend,
) -> TemporalStructureResult:
    series = np.asarray(event_series, dtype=float)
    tsamp_ms = float(tsamp_ms)
    segment_length_ms = float(segment_length_ms)
    fitburst_min_component_ms = None
    if fitburst_widths_ms is not None:
        finite_widths = np.asarray(fitburst_widths_ms, dtype=float)
        finite_widths = finite_widths[np.isfinite(finite_widths) & (finite_widths > 0)]
        if finite_widths.size:
            fitburst_min_component_ms = float(np.min(finite_widths))

    raw_freq_hz, raw_power, _raw_df, raw_nyquist_hz = _raw_periodogram(series, tsamp_ms)
    structure_scan = _scan_minimum_structure(series, tsamp_ms=tsamp_ms, noise_sigma=float(noise_sigma))

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
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
        )

    if series.size < MIN_EVENT_BINS:
        return _failure(
            "insufficient_time_bins",
            "The selected event window is too short for temporal-structure analysis. Use at least 4 time bins.",
            segment_length_ms=segment_length_ms,
            segment_bins=None,
            segment_count=None,
            event_window_ms=event_window_ms,
            spectral_extent_mhz=spectral_extent_mhz,
            tsamp_ms=tsamp_ms,
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
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
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
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
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
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
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
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
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
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
            min_structure_ms_primary=structure_scan["primary_ms"],
            min_structure_ms_wavelet=structure_scan["wavelet_ms"],
            fitburst_min_component_ms=fitburst_min_component_ms,
            raw_periodogram_freq_hz=raw_freq_hz,
            raw_periodogram_power=raw_power,
            matched_filter_scales_ms=structure_scan["scales_ms"],
            matched_filter_boxcar_sigma=structure_scan["boxcar_sigma"],
            matched_filter_gaussian_sigma=structure_scan["gaussian_sigma"],
            matched_filter_threshold_sigma=structure_scan["matched_threshold"],
            wavelet_scales_ms=structure_scan["scales_ms"],
            wavelet_sigma=structure_scan["wavelet_sigma"],
            wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
        )

    freq_hz = np.asarray(getattr(spectrum, "freq", []), dtype=float)
    power = np.asarray(getattr(spectrum, "power", []), dtype=float)
    resolved_segment_count = int(getattr(spectrum, "m", segment_count) or segment_count)
    df = None
    if freq_hz.size > 1:
        df = float(freq_hz[1] - freq_hz[0])
    elif hasattr(spectrum, "df") and np.isfinite(getattr(spectrum, "df")):
        df = float(getattr(spectrum, "df"))

    power_law = _fit_power_law_model(freq_hz, power, resolved_segment_count)
    combined_message = power_law["fit_message"]

    return TemporalStructureResult(
        status="ok",
        message=combined_message,
        segment_length_ms=effective_segment_length_ms,
        segment_bins=segment_bins,
        segment_count=resolved_segment_count,
        normalization="none",
        event_window_ms=[float(event_window_ms[0]), float(event_window_ms[1])],
        spectral_extent_mhz=[float(spectral_extent_mhz[0]), float(spectral_extent_mhz[1])],
        tsamp_ms=tsamp_ms,
        frequency_resolution_hz=df,
        nyquist_hz=raw_nyquist_hz,
        min_structure_ms_primary=structure_scan["primary_ms"],
        min_structure_ms_wavelet=structure_scan["wavelet_ms"],
        fitburst_min_component_ms=fitburst_min_component_ms,
        power_law_fit_status=str(power_law["fit_status"]),
        power_law_fit_message=power_law["fit_message"],
        raw_periodogram_freq_hz=np.asarray(raw_freq_hz, dtype=float),
        raw_periodogram_power=np.asarray(raw_power, dtype=float),
        averaged_psd_freq_hz=np.asarray(freq_hz, dtype=float),
        averaged_psd_power=np.asarray(power, dtype=float),
        matched_filter_scales_ms=np.asarray(structure_scan["scales_ms"], dtype=float),
        matched_filter_boxcar_sigma=np.asarray(structure_scan["boxcar_sigma"], dtype=float),
        matched_filter_gaussian_sigma=np.asarray(structure_scan["gaussian_sigma"], dtype=float),
        matched_filter_threshold_sigma=structure_scan["matched_threshold"],
        wavelet_scales_ms=np.asarray(structure_scan["scales_ms"], dtype=float),
        wavelet_sigma=np.asarray(structure_scan["wavelet_sigma"], dtype=float),
        wavelet_threshold_sigma=structure_scan["wavelet_threshold"],
        power_law_a=power_law["power_law_a"],
        power_law_alpha=power_law["power_law_alpha"],
        power_law_c=power_law["power_law_c"],
        power_law_a_err=power_law["power_law_a_err"],
        power_law_alpha_err=power_law["power_law_alpha_err"],
        power_law_c_err=power_law["power_law_c_err"],
    )
