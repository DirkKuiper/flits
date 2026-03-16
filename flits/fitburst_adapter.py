from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass

import numpy as np

from flits.models import ScatteringFitDiagnostics

try:
    from fitburst.analysis.fitter import LSFitter
    from fitburst.analysis.model import SpectrumModeler
except Exception:  # pragma: no cover - optional runtime dependency
    LSFitter = None
    SpectrumModeler = None


MIN_FIT_CHANNELS = 4
MIN_FIT_TIME_BINS = 16
MIN_WEIGHT_BINS = 8
FIT_PARAMETERS = ("amplitude", "arrival_time", "burst_width", "scattering_timescale")
FIXED_PARAMETERS = ("dm", "dm_index", "scattering_index", "spectral_index", "spectral_running")


@dataclass(frozen=True)
class FitburstScatteringResult:
    status: str
    message: str | None
    width_ms_model: float | None
    width_uncertainty_ms: float | None
    tau_sc_ms: float | None
    tau_uncertainty_ms: float | None
    diagnostics: ScatteringFitDiagnostics


def fit_scattering_selected_band(
    *,
    selected_band: np.ndarray,
    freqs_mhz: np.ndarray,
    time_axis_ms: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    offpulse_bins: np.ndarray,
    tsamp_ms: float,
    peak_rel_bin: int | None,
    width_guess_ms: float | None,
) -> FitburstScatteringResult:
    if SpectrumModeler is None or LSFitter is None:
        return _failed_result(
            status="fitburst_unavailable",
            message="fitburst is not available in the active Python environment.",
        )

    data = np.asarray(selected_band, dtype=float)
    freqs = np.asarray(freqs_mhz, dtype=float)
    time_axis_ms = np.asarray(time_axis_ms, dtype=float)
    offpulse_bins = np.asarray(offpulse_bins, dtype=int)

    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
        return _failed_result(status="insufficient_data", message="No selected-band data are available for fitting.")
    if data.shape[1] < MIN_FIT_TIME_BINS:
        return _failed_result(
            status="insufficient_time_bins",
            message=f"At least {MIN_FIT_TIME_BINS} time bins are required for scattering fits.",
        )

    normalized_data, good_freq = _normalize_dynamic_spectrum(data, offpulse_bins)
    good_channel_count = int(np.count_nonzero(good_freq))
    if good_channel_count < MIN_FIT_CHANNELS:
        return _failed_result(
            status="insufficient_channels",
            message=f"At least {MIN_FIT_CHANNELS} unmasked channels are required for scattering fits.",
        )

    weight_range = _contiguous_weight_range(
        num_time=data.shape[1],
        event_rel_start=int(event_rel_start),
        event_rel_end=int(event_rel_end),
    )
    if weight_range is None:
        return _failed_result(
            status="insufficient_offpulse",
            message="A contiguous off-pulse region is required to estimate fit weights.",
        )

    event_slice = np.asarray(
        np.nanmean(normalized_data[good_freq, event_rel_start:event_rel_end], axis=0),
        dtype=float,
    )
    if event_slice.size == 0 or not np.isfinite(event_slice).any():
        return _failed_result(
            status="insufficient_signal",
            message="The selected event window does not contain a stable signal for fitting.",
        )

    if peak_rel_bin is None:
        peak_rel_bin = int(event_rel_start + np.nanargmax(event_slice))
    peak_rel_bin = max(0, min(int(peak_rel_bin), data.shape[1] - 1))

    initial_parameters = _initial_parameters(
        normalized_data=normalized_data,
        freqs_mhz=freqs,
        time_axis_ms=time_axis_ms,
        tsamp_ms=float(tsamp_ms),
        peak_rel_bin=peak_rel_bin,
        event_rel_start=int(event_rel_start),
        event_rel_end=int(event_rel_end),
        width_guess_ms=width_guess_ms,
    )

    times_sec = (time_axis_ms - float(time_axis_ms[0])) / 1e3
    model = SpectrumModeler(
        freqs,
        times_sec,
        dm_incoherent=0.0,
        num_components=1,
        is_dedispersed=True,
    )
    model.update_parameters(initial_parameters)
    fitter = LSFitter(
        normalized_data,
        model,
        good_freq=good_freq,
        weighted_fit=True,
        weight_range=weight_range,
    )
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        fitter.fix_parameter(list(FIXED_PARAMETERS))
        fitter.fit()

    results = getattr(fitter, "results", None)
    if results is None or not getattr(results, "success", False):
        message = None
        if results is not None:
            message = str(getattr(results, "message", "") or "").strip() or None
        return _failed_result(
            status="fit_failed",
            message=message or "fitburst could not converge for the current selection.",
            initial_parameters=initial_parameters,
            fit_statistics=getattr(fitter, "fit_statistics", {}),
        )

    fit_statistics = dict(getattr(fitter, "fit_statistics", {}))
    bestfit_parameters = dict(fit_statistics.get("bestfit_parameters") or {})
    bestfit_uncertainties = dict(fit_statistics.get("bestfit_uncertainties") or {})
    fit_parameters = list(getattr(fitter, "fit_parameters", []))
    full_best_parameters = dict(initial_parameters)
    full_best_parameters.update(bestfit_parameters)
    model.update_parameters(full_best_parameters)
    model_dynamic_spectrum = np.asarray(model.compute_model(data=normalized_data), dtype=float)

    data_profile = np.nanmean(normalized_data[good_freq, :], axis=0)
    model_profile = np.mean(model_dynamic_spectrum[good_freq, :], axis=0)
    residual_profile = data_profile - model_profile

    width_sec = _parameter_value(full_best_parameters, "burst_width")
    tau_sec = _parameter_value(full_best_parameters, "scattering_timescale")
    width_uncertainty_sec = _parameter_value(bestfit_uncertainties, "burst_width")
    tau_uncertainty_sec = _parameter_value(bestfit_uncertainties, "scattering_timescale")

    diagnostics = ScatteringFitDiagnostics(
        status="ok",
        message=str(getattr(results, "message", "") or "").strip() or None,
        fitter="fitburst",
        component_count=1,
        fit_parameters=fit_parameters,
        fixed_parameters=list(FIXED_PARAMETERS),
        initial_parameters=initial_parameters,
        bestfit_parameters=full_best_parameters,
        bestfit_uncertainties=bestfit_uncertainties,
        fit_statistics=_sanitize_fit_statistics(fit_statistics),
        freq_axis_mhz=np.asarray(freqs, dtype=float),
        time_axis_ms=np.asarray(time_axis_ms, dtype=float),
        data_dynamic_spectrum_sn=np.asarray(normalized_data, dtype=float),
        model_dynamic_spectrum_sn=np.asarray(model_dynamic_spectrum, dtype=float),
        residual_dynamic_spectrum_sn=np.asarray(normalized_data - model_dynamic_spectrum, dtype=float),
        data_profile_sn=np.asarray(data_profile, dtype=float),
        model_profile_sn=np.asarray(model_profile, dtype=float),
        residual_profile_sn=np.asarray(residual_profile, dtype=float),
    )
    return FitburstScatteringResult(
        status="ok",
        message=diagnostics.message,
        width_ms_model=None if width_sec is None else float(width_sec * 1e3),
        width_uncertainty_ms=None if width_uncertainty_sec is None else float(width_uncertainty_sec * 1e3),
        tau_sc_ms=None if tau_sec is None else float(tau_sec * 1e3),
        tau_uncertainty_ms=None if tau_uncertainty_sec is None else float(tau_uncertainty_sec * 1e3),
        diagnostics=diagnostics,
    )


def _normalize_dynamic_spectrum(
    data: np.ndarray,
    offpulse_bins: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    normalized = np.zeros_like(data, dtype=float)
    good_freq = np.zeros(data.shape[0], dtype=bool)
    for index in range(data.shape[0]):
        row = np.asarray(data[index], dtype=float)
        finite_row = row[np.isfinite(row)]
        if finite_row.size == 0:
            continue
        reference = row[offpulse_bins] if offpulse_bins.size else row
        finite_reference = reference[np.isfinite(reference)]
        if finite_reference.size == 0:
            finite_reference = finite_row
        baseline = float(np.nanmean(finite_reference))
        sigma = float(np.nanstd(finite_reference))
        if not np.isfinite(sigma) or sigma <= 0:
            continue
        filled = np.where(np.isfinite(row), row, baseline)
        normalized[index, :] = (filled - baseline) / sigma
        good_freq[index] = True
    return normalized, good_freq


def _contiguous_weight_range(
    *,
    num_time: int,
    event_rel_start: int,
    event_rel_end: int,
) -> list[int] | None:
    candidates = [
        [0, max(0, int(event_rel_start))],
        [min(num_time, int(event_rel_end)), int(num_time)],
    ]
    candidates = [item for item in candidates if item[1] - item[0] >= MIN_WEIGHT_BINS]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1] - item[0])


def _initial_parameters(
    *,
    normalized_data: np.ndarray,
    freqs_mhz: np.ndarray,
    time_axis_ms: np.ndarray,
    tsamp_ms: float,
    peak_rel_bin: int,
    event_rel_start: int,
    event_rel_end: int,
    width_guess_ms: float | None,
) -> dict[str, list[float]]:
    event_window = normalized_data[:, event_rel_start:event_rel_end]
    peak_value = float(np.nanmax(event_window)) if event_window.size and np.isfinite(event_window).any() else 1.0
    amplitude_guess = float(np.log10(max(peak_value, 1e-2)))
    arrival_time_sec = float((time_axis_ms[peak_rel_bin] - float(time_axis_ms[0])) / 1e3)
    width_ms = float(width_guess_ms) if width_guess_ms is not None and np.isfinite(width_guess_ms) else max(tsamp_ms * 2.0, (event_rel_end - event_rel_start) * tsamp_ms / 6.0)
    width_ms = max(width_ms, tsamp_ms)
    tau_ms = max(tsamp_ms, width_ms / 4.0)
    ref_freq = float(np.min(freqs_mhz))
    return {
        "amplitude": [amplitude_guess],
        "arrival_time": [arrival_time_sec],
        "burst_width": [float(width_ms / 1e3)],
        "dm": [0.0],
        "dm_index": [-2.0],
        "ref_freq": [ref_freq],
        "scattering_timescale": [float(tau_ms / 1e3)],
        "scattering_index": [-4.0],
        "spectral_index": [0.0],
        "spectral_running": [0.0],
    }


def _parameter_value(parameters: dict[str, list[float] | None], key: str) -> float | None:
    values = parameters.get(key)
    if not values:
        return None
    value = values[0]
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _sanitize_fit_statistics(
    fit_statistics: dict[str, object],
) -> dict[str, float | int | None]:
    payload: dict[str, float | int | None] = {}
    for key in (
        "chisq_initial",
        "chisq_final",
        "chisq_final_reduced",
        "snr",
        "num_freq",
        "num_freq_good",
        "num_fit_parameters",
        "num_observations",
        "num_time",
    ):
        value = fit_statistics.get(key)
        if value is None:
            payload[key] = None
        elif isinstance(value, (np.integer, int)):
            payload[key] = int(value)
        elif isinstance(value, (np.floating, float)):
            payload[key] = None if not np.isfinite(value) else float(value)
    return payload


def _failed_result(
    *,
    status: str,
    message: str | None,
    initial_parameters: dict[str, list[float]] | None = None,
    fit_statistics: dict[str, object] | None = None,
) -> FitburstScatteringResult:
    diagnostics = ScatteringFitDiagnostics(
        status=status,
        message=message,
        fitter="fitburst" if SpectrumModeler is not None and LSFitter is not None else None,
        component_count=1,
        fit_parameters=list(FIT_PARAMETERS),
        fixed_parameters=list(FIXED_PARAMETERS),
        initial_parameters=initial_parameters or {},
        bestfit_parameters={},
        bestfit_uncertainties={},
        fit_statistics=_sanitize_fit_statistics(fit_statistics or {}),
        freq_axis_mhz=np.array([], dtype=float),
        time_axis_ms=np.array([], dtype=float),
        data_dynamic_spectrum_sn=np.empty((0, 0), dtype=float),
        model_dynamic_spectrum_sn=np.empty((0, 0), dtype=float),
        residual_dynamic_spectrum_sn=np.empty((0, 0), dtype=float),
        data_profile_sn=np.array([], dtype=float),
        model_profile_sn=np.array([], dtype=float),
        residual_profile_sn=np.array([], dtype=float),
    )
    return FitburstScatteringResult(
        status=status,
        message=message,
        width_ms_model=None,
        width_uncertainty_ms=None,
        tau_sc_ms=None,
        tau_uncertainty_ms=None,
        diagnostics=diagnostics,
    )
