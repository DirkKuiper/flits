from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from flits.measurements import MeasurementContext, event_snr
from flits.models import (
    DmMetricDefinition,
    DmMetricReference,
    DmOptimizationProvenance,
    DmOptimizationResult,
    DmOptimizationSettings,
)
from flits.signal import dedisperse


ResidualRunner = Callable[[], tuple[np.ndarray, np.ndarray, np.ndarray, str]]
DMMetricRunner = Callable[[MeasurementContext, ResidualRunner | None], float]


def _dm_ref(label: str, citation: str, url: str, note: str | None = None) -> DmMetricReference:
    return DmMetricReference(label=label, citation=citation, url=url, note=note)


DM_METRIC_METADATA: dict[str, DmMetricDefinition] = {
    "integrated_event_snr": DmMetricDefinition(
        key="integrated_event_snr",
        label="Integrated-event S/N",
        summary="Sum the off-pulse-normalized event profile across the selected event window.",
        formula="score = sum_i s_i / sqrt(N_event), where s_i is the event profile in off-pulse noise units.",
        origin="Direct FLITS implementation of the standard integrated-S/N idea used by pulsar/FRB search software.",
        references=[
            _dm_ref(
                label="PSRCHIVE psrstat",
                citation="PSRCHIVE psrstat documentation: phase S/N algorithm",
                url="https://psrchive.sourceforge.net/manuals/psrstat/algorithms/snr/",
                note="Documents integrated on-pulse S/N estimation relative to off-pulse noise.",
            ),
            _dm_ref(
                label="PDMP",
                citation="Teoh 2005, PDMP User Manual",
                url="https://psrchive.sourceforge.net/manuals/pdmp/pdmp_manual.html",
                note="Software precedent for DM sweeps scored by profile S/N.",
            ),
        ],
    ),
    "peak_snr": DmMetricDefinition(
        key="peak_snr",
        label="Peak S/N",
        summary="Take the maximum off-pulse-normalized sample inside the selected event window.",
        formula="score = max_i s_i over the selected event bins.",
        origin="FLITS simplification of peak-S/N pulse-search logic; inspired by software that searches for the strongest dedispersed pulse after baseline/noise normalization.",
        references=[
            _dm_ref(
                label="PSRCHIVE psrstat",
                citation="PSRCHIVE psrstat documentation: phase/pdmp S/N algorithms",
                url="https://psrchive.sourceforge.net/manuals/psrstat/algorithms/snr/",
                note="Background on profile S/N definitions used in pulsar software.",
            ),
            _dm_ref(
                label="PDMP",
                citation="Teoh 2005, PDMP User Manual",
                url="https://psrchive.sourceforge.net/manuals/pdmp/pdmp_manual.html",
                note="DM-search software precedent; FLITS uses a simpler peak-in-window statistic rather than pdmp's matched-boxcar sweep.",
            ),
        ],
    ),
    "profile_sharpness": DmMetricDefinition(
        key="profile_sharpness",
        label="Profile Sharpness",
        summary="After light smoothing, favor DMs that concentrate burst power into sharper time structure.",
        formula="p_i = max(0, baselined event profile); p_tilde = [0.25, 0.5, 0.25] * p; score = sum_i p_tilde_i^2.",
        origin="FLITS heuristic inspired by structure-based DM optimization, especially DM-power, but not a reproduction of the DM-power Fourier-domain statistic.",
        references=[
            _dm_ref(
                label="DM-power",
                citation="Majid et al. 2022, DM-power: an algorithm for high precision dispersion measure with application to fast radio bursts",
                url="https://arxiv.org/abs/2208.13677",
                note="Primary inspiration for structure-aware DM optimization in FRBs.",
            ),
        ],
    ),
    "burst_compactness": DmMetricDefinition(
        key="burst_compactness",
        label="Burst Compactness",
        summary="Favor high fluence concentration within a narrow event profile.",
        formula="p_i = max(0, baselined event profile); score = (sum_i p_i^2) / (sum_i p_i).",
        origin="FLITS heuristic related to equivalent-width and matched-filter intuition; not a direct literature formula.",
        references=[
            _dm_ref(
                label="PSRCHIVE psrstat",
                citation="PSRCHIVE psrstat documentation: pdmp S/N algorithm",
                url="https://psrchive.sourceforge.net/manuals/psrstat/algorithms/snr/",
                note="Matched-boxcar S/N motivates rewarding fluence concentration.",
            ),
            _dm_ref(
                label="DM-power",
                citation="Majid et al. 2022, DM-power",
                url="https://arxiv.org/abs/2208.13677",
                note="Structure-preserving DM optimization is the broader motivation; FLITS uses a simpler time-domain compactness score.",
            ),
        ],
    ),
    "minimal_residual_drift": DmMetricDefinition(
        key="minimal_residual_drift",
        label="Minimal Residual Drift",
        summary="Use sub-band arrival times and reward DMs that flatten the residual delay pattern across frequency.",
        formula="r_j = t_j - mean(t); score = 1 / sqrt(mean_j r_j^2), where t_j is the sub-band arrival time.",
        origin="FLITS residual-alignment heuristic inspired by wideband TOA+DM fitting and per-channel/sub-band arrival-time analyses.",
        references=[
            _dm_ref(
                label="Wideband timing",
                citation="Pennucci, Demorest, and Ransom 2014, Elementary Wideband Timing of Radio Pulsars",
                url="https://arxiv.org/abs/1402.1672",
                note="Primary reference for simultaneously constraining TOA and DM from frequency-resolved pulse data.",
            ),
            _dm_ref(
                label="Per-channel arrivals",
                citation="High precision spectro-temporal analysis of ultra-fast radio bursts using per-channel arrival times",
                url="https://arxiv.org/abs/2412.12404",
                note="FRB-specific example of frequency-resolved arrival-time analysis.",
            ),
        ],
    ),
    "maximal_structure": DmMetricDefinition(
        key="maximal_structure",
        label="Maximal Structure",
        summary="After light smoothing, reward larger absolute second differences in the event profile.",
        formula="p_tilde = [0.25, 0.5, 0.25] * p; score = sum_i |Delta^2 p_tilde_i|.",
        origin="FLITS heuristic inspired by multi-scale structure metrics such as DM-power; this implementation is a simple time-domain curvature score, not the published DM-power algorithm.",
        references=[
            _dm_ref(
                label="DM-power",
                citation="Majid et al. 2022, DM-power",
                url="https://arxiv.org/abs/2208.13677",
                note="Primary published reference for maximizing burst structure during DM optimization.",
            ),
        ],
    ),
}


def available_dm_metrics() -> list[dict[str, Any]]:
    return [definition.to_dict() for definition in DM_METRIC_METADATA.values()]


def _finite_or_neg_inf(value: float) -> float:
    return float(value) if np.isfinite(value) else float("-inf")


def _positive_event_profile(context: MeasurementContext) -> np.ndarray:
    profile = np.asarray(context.event_profile_baselined, dtype=float)
    if profile.size == 0:
        return np.array([], dtype=float)
    profile = np.where(np.isfinite(profile), profile, 0.0)
    return np.clip(profile, a_min=0.0, a_max=None)


def _smoothed_profile(values: np.ndarray) -> np.ndarray:
    profile = np.asarray(values, dtype=float)
    if profile.size < 3:
        return profile
    kernel = np.array([0.25, 0.5, 0.25], dtype=float)
    return np.convolve(profile, kernel, mode="same")


def _run_integrated_event_snr(context: MeasurementContext, residuals: ResidualRunner | None) -> float:
    del residuals
    return float(event_snr(context.selected_profile_sn, context.event_rel_start, context.event_rel_end))


def _run_peak_snr(context: MeasurementContext, residuals: ResidualRunner | None) -> float:
    del residuals
    event_profile = np.asarray(context.event_profile_sn, dtype=float)
    finite = event_profile[np.isfinite(event_profile)]
    if finite.size == 0:
        return float("-inf")
    return float(np.nanmax(finite))


def _run_profile_sharpness(context: MeasurementContext, residuals: ResidualRunner | None) -> float:
    del residuals
    profile = _smoothed_profile(_positive_event_profile(context))
    if profile.size == 0 or not np.any(profile > 0):
        return float("-inf")
    return _finite_or_neg_inf(float(np.nansum(profile ** 2)))


def _run_burst_compactness(context: MeasurementContext, residuals: ResidualRunner | None) -> float:
    del residuals
    profile = _positive_event_profile(context)
    if profile.size == 0 or not np.any(profile > 0):
        return float("-inf")
    fluence = float(np.nansum(profile))
    power = float(np.nansum(profile ** 2))
    if not np.isfinite(fluence) or not np.isfinite(power) or fluence <= 0 or power <= 0:
        return float("-inf")
    return float(power / fluence)


def _run_minimal_residual_drift(context: MeasurementContext, residuals: ResidualRunner | None) -> float:
    del context
    if residuals is None:
        return float("-inf")
    _, _, residual_values, status = residuals()
    if status != "ok":
        return float("-inf")
    residual_values = np.asarray(residual_values, dtype=float)
    finite = residual_values[np.isfinite(residual_values)]
    if finite.size < 3:
        return float("-inf")
    rms = float(np.sqrt(np.mean(finite ** 2)))
    if not np.isfinite(rms):
        return float("-inf")
    return float(1.0 / max(rms, 1e-6))


def _run_maximal_structure(context: MeasurementContext, residuals: ResidualRunner | None) -> float:
    del residuals
    profile = _smoothed_profile(_positive_event_profile(context))
    if profile.size < 3 or not np.any(profile > 0):
        return float("-inf")
    curvature = np.diff(profile, n=2)
    if curvature.size == 0:
        return float("-inf")
    return _finite_or_neg_inf(float(np.nansum(np.abs(curvature))))


DM_METRIC_REGISTRY: dict[str, DMMetricRunner] = {
    "integrated_event_snr": _run_integrated_event_snr,
    "peak_snr": _run_peak_snr,
    "profile_sharpness": _run_profile_sharpness,
    "burst_compactness": _run_burst_compactness,
    "minimal_residual_drift": _run_minimal_residual_drift,
    "maximal_structure": _run_maximal_structure,
}


def dm_trial_grid(center_dm: float, half_range: float, step: float) -> tuple[np.ndarray, float]:
    center_dm = float(center_dm)
    half_range = float(half_range)
    step = float(step)
    if not all(np.isfinite(value) for value in (center_dm, half_range, step)):
        raise ValueError("DM sweep parameters must be finite numbers.")
    if half_range <= 0:
        raise ValueError("DM sweep half-range must be greater than zero.")
    if step <= 0:
        raise ValueError("DM sweep step must be greater than zero.")

    num_side = int(np.floor((half_range / step) + 1e-12))
    num_trials = 2 * num_side + 1
    if num_trials < 5:
        raise ValueError("DM sweep must include at least 5 trial DMs.")
    if num_trials > 121:
        raise ValueError("DM sweep supports at most 121 trial DMs.")

    offsets = np.arange(-num_side, num_side + 1, dtype=float) * step
    trial_dms = np.round(center_dm + offsets, 12)
    actual_half_range = float(abs(offsets[-1])) if offsets.size else 0.0
    return trial_dms, actual_half_range


def _uncertainty_drop(metric: str, best_value: float, local_values: np.ndarray) -> float:
    if metric in {"integrated_event_snr", "peak_snr"}:
        return 1.0
    finite = np.asarray(local_values, dtype=float)
    finite = finite[np.isfinite(finite)]
    local_span = float(np.nanmax(finite) - np.nanmin(finite)) if finite.size else 0.0
    candidate = max(0.1 * local_span, 0.02 * abs(float(best_value)), 1e-6)
    return float(candidate)


def fit_dm_peak(
    trial_dms: np.ndarray,
    scores: np.ndarray,
    peak_index: int,
    *,
    metric: str = "integrated_event_snr",
) -> tuple[float, float, float | None, str]:
    sampled_best_dm = float(trial_dms[peak_index])
    sampled_best_score = float(scores[peak_index])

    if peak_index < 2 or peak_index > len(trial_dms) - 3:
        return sampled_best_dm, sampled_best_score, None, "peak_on_sweep_edge"

    x = np.asarray(trial_dms[peak_index - 2 : peak_index + 3], dtype=float)
    y = np.asarray(scores[peak_index - 2 : peak_index + 3], dtype=float)
    if x.size != 5 or not np.all(np.isfinite(y)):
        return sampled_best_dm, sampled_best_score, None, "insufficient_peak_window"

    try:
        coeffs = np.polyfit(x, y, 2)
    except Exception:
        return sampled_best_dm, sampled_best_score, None, "quadratic_fit_failed"

    a, b, c = (float(value) for value in coeffs)
    if not np.all(np.isfinite(coeffs)):
        return sampled_best_dm, sampled_best_score, None, "quadratic_fit_failed"
    if a >= 0:
        return sampled_best_dm, sampled_best_score, None, "quadratic_not_concave"

    best_dm = float(-b / (2 * a))
    if best_dm < float(x[0]) or best_dm > float(x[-1]):
        return sampled_best_dm, sampled_best_score, None, "fit_vertex_outside_peak_window"

    best_score = float(np.polyval(coeffs, best_dm))
    if not np.isfinite(best_score):
        return sampled_best_dm, sampled_best_score, None, "quadratic_fit_failed"

    target_score = best_score - _uncertainty_drop(metric, best_score, y)
    uncertainty: float | None = None
    status = "quadratic_peak_fit"
    try:
        roots = np.roots([a, b, c - target_score])
    except Exception:
        roots = np.array([], dtype=complex)

    real_roots = sorted(
        float(root.real)
        for root in np.atleast_1d(roots)
        if np.isfinite(root.real) and abs(float(root.imag)) < 1e-6
    )
    lower = max((root for root in real_roots if root <= best_dm), default=None)
    upper = min((root for root in real_roots if root >= best_dm), default=None)
    if lower is not None and upper is not None:
        sweep_min = float(np.min(trial_dms))
        sweep_max = float(np.max(trial_dms))
        candidate = 0.5 * (upper - lower)
        if np.isfinite(candidate) and candidate > 0 and lower >= sweep_min and upper <= sweep_max:
            uncertainty = float(candidate)
        else:
            status = "quadratic_peak_fit_uncertainty_unavailable"
    else:
        status = "quadratic_peak_fit_uncertainty_unavailable"

    return best_dm, best_score, uncertainty, status


def _residual_summary(
    freqs_mhz: np.ndarray,
    residuals_ms: np.ndarray,
) -> tuple[float | None, float | None]:
    freqs = np.asarray(freqs_mhz, dtype=float)
    residuals = np.asarray(residuals_ms, dtype=float)
    finite = np.isfinite(freqs) & np.isfinite(residuals)
    if int(np.count_nonzero(finite)) < 2:
        return None, None
    freqs = freqs[finite]
    residuals = residuals[finite]
    rms = float(np.sqrt(np.mean(residuals ** 2)))
    slope = None
    if freqs.size >= 2:
        try:
            slope = float(np.polyfit(freqs, residuals, 1)[0])
        except Exception:
            slope = None
    return (rms if np.isfinite(rms) else None), slope


def optimize_dm_trials(
    *,
    data: np.ndarray,
    current_dm: float,
    freqs_mhz: np.ndarray,
    tsamp_sec: float,
    center_dm: float,
    half_range: float,
    step: float,
    context_builder: Callable[[np.ndarray], MeasurementContext],
    residuals: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray, str]],
    provenance: DmOptimizationProvenance,
    metric: str = "integrated_event_snr",
) -> DmOptimizationResult:
    metric_runner = DM_METRIC_REGISTRY.get(metric)
    if metric_runner is None:
        valid = ", ".join(sorted(DM_METRIC_REGISTRY))
        raise ValueError(f"Unsupported DM metric '{metric}'. Valid metrics: {valid}.")

    trial_dms, actual_half_range = dm_trial_grid(center_dm, half_range, step)
    data = np.asarray(data, dtype=float)
    freqs_mhz = np.asarray(freqs_mhz, dtype=float)

    scores = np.empty(trial_dms.size, dtype=float)
    for idx, trial_dm in enumerate(trial_dms):
        if np.isclose(trial_dm, current_dm):
            trial_data = data
        else:
            trial_data = dedisperse(data, float(trial_dm - current_dm), freqs_mhz, float(tsamp_sec))

        context = context_builder(trial_data)
        residual_cache: tuple[np.ndarray, np.ndarray, np.ndarray, str] | None = None

        def residual_provider() -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
            nonlocal residual_cache
            if residual_cache is None:
                residual_cache = residuals(trial_data)
            return residual_cache

        scores[idx] = metric_runner(
            context,
            residual_provider if metric == "minimal_residual_drift" else None,
        )

    if not np.isfinite(scores).any():
        raise ValueError("Unable to compute DM sweep for the current selection.")

    peak_index = int(np.nanargmax(scores))
    sampled_best_dm = float(trial_dms[peak_index])
    sampled_best_score = float(scores[peak_index])
    best_dm, best_score, best_dm_uncertainty, fit_status = fit_dm_peak(
        trial_dms,
        scores,
        peak_index,
        metric=metric,
    )

    applied_freqs, applied_arrivals, applied_residuals, applied_status = residuals(data)
    best_freqs = np.array([], dtype=float)
    best_arrivals = np.array([], dtype=float)
    best_residuals = np.array([], dtype=float)
    if np.isclose(best_dm, current_dm):
        best_freqs = applied_freqs
        best_arrivals = applied_arrivals
        best_residuals = applied_residuals
        residual_status = applied_status
    else:
        best_data = dedisperse(data, float(best_dm - current_dm), freqs_mhz, float(tsamp_sec))
        best_freqs, best_arrivals, best_residuals, best_status = residuals(best_data)
        if applied_status == "ok" and best_status == "ok":
            residual_status = "ok"
        else:
            residual_status = applied_status if applied_status != "ok" else best_status

    subband_freqs = applied_freqs if applied_freqs.size else best_freqs
    arrival_times_applied = applied_arrivals if residual_status == "ok" and applied_freqs.size else np.array([], dtype=float)
    residuals_applied = applied_residuals if residual_status == "ok" and applied_freqs.size else np.array([], dtype=float)
    arrival_times_best = best_arrivals if residual_status == "ok" and best_freqs.size else np.array([], dtype=float)
    residuals_best = best_residuals if residual_status == "ok" and best_freqs.size else np.array([], dtype=float)

    residual_rms_applied_ms: float | None = None
    residual_rms_best_ms: float | None = None
    residual_slope_applied_ms_per_mhz: float | None = None
    residual_slope_best_ms_per_mhz: float | None = None
    if residual_status == "ok":
        residual_rms_applied_ms, residual_slope_applied_ms_per_mhz = _residual_summary(
            subband_freqs,
            residuals_applied,
        )
        residual_rms_best_ms, residual_slope_best_ms_per_mhz = _residual_summary(
            subband_freqs,
            residuals_best,
        )
    else:
        subband_freqs = np.array([], dtype=float)
        arrival_times_applied = np.array([], dtype=float)
        arrival_times_best = np.array([], dtype=float)
        residuals_applied = np.array([], dtype=float)
        residuals_best = np.array([], dtype=float)

    settings = DmOptimizationSettings(
        center_dm=float(center_dm),
        half_range=float(half_range),
        step=float(step),
        metric=metric,
    )
    return DmOptimizationResult(
        center_dm=float(center_dm),
        requested_half_range=float(half_range),
        actual_half_range=actual_half_range,
        step=float(step),
        trial_dms=trial_dms,
        snr=scores,
        snr_metric=metric,
        applied_dm=float(current_dm),
        sampled_best_dm=sampled_best_dm,
        sampled_best_sn=sampled_best_score,
        best_dm=best_dm,
        best_dm_uncertainty=best_dm_uncertainty,
        best_sn=best_score,
        fit_status=fit_status,
        subband_freqs_mhz=subband_freqs,
        arrival_times_applied_ms=arrival_times_applied,
        arrival_times_best_ms=arrival_times_best,
        residuals_applied_ms=residuals_applied,
        residuals_best_ms=residuals_best,
        residual_status=residual_status,
        residual_rms_applied_ms=residual_rms_applied_ms,
        residual_rms_best_ms=residual_rms_best_ms,
        residual_slope_applied_ms_per_mhz=residual_slope_applied_ms_per_mhz,
        residual_slope_best_ms_per_mhz=residual_slope_best_ms_per_mhz,
        settings=settings,
        provenance=provenance,
    )
