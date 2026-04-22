from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import warnings

import numpy as np
from astropy import units as u
from scipy.optimize import curve_fit

from flits.models import (
    AcceptedWidthSelection,
    BurstMeasurements,
    GaussianFit1D,
    MeasurementDiagnostics,
    MeasurementProvenance,
    MeasurementUncertainties,
    NoiseEstimateSettings,
    NoiseEstimateSummary,
    UncertaintyDetail,
    WidthResult,
    compatible_scalar_uncertainty,
)
from flits.signal import acf_1d, gaussian_1d, radiometer


LOW_SN_THRESHOLD = 6.0
HEAVILY_MASKED_FRACTION = 0.25
DM_RESIDUAL_MAX_SUBBANDS = 8
DM_RESIDUAL_MIN_CHANNELS = 4
DM_RESIDUAL_MIN_SUBBANDS = 3


def _uncertainty_detail(
    *,
    value: float | None,
    units: str | None,
    classification: str,
    basis: str,
    tooltip: str,
    publishable: bool,
    warning_flags: Sequence[str] = (),
) -> UncertaintyDetail:
    finite_value = None if value is None or not np.isfinite(value) else float(value)
    return UncertaintyDetail(
        value=finite_value,
        units=units,
        classification=classification,
        is_formal_1sigma=(classification == "formal_1sigma"),
        publishable=bool(publishable),
        basis=str(basis),
        tooltip=str(tooltip),
        warning_flags=sorted(set(str(flag) for flag in warning_flags)),
    )


def _noise_uncertainty_publishable(noise_summary: NoiseEstimateSummary) -> bool:
    if noise_summary.basis != "explicit":
        return False
    blocking_flags = {"implicit_offpulse", "insufficient_offpulse_bins", "zero_noise"}
    return not any(flag in blocking_flags for flag in noise_summary.warning_flags)


def _nanmean_profile(values: np.ndarray, axis: int) -> np.ndarray:
    if values.size == 0:
        shape = values.shape[1 - axis] if values.ndim == 2 else 0
        return np.zeros(shape, dtype=float)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(values, axis=axis)


def _implicit_offpulse_bins(time_bins: int, event_rel_start: int, event_rel_end: int) -> np.ndarray:
    if time_bins <= 0:
        return np.array([], dtype=int)

    candidate_bins = np.concatenate(
        [
            np.arange(0, max(0, event_rel_start), dtype=int),
            np.arange(max(event_rel_end, 0), time_bins, dtype=int),
        ]
    )
    if candidate_bins.size:
        return candidate_bins
    return np.arange(time_bins, dtype=int)


def _explicit_offpulse_bins(
    time_bins: int,
    offpulse_regions: Sequence[tuple[int, int]] | None,
) -> tuple[np.ndarray, str]:
    if not offpulse_regions:
        return np.array([], dtype=int), "implicit_event_complement"

    bins: list[int] = []
    for start, end in offpulse_regions:
        lo = max(0, min(int(start), time_bins))
        hi = max(lo, min(int(end), time_bins))
        if hi > lo:
            bins.extend(range(lo, hi))
    if not bins:
        return np.array([], dtype=int), "implicit_event_complement"
    return np.asarray(sorted(set(bins)), dtype=int), "explicit"


def _reference_stats(reference: np.ndarray, estimator: str = "mean_std") -> tuple[float, float]:
    finite_reference = np.asarray(reference, dtype=float)
    finite_reference = finite_reference[np.isfinite(finite_reference)]
    if finite_reference.size == 0:
        return 0.0, 1.0
    estimator = str(estimator)
    if estimator == "mean_std":
        baseline = float(np.nanmean(finite_reference))
        sigma = float(np.nanstd(finite_reference))
    elif estimator == "median_mad":
        baseline = float(np.nanmedian(finite_reference))
        sigma = float(1.4826 * np.nanmedian(np.abs(finite_reference - baseline)))
    else:
        raise ValueError(f"Unsupported noise estimator '{estimator}'.")
    if not np.isfinite(baseline):
        baseline = 0.0
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = 1.0
    return baseline, sigma


def _normalize_from_stats(series: np.ndarray, baseline: float, sigma: float) -> np.ndarray:
    series = np.asarray(series, dtype=float)
    if series.size == 0:
        return np.array([], dtype=float)
    sigma = 1.0 if not np.isfinite(sigma) or sigma <= 0 else float(sigma)
    baseline = 0.0 if not np.isfinite(baseline) else float(baseline)
    return (series - baseline) / sigma


def _noise_summary(
    *,
    reference: np.ndarray,
    basis: str,
    estimator: str,
    offpulse_bins: np.ndarray,
) -> NoiseEstimateSummary:
    baseline, sigma = _reference_stats(reference, estimator=estimator)
    warning_flags: list[str] = []
    if basis != "explicit":
        warning_flags.append("implicit_offpulse")
    if offpulse_bins.size < 4:
        warning_flags.append("insufficient_offpulse_bins")
    if sigma <= 0 or not np.isfinite(sigma):
        warning_flags.append("zero_noise")
        sigma = 1.0
    return NoiseEstimateSummary(
        estimator=estimator,
        basis=basis,
        baseline=float(baseline),
        sigma=float(sigma),
        offpulse_bin_count=int(offpulse_bins.size),
        offpulse_bins=np.asarray(offpulse_bins, dtype=int),
        warning_flags=sorted(set(warning_flags)),
    )


def _offpulse_windows_ms(
    *,
    offpulse_bins: np.ndarray,
    time_axis_ms: np.ndarray,
    tsamp_ms: float,
) -> list[list[float]]:
    bins = np.asarray(offpulse_bins, dtype=int)
    if bins.size == 0 or time_axis_ms.size == 0:
        return []

    windows: list[list[float]] = []
    start = int(bins[0])
    end = int(bins[0])
    for current in bins[1:]:
        current = int(current)
        if current == end + 1:
            end = current
            continue
        windows.append([float(time_axis_ms[start]), float(time_axis_ms[end] + tsamp_ms)])
        start = current
        end = current
    windows.append([float(time_axis_ms[start]), float(time_axis_ms[end] + tsamp_ms)])
    return windows


def _half_max_crossing(lags: np.ndarray, corr: np.ndarray) -> float | None:
    if lags.size < 2 or corr.size < 2:
        return None

    crossing = np.flatnonzero(corr[1:] <= 0.5)
    if crossing.size == 0:
        return None

    idx = int(crossing[0] + 1)
    x0 = float(lags[idx - 1])
    x1 = float(lags[idx])
    y0 = float(corr[idx - 1])
    y1 = float(corr[idx])
    if not np.isfinite(y0) or not np.isfinite(y1):
        return None
    if y1 == y0:
        return x1
    return float(np.interp(0.5, [y1, y0], [x1, x0]))


def _acf_width(series: np.ndarray, spacing: float) -> tuple[float | None, np.ndarray, np.ndarray]:
    finite = np.asarray(series, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size < 2:
        return None, np.array([], dtype=float), np.array([], dtype=float)

    positive = np.clip(finite - float(np.nanmin(finite)), a_min=0.0, a_max=None)
    if not np.any(positive > 0):
        return None, np.array([], dtype=float), np.array([], dtype=float)

    corr = acf_1d(positive)
    center = corr.size // 2
    zero_lag = float(corr[center]) if corr.size else 0.0
    if not np.isfinite(zero_lag) or zero_lag <= 0:
        return None, np.array([], dtype=float), np.array([], dtype=float)

    positive_corr = np.asarray(corr[center:], dtype=float) / zero_lag
    lags = np.arange(positive_corr.size, dtype=float) * float(spacing)
    lag_half = _half_max_crossing(lags, positive_corr)
    if lag_half is None:
        return None, lags, positive_corr

    return float(np.sqrt(2.0) * lag_half), lags, positive_corr


def _fit_gaussian_regions(
    time_axis_ms: np.ndarray,
    profile_sn: np.ndarray,
    burst_regions_abs: Sequence[tuple[int, int]],
    crop_start_bin: int,
) -> list[GaussianFit1D]:
    gaussian_fits: list[GaussianFit1D] = []
    for start_abs, end_abs in burst_regions_abs:
        fit_rel_start = max(0, int(start_abs) - int(crop_start_bin))
        fit_rel_end = min(profile_sn.size, int(end_abs) - int(crop_start_bin))
        if fit_rel_end - fit_rel_start < 4:
            continue

        xdata = np.asarray(time_axis_ms[fit_rel_start:fit_rel_end], dtype=float)
        ydata = np.asarray(profile_sn[fit_rel_start:fit_rel_end], dtype=float)
        if not np.isfinite(ydata).any():
            continue

        initial_guess = (
            float(np.nanmax(ydata)),
            float(xdata[int(np.nanargmax(ydata))]),
            float(max(np.diff(xdata).min(initial=1.0), 1e-9)),
            0.0,
        )
        bounds = ([0.0, xdata[0], 0.0, -10.0], [np.inf, xdata[-1], xdata[-1] - xdata[0], 10.0])
        try:
            popt, _ = curve_fit(
                gaussian_1d,
                xdata,
                ydata,
                p0=initial_guess,
                bounds=bounds,
                maxfev=10000,
            )
        except Exception:
            continue

        gaussian_fits.append(
            GaussianFit1D(
                amp=float(popt[0]),
                mu_ms=float(popt[1]),
                sigma_ms=float(popt[2]),
                offset=float(popt[3]),
            )
        )
    return gaussian_fits


def _primary_peak_bin(
    peak_bins_abs: Sequence[int],
    profile_sn: np.ndarray,
    crop_start_bin: int,
    event_rel_start: int,
    event_rel_end: int,
) -> int | None:
    candidates = [int(bin_abs) for bin_abs in peak_bins_abs if crop_start_bin <= int(bin_abs) < crop_start_bin + profile_sn.size]
    if candidates:
        return max(
            candidates,
            key=lambda bin_abs: float(profile_sn[int(bin_abs) - int(crop_start_bin)])
            if 0 <= int(bin_abs) - int(crop_start_bin) < profile_sn.size
            else float("-inf"),
        )

    event_slice = np.asarray(profile_sn[event_rel_start:event_rel_end], dtype=float)
    if event_slice.size and np.isfinite(event_slice).any():
        return int(crop_start_bin + event_rel_start + int(np.nanargmax(event_slice)))

    finite_profile = np.asarray(profile_sn, dtype=float)
    if finite_profile.size and np.isfinite(finite_profile).any():
        return int(crop_start_bin + int(np.nanargmax(finite_profile)))
    return None


@dataclass(frozen=True)
class MeasurementContext:
    masked: np.ndarray
    time_axis_ms: np.ndarray
    freqs_mhz: np.ndarray
    selected_profile_raw: np.ndarray
    selected_profile_baselined: np.ndarray
    selected_profile_sn: np.ndarray
    event_profile_raw: np.ndarray
    event_profile_baselined: np.ndarray
    event_profile_sn: np.ndarray
    time_profile_sn: np.ndarray
    spectrum_raw: np.ndarray
    spectrum_sn: np.ndarray
    spectral_axis_mhz: np.ndarray
    offpulse_bins: np.ndarray
    offpulse_regions_rel: list[tuple[int, int]]
    noise_summary: NoiseEstimateSummary
    event_rel_start: int
    event_rel_end: int
    spec_lo: int
    spec_hi: int
    selected_channel_count: int
    active_channel_count: int
    selected_bandwidth_mhz: float
    effective_bandwidth_mhz: float


@dataclass(frozen=True)
class SubbandResidualDiagnostics:
    center_freqs_mhz: np.ndarray
    arrival_times_ms: np.ndarray
    residuals_ms: np.ndarray
    status: str


def build_measurement_context(
    *,
    masked: np.ndarray,
    time_axis_ms: np.ndarray,
    freqs_mhz: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    spec_lo: int,
    spec_hi: int,
    freqres_mhz: float,
    offpulse_regions: Sequence[tuple[int, int]] | None = None,
    noise_settings: NoiseEstimateSettings | None = None,
) -> MeasurementContext:
    masked = np.asarray(masked, dtype=float)
    time_axis_ms = np.asarray(time_axis_ms, dtype=float)
    freqs_mhz = np.asarray(freqs_mhz, dtype=float)
    noise_settings = NoiseEstimateSettings() if noise_settings is None else noise_settings

    spec_lo, spec_hi = sorted((int(spec_lo), int(spec_hi)))
    selected = masked[spec_lo : spec_hi + 1, :]
    selected_channel_count = int(max(0, spec_hi - spec_lo + 1))
    active_channel_count = int(np.isfinite(selected).any(axis=1).sum()) if selected.size else 0

    explicit_offpulse, basis = _explicit_offpulse_bins(masked.shape[1], offpulse_regions)
    offpulse_bins = explicit_offpulse
    if offpulse_bins.size == 0:
        offpulse_bins = _implicit_offpulse_bins(masked.shape[1], event_rel_start, event_rel_end)
        basis = "implicit_event_complement"

    full_profile_raw = _nanmean_profile(masked, axis=0)
    selected_profile_raw = _nanmean_profile(selected, axis=0)
    selected_offpulse = selected_profile_raw[offpulse_bins] if offpulse_bins.size else selected_profile_raw
    full_offpulse = full_profile_raw[offpulse_bins] if offpulse_bins.size else full_profile_raw
    noise_summary = _noise_summary(
        reference=selected_offpulse,
        basis=basis,
        estimator=noise_settings.estimator,
        offpulse_bins=offpulse_bins,
    )

    event_spectrum_raw = _nanmean_profile(selected[:, event_rel_start:event_rel_end], axis=1) if selected.size else np.array([], dtype=float)
    offpulse_spectrum_raw = _nanmean_profile(selected[:, offpulse_bins], axis=1) if selected.size and offpulse_bins.size else np.array([], dtype=float)
    spectrum_baseline, spectrum_sigma = _reference_stats(offpulse_spectrum_raw, estimator=noise_settings.estimator)
    full_baseline, full_sigma = _reference_stats(full_offpulse, estimator=noise_settings.estimator)

    return MeasurementContext(
        masked=masked,
        time_axis_ms=time_axis_ms,
        freqs_mhz=freqs_mhz,
        selected_profile_raw=np.asarray(selected_profile_raw, dtype=float),
        selected_profile_baselined=np.asarray(selected_profile_raw, dtype=float) - float(noise_summary.baseline),
        selected_profile_sn=_normalize_from_stats(
            np.asarray(selected_profile_raw, dtype=float),
            float(noise_summary.baseline),
            float(noise_summary.sigma),
        ),
        event_profile_raw=np.asarray(selected_profile_raw[event_rel_start:event_rel_end], dtype=float),
        event_profile_baselined=(
            np.asarray(selected_profile_raw[event_rel_start:event_rel_end], dtype=float) - float(noise_summary.baseline)
        ),
        event_profile_sn=_normalize_from_stats(
            np.asarray(selected_profile_raw[event_rel_start:event_rel_end], dtype=float),
            float(noise_summary.baseline),
            float(noise_summary.sigma),
        ),
        time_profile_sn=_normalize_from_stats(
            np.asarray(full_profile_raw, dtype=float),
            float(full_baseline),
            float(full_sigma),
        ),
        spectrum_raw=np.asarray(event_spectrum_raw, dtype=float),
        spectrum_sn=_normalize_from_stats(
            np.asarray(event_spectrum_raw, dtype=float),
            float(spectrum_baseline),
            float(spectrum_sigma),
        ),
        spectral_axis_mhz=np.asarray(freqs_mhz[spec_lo : spec_hi + 1], dtype=float),
        offpulse_bins=offpulse_bins,
        offpulse_regions_rel=[(int(start), int(end)) for start, end in (offpulse_regions or [])],
        noise_summary=noise_summary,
        event_rel_start=int(event_rel_start),
        event_rel_end=int(event_rel_end),
        spec_lo=spec_lo,
        spec_hi=spec_hi,
        selected_channel_count=selected_channel_count,
        active_channel_count=active_channel_count,
        selected_bandwidth_mhz=float(selected_channel_count * abs(float(freqres_mhz))),
        effective_bandwidth_mhz=float(active_channel_count * abs(float(freqres_mhz))),
    )


def _subband_windows(
    masked: np.ndarray,
    spec_lo: int,
    spec_hi: int,
) -> tuple[list[tuple[int, int]], str]:
    selected = np.asarray(masked[spec_lo : spec_hi + 1, :], dtype=float)
    if selected.size == 0:
        return [], "insufficient_active_channels"

    active_channels = np.isfinite(selected).any(axis=1)
    active_count = int(active_channels.sum())
    min_active = DM_RESIDUAL_MIN_CHANNELS * DM_RESIDUAL_MIN_SUBBANDS
    if active_count < min_active:
        return [], "insufficient_active_channels"

    max_subbands = min(DM_RESIDUAL_MAX_SUBBANDS, active_count // DM_RESIDUAL_MIN_CHANNELS)
    if max_subbands < DM_RESIDUAL_MIN_SUBBANDS:
        return [], "insufficient_subbands"

    channel_indices = np.arange(spec_lo, spec_hi + 1, dtype=int)
    for num_subbands in range(max_subbands, DM_RESIDUAL_MIN_SUBBANDS - 1, -1):
        windows: list[tuple[int, int]] = []
        usable = True
        for chunk in np.array_split(channel_indices, num_subbands):
            if chunk.size == 0:
                usable = False
                break
            start = int(chunk[0])
            end = int(chunk[-1])
            active_in_window = int(active_channels[start - spec_lo : end - spec_lo + 1].sum())
            if active_in_window < DM_RESIDUAL_MIN_CHANNELS:
                usable = False
                break
            windows.append((start, end))
        if usable:
            return windows, "ok"

    return [], "heavily_masked_subbands"


def _arrival_time_ms(context: MeasurementContext) -> float | None:
    event_times = np.asarray(
        context.time_axis_ms[context.event_rel_start : context.event_rel_end],
        dtype=float,
    )
    event_profile = np.asarray(
        context.selected_profile_sn[context.event_rel_start : context.event_rel_end],
        dtype=float,
    )
    if event_times.size == 0 or event_profile.size == 0 or not np.isfinite(event_profile).any():
        return None

    weights = np.clip(np.where(np.isfinite(event_profile), event_profile, 0.0), a_min=0.0, a_max=None)
    if np.isfinite(weights).any() and float(np.nansum(weights)) > 0:
        return float(np.average(event_times, weights=weights))

    try:
        peak_index = int(np.nanargmax(event_profile))
    except ValueError:
        return None
    return float(event_times[peak_index])


def compute_subband_arrival_residuals(
    *,
    masked: np.ndarray,
    time_axis_ms: np.ndarray,
    freqs_mhz: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    spec_lo: int,
    spec_hi: int,
    freqres_mhz: float,
    offpulse_regions: Sequence[tuple[int, int]] | None = None,
    noise_settings: NoiseEstimateSettings | None = None,
) -> SubbandResidualDiagnostics:
    windows, status = _subband_windows(masked, spec_lo, spec_hi)
    if not windows:
        return SubbandResidualDiagnostics(
            center_freqs_mhz=np.array([], dtype=float),
            arrival_times_ms=np.array([], dtype=float),
            residuals_ms=np.array([], dtype=float),
            status=status,
        )

    center_freqs: list[float] = []
    arrival_times: list[float] = []
    for start, end in windows:
        context = build_measurement_context(
            masked=masked,
            time_axis_ms=time_axis_ms,
            freqs_mhz=freqs_mhz,
            event_rel_start=event_rel_start,
            event_rel_end=event_rel_end,
            spec_lo=start,
            spec_hi=end,
            freqres_mhz=freqres_mhz,
            offpulse_regions=offpulse_regions,
            noise_settings=noise_settings,
        )
        arrival_time = _arrival_time_ms(context)
        if arrival_time is None:
            return SubbandResidualDiagnostics(
                center_freqs_mhz=np.array([], dtype=float),
                arrival_times_ms=np.array([], dtype=float),
                residuals_ms=np.array([], dtype=float),
                status="insufficient_signal",
            )

        window = np.asarray(masked[start : end + 1, :], dtype=float)
        freqs_window = np.asarray(freqs_mhz[start : end + 1], dtype=float)
        active = np.isfinite(window).any(axis=1)
        center_freq = float(np.nanmean(freqs_window[active])) if np.any(active) else float(np.nanmean(freqs_window))
        if not np.isfinite(center_freq):
            return SubbandResidualDiagnostics(
                center_freqs_mhz=np.array([], dtype=float),
                arrival_times_ms=np.array([], dtype=float),
                residuals_ms=np.array([], dtype=float),
                status="insufficient_signal",
            )

        center_freqs.append(center_freq)
        arrival_times.append(arrival_time)

    if len(arrival_times) < DM_RESIDUAL_MIN_SUBBANDS:
        return SubbandResidualDiagnostics(
            center_freqs_mhz=np.array([], dtype=float),
            arrival_times_ms=np.array([], dtype=float),
            residuals_ms=np.array([], dtype=float),
            status="insufficient_subbands",
        )

    freqs = np.asarray(center_freqs, dtype=float)
    arrivals = np.asarray(arrival_times, dtype=float)
    order = np.argsort(freqs)
    freqs = freqs[order]
    arrivals = arrivals[order]
    residuals = arrivals - float(np.mean(arrivals))
    return SubbandResidualDiagnostics(
        center_freqs_mhz=freqs,
        arrival_times_ms=arrivals,
        residuals_ms=residuals,
        status="ok",
    )


def event_snr(selected_profile_sn: np.ndarray, event_rel_start: int, event_rel_end: int) -> float:
    event = np.asarray(selected_profile_sn[event_rel_start:event_rel_end], dtype=float)
    finite = event[np.isfinite(event)]
    if finite.size == 0:
        return float("-inf")
    return float(np.nansum(finite) / np.sqrt(finite.size))


def compute_burst_measurements(
    *,
    burst_name: str,
    dm: float,
    start_mjd: float,
    read_start_sec: float,
    crop_start_bin: int,
    tsamp_ms: float,
    freqres_mhz: float,
    freqs_mhz: np.ndarray,
    masked: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    spec_lo: int,
    spec_hi: int,
    peak_bins_abs: Sequence[int],
    burst_regions_abs: Sequence[tuple[int, int]],
    manual_selection: bool,
    manual_peak_selection: bool,
    sefd_jy: float | None,
    npol: int,
    distance_mpc: float | None,
    redshift: float | None,
    sefd_fractional_uncertainty: float | None,
    distance_fractional_uncertainty: float | None,
    masked_channels: Sequence[int],
    offpulse_regions_rel: Sequence[tuple[int, int]] | None = None,
    noise_settings: NoiseEstimateSettings | None = None,
    width_results: Sequence[WidthResult] | None = None,
    accepted_width: AcceptedWidthSelection | None = None,
    time_axis_ms: np.ndarray | None = None,
) -> BurstMeasurements:
    if time_axis_ms is None:
        time_axis_ms = (
            (int(crop_start_bin) + np.arange(masked.shape[1], dtype=float)) * float(tsamp_ms)
            + float(read_start_sec) * 1000.0
        )
    else:
        time_axis_ms = np.asarray(time_axis_ms, dtype=float)
    context = build_measurement_context(
        masked=np.asarray(masked, dtype=float),
        time_axis_ms=time_axis_ms,
        freqs_mhz=np.asarray(freqs_mhz, dtype=float),
        event_rel_start=event_rel_start,
        event_rel_end=event_rel_end,
        spec_lo=spec_lo,
        spec_hi=spec_hi,
        freqres_mhz=freqres_mhz,
        offpulse_regions=offpulse_regions_rel,
        noise_settings=noise_settings,
    )

    peak_bin_abs = _primary_peak_bin(
        peak_bins_abs,
        context.selected_profile_sn,
        crop_start_bin,
        event_rel_start,
        event_rel_end,
    )
    peak_positions_ms = [
        float(time_axis_ms[int(peak_bin) - int(crop_start_bin)])
        for peak_bin in peak_bins_abs
        if 0 <= int(peak_bin) - int(crop_start_bin) < time_axis_ms.size
    ]
    if not peak_positions_ms and peak_bin_abs is not None and 0 <= int(peak_bin_abs) - int(crop_start_bin) < time_axis_ms.size:
        peak_positions_ms = [float(time_axis_ms[int(peak_bin_abs) - int(crop_start_bin)])]

    toa_topo_mjd = None
    if peak_bin_abs is not None and 0 <= int(peak_bin_abs) - int(crop_start_bin) < time_axis_ms.size:
        peak_time_ms = float(time_axis_ms[int(peak_bin_abs) - int(crop_start_bin)])
        toa_topo_mjd = float(start_mjd + ((peak_time_ms / 1e3) / 86400.0))

    snr_peak = None
    if context.event_profile_sn.size and np.isfinite(context.event_profile_sn).any():
        snr_peak = float(np.nanmax(context.event_profile_sn))
    snr_integrated = event_snr(context.selected_profile_sn, event_rel_start, event_rel_end)
    if not np.isfinite(snr_integrated):
        snr_integrated = None

    width_ms_acf, temporal_lags_ms, temporal_acf = _acf_width(context.event_profile_sn, tsamp_ms)
    spectral_width_mhz_acf, spectral_lags_mhz, spectral_acf = _acf_width(context.spectrum_sn, abs(freqres_mhz))

    flux_scale = None
    peak_flux_jy = None
    fluence_jyms = None
    fluence_uncertainty = None
    if sefd_jy is not None and context.effective_bandwidth_mhz > 0:
        flux_scale = radiometer(tsamp_ms, context.effective_bandwidth_mhz, npol, sefd_jy)
        if snr_peak is not None:
            peak_flux_jy = float(snr_peak * flux_scale)
        finite_event = context.event_profile_sn[np.isfinite(context.event_profile_sn)]
        fluence_jyms = float(np.nansum(finite_event) * tsamp_ms * flux_scale) if finite_event.size else 0.0
        fluence_uncertainty = float(np.sqrt(finite_event.size) * tsamp_ms * flux_scale) if finite_event.size else 0.0

    iso_e = None
    if fluence_jyms is not None and distance_mpc is not None and redshift is not None:
        iso_e = (
            4
            * np.pi
            * fluence_jyms
            * u.Jy
            * u.ms
            * context.effective_bandwidth_mhz
            * u.MHz
            * (distance_mpc * u.megaparsec) ** 2
            / (1 + redshift)
        ).to_value(u.erg)

    gaussian_fits = _fit_gaussian_regions(time_axis_ms, context.selected_profile_sn, burst_regions_abs, crop_start_bin)
    masked_fraction = 1.0
    if context.selected_channel_count > 0:
        masked_fraction = 1.0 - (context.active_channel_count / context.selected_channel_count)

    measurement_flags: list[str] = list(context.noise_summary.warning_flags)
    if manual_selection:
        measurement_flags.append("manual")
    if width_ms_acf is not None or spectral_width_mhz_acf is not None:
        measurement_flags.append("acf")
    if gaussian_fits:
        measurement_flags.append("fit")
    if peak_flux_jy is not None or fluence_jyms is not None:
        measurement_flags.append("calibrated")
    if snr_peak is None or snr_peak < LOW_SN_THRESHOLD:
        measurement_flags.append("low_sn")
    if masked_fraction >= HEAVILY_MASKED_FRACTION:
        measurement_flags.append("heavily_masked")
    if event_rel_start <= 0 or event_rel_end >= context.selected_profile_sn.size:
        measurement_flags.append("edge_clipped")
    if distance_mpc is None or redshift is None:
        measurement_flags.append("missing_distance")
    if sefd_jy is None or context.effective_bandwidth_mhz <= 0:
        measurement_flags.append("missing_sefd")

    uncertainty_details: dict[str, UncertaintyDetail] = {}
    noise_publishable = _noise_uncertainty_publishable(context.noise_summary)
    resolution_warning_flags = list(context.noise_summary.warning_flags)

    if toa_topo_mjd is not None:
        uncertainty_details["toa_topo_mjd"] = _uncertainty_detail(
            value=float((tsamp_ms / 1e3) / (2 * 86400.0)),
            units="MJD",
            classification="resolution_limit",
            basis="Half of the effective time bin after reduction; TOA is anchored to the selected peak bin rather than a fitted centroid model.",
            tooltip="Resolution limit from half of the effective time bin. This is a discretization floor, not a formal statistical 1σ error bar.",
            publishable=False,
            warning_flags=resolution_warning_flags,
        )
    if width_ms_acf is not None:
        uncertainty_details["width_ms_acf"] = _uncertainty_detail(
            value=float(tsamp_ms * 0.5),
            units="ms",
            classification="resolution_limit",
            basis="Half of the effective time sample used to evaluate the temporal ACF crossing.",
            tooltip="Resolution limit from half the effective time sample. The temporal ACF width itself is retained, but this is not treated as a formal 1σ uncertainty.",
            publishable=False,
            warning_flags=resolution_warning_flags,
        )
    if spectral_width_mhz_acf is not None:
        uncertainty_details["spectral_width_mhz_acf"] = _uncertainty_detail(
            value=float(abs(freqres_mhz) * 0.5),
            units="MHz",
            classification="resolution_limit",
            basis="Half of the effective channel width used to evaluate the spectral ACF crossing.",
            tooltip="Resolution limit from half the effective channel width. The spectral ACF width is retained as a diagnostic scale, not a formal 1σ uncertainty.",
            publishable=False,
            warning_flags=resolution_warning_flags,
        )

    flux_like_warning_flags = list(context.noise_summary.warning_flags)
    if "low_sn" in measurement_flags:
        flux_like_warning_flags.append("low_sn")
    sefd_fractional_uncertainty = (
        None if sefd_fractional_uncertainty is None else max(0.0, float(sefd_fractional_uncertainty))
    )
    distance_fractional_uncertainty = (
        None if distance_fractional_uncertainty is None else max(0.0, float(distance_fractional_uncertainty))
    )
    if peak_flux_jy is not None and flux_scale is not None:
        peak_flux_stat = float(flux_scale)
        peak_flux_value = None
        peak_flux_classification = "statistical_only"
        peak_flux_basis = "Radiometer-noise statistical term from one off-pulse sigma in the selected band."
        peak_flux_publishable = False
        if sefd_fractional_uncertainty is not None:
            peak_flux_value = float(
                np.sqrt(peak_flux_stat**2 + (abs(peak_flux_jy) * sefd_fractional_uncertainty) ** 2)
            )
            peak_flux_classification = "formal_1sigma"
            peak_flux_basis = (
                "Quadrature combination of the radiometer-noise statistical term and the supplied SEFD fractional uncertainty."
            )
            peak_flux_publishable = noise_publishable
        else:
            peak_flux_value = peak_flux_stat
            flux_like_warning_flags.append("missing_sefd_fractional_uncertainty")
        uncertainty_details["peak_flux_jy"] = _uncertainty_detail(
            value=peak_flux_value,
            units="Jy",
            classification=peak_flux_classification,
            basis=peak_flux_basis,
            tooltip=(
                "Peak-flux uncertainty combines radiometer-noise statistics with any supplied SEFD fractional systematic. "
                "Without an SEFD uncertainty input, this remains statistical-only and non-publishable."
            ),
            publishable=peak_flux_publishable,
            warning_flags=flux_like_warning_flags,
        )
    if fluence_jyms is not None and fluence_uncertainty is not None:
        fluence_value = None
        fluence_classification = "statistical_only"
        fluence_basis = "Radiometer-noise statistical term from the event-window sum over off-pulse-normalized bins."
        fluence_publishable = False
        if sefd_fractional_uncertainty is not None:
            fluence_value = float(
                np.sqrt(fluence_uncertainty**2 + (abs(fluence_jyms) * sefd_fractional_uncertainty) ** 2)
            )
            fluence_classification = "formal_1sigma"
            fluence_basis = (
                "Quadrature combination of the radiometer-noise statistical term and the supplied SEFD fractional uncertainty."
            )
            fluence_publishable = noise_publishable
        else:
            fluence_value = float(fluence_uncertainty)
        uncertainty_details["fluence_jyms"] = _uncertainty_detail(
            value=fluence_value,
            units="Jy ms",
            classification=fluence_classification,
            basis=fluence_basis,
            tooltip=(
                "Fluence uncertainty combines radiometer-noise statistics with any supplied SEFD fractional systematic. "
                "Without an SEFD uncertainty input, this remains statistical-only and non-publishable."
            ),
            publishable=fluence_publishable,
            warning_flags=flux_like_warning_flags,
        )
    if iso_e is not None and fluence_jyms not in (None, 0.0):
        iso_stat = 0.0
        if "fluence_jyms" in uncertainty_details and uncertainty_details["fluence_jyms"].value is not None:
            iso_stat = float(uncertainty_details["fluence_jyms"].value) / abs(float(fluence_jyms))
        iso_value = None
        iso_classification = "statistical_only"
        iso_basis = "Propagated from the fluence statistical term only."
        iso_publishable = False
        if sefd_fractional_uncertainty is not None and distance_fractional_uncertainty is not None:
            iso_fractional = float(
                np.sqrt(iso_stat**2 + (2.0 * distance_fractional_uncertainty) ** 2)
            )
            iso_value = float(abs(iso_e) * iso_fractional)
            iso_classification = "formal_1sigma"
            iso_basis = (
                "Quadrature combination of the propagated fluence term and the supplied luminosity-distance fractional uncertainty."
            )
            iso_publishable = noise_publishable
        else:
            iso_value = float(abs(iso_e) * iso_stat) if iso_stat > 0 else None
            if sefd_fractional_uncertainty is None:
                flux_like_warning_flags.append("missing_sefd_fractional_uncertainty")
            if distance_fractional_uncertainty is None:
                flux_like_warning_flags.append("missing_distance_fractional_uncertainty")
        uncertainty_details["iso_e"] = _uncertainty_detail(
            value=iso_value,
            units="erg",
            classification=iso_classification,
            basis=iso_basis,
            tooltip=(
                "Isotropic-energy uncertainty is only treated as publishable after both calibration (SEFD) and distance systematics are supplied. "
                "Otherwise it is statistical-only."
            ),
            publishable=iso_publishable,
            warning_flags=flux_like_warning_flags,
        )

    uncertainties = MeasurementUncertainties(
        toa_topo_mjd=compatible_scalar_uncertainty(uncertainty_details.get("toa_topo_mjd")),
        snr_peak=None,
        snr_integrated=None,
        width_ms_acf=compatible_scalar_uncertainty(uncertainty_details.get("width_ms_acf")),
        width_ms_model=None,
        spectral_width_mhz_acf=compatible_scalar_uncertainty(uncertainty_details.get("spectral_width_mhz_acf")),
        tau_sc_ms=None,
        peak_flux_jy=compatible_scalar_uncertainty(uncertainty_details.get("peak_flux_jy")),
        fluence_jyms=compatible_scalar_uncertainty(uncertainty_details.get("fluence_jyms")),
        iso_e=compatible_scalar_uncertainty(uncertainty_details.get("iso_e")),
    )

    offpulse_windows_ms = _offpulse_windows_ms(
        offpulse_bins=context.offpulse_bins,
        time_axis_ms=context.time_axis_ms,
        tsamp_ms=tsamp_ms,
    )
    calibration_assumptions = []
    if flux_scale is not None:
        calibration_assumptions.append("Radiometer-noise limited SEFD calibration.")
    if distance_mpc is not None and redshift is not None and iso_e is not None:
        calibration_assumptions.append("Isotropic energy assumes bandwidth-limited emission.")

    provenance = MeasurementProvenance(
        manual_selection=bool(manual_selection),
        peak_selection="manual" if manual_peak_selection else "automatic",
        width_method="acf_half_max",
        spectral_width_method="acf_half_max",
        calibration_method="radiometer_equation" if flux_scale is not None else "uncalibrated",
        energy_unit="erg" if iso_e is not None else None,
        uncertainty_basis=(
            "Measurement uncertainty details classify each value as formal, model-based, statistical-only, heuristic, or resolution-limited. "
            "Formal flux-like uncertainties require explicit off-pulse noise plus supplied calibration and distance systematics where applicable."
        ),
        event_window_ms=[
            float(time_axis_ms[max(0, min(event_rel_start, time_axis_ms.size - 1))]) if time_axis_ms.size else 0.0,
            float(time_axis_ms[max(0, min(event_rel_end - 1, time_axis_ms.size - 1))] + tsamp_ms) if time_axis_ms.size else 0.0,
        ],
        spectral_extent_mhz=[
            float(np.min(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
            float(np.max(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
        ],
        offpulse_windows_ms=offpulse_windows_ms,
        offpulse_bin_count=int(context.offpulse_bins.size),
        burst_bin_count=int(max(0, event_rel_end - event_rel_start)),
        selected_channel_count=context.selected_channel_count,
        active_channel_count=context.active_channel_count,
        selected_bandwidth_mhz=context.selected_bandwidth_mhz,
        effective_bandwidth_mhz=context.effective_bandwidth_mhz,
        masked_fraction=float(max(0.0, masked_fraction)),
        masked_channels=[int(channel) for channel in masked_channels],
        tsamp_ms=float(tsamp_ms),
        freqres_mhz=float(abs(freqres_mhz)),
        npol=int(npol),
        sefd_jy=None if sefd_jy is None else float(sefd_jy),
        calibration_assumptions=calibration_assumptions,
        noise_basis=context.noise_summary.basis,
        noise_estimator=context.noise_summary.estimator,
        algorithm_name="compute_burst_measurements",
        warning_flags=sorted(set(measurement_flags)),
        low_sn_threshold=float(LOW_SN_THRESHOLD),
        heavily_masked_threshold=float(HEAVILY_MASKED_FRACTION),
        deprecated_fields=["mjd_at_peak"],
    )

    diagnostics = MeasurementDiagnostics(
        gaussian_fits=gaussian_fits,
        time_axis_ms=np.asarray(time_axis_ms, dtype=float),
        time_profile_sn=np.asarray(context.time_profile_sn, dtype=float),
        burst_only_profile_sn=np.asarray(context.selected_profile_sn, dtype=float),
        event_profile_sn=np.asarray(context.event_profile_sn, dtype=float),
        spectral_axis_mhz=np.asarray(context.spectral_axis_mhz, dtype=float),
        spectrum_sn=np.asarray(context.spectrum_sn, dtype=float),
        temporal_acf=np.asarray(temporal_acf, dtype=float),
        temporal_acf_lags_ms=np.asarray(temporal_lags_ms, dtype=float),
        spectral_acf=np.asarray(spectral_acf, dtype=float),
        spectral_acf_lags_mhz=np.asarray(spectral_lags_mhz, dtype=float),
        scattering_fit=None,
    )

    spectral_extent_mhz = 0.0
    if context.spectral_axis_mhz.size:
        spectral_extent_mhz = float(np.max(context.spectral_axis_mhz) - np.min(context.spectral_axis_mhz))

    return BurstMeasurements(
        burst_name=burst_name,
        dm=float(dm),
        toa_topo_mjd=toa_topo_mjd,
        mjd_at_peak=toa_topo_mjd,
        peak_positions_ms=[float(value) for value in peak_positions_ms],
        snr_peak=snr_peak,
        snr_integrated=snr_integrated,
        width_ms_acf=width_ms_acf,
        width_ms_model=None,
        spectral_width_mhz_acf=spectral_width_mhz_acf,
        tau_sc_ms=None,
        peak_flux_jy=peak_flux_jy,
        fluence_jyms=fluence_jyms,
        iso_e=None if iso_e is None else float(iso_e),
        event_duration_ms=float(max(0, event_rel_end - event_rel_start) * tsamp_ms),
        spectral_extent_mhz=spectral_extent_mhz,
        measurement_flags=sorted(set(measurement_flags)),
        uncertainties=uncertainties,
        uncertainty_details=uncertainty_details,
        provenance=provenance,
        diagnostics=diagnostics,
        mask_count=len(list(masked_channels)),
        masked_channels=[int(channel) for channel in masked_channels],
        width_results=[result for result in (width_results or [])],
        accepted_width=accepted_width,
    )
