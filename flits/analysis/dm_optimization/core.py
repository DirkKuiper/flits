"""DM optimization metrics and trial-sweep orchestration used by FLITS.

The session layer calls this module with the current dynamic spectrum, selected
event window, spectral selection, masking, and reduced-resolution state. This
module builds a symmetric trial-DM grid, dedisperses each trial relative to the
currently applied DM, scores each trial with a registered metric, refines the
peak DM when possible, and computes residual arrival-time diagnostics at the
applied and best-fit DMs.

DM values are expressed in the usual pulsar/FRB units of ``pc cm^-3``.
Frequency arrays are in MHz. Sampling intervals passed to the optimizer are in
seconds, while serialized provenance and residual diagnostics use milliseconds.

This module includes a clean-room FLITS implementation of DMphase. FLITS keeps
the runtime implementation native so it can operate on the current reduced
analysis grid, masking, crop, and selection state without depending on the
external package.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import signal

from flits.measurements import MeasurementContext, event_snr
from flits.models import (
    DmMetricDefinition,
    DmMetricReference,
    DmOptimizationProvenance,
    DmOptimizationResult,
    DmOptimizationSettings,
    UncertaintyDetail,
    compatible_scalar_uncertainty,
)
from flits.signal import dedisperse


ResidualRunner = Callable[[], tuple[np.ndarray, np.ndarray, np.ndarray, str]]


@dataclass(frozen=True)
class DMMetricInput:
    """Data bundle passed to a DM scoring metric for one trial DM.

    Parameters
    ----------
    context
        Measurement context derived from the trial-dedispersed waterfall. It
        carries event bounds, selected-channel bounds, normalized profiles, and
        noise/off-pulse metadata.
    waterfall
        Full waterfall used by the metric, with shape ``(n_channels, n_time)``.
        For DMphase this is usually the current reduced analysis grid.
    selected_waterfall
        Frequency-selected waterfall, with shape
        ``(n_selected_channels, n_time)``.
    event_waterfall
        Event-window slice of ``selected_waterfall``, with shape
        ``(n_selected_channels, n_event_time)``.
    offpulse_bins
        Time-bin indices used as the off-pulse reference for this trial.
    freqs_mhz
        Frequency axis in MHz matching ``selected_waterfall`` rows.
    tsamp_sec
        Effective time resolution of ``waterfall`` in seconds.
    """

    context: MeasurementContext
    waterfall: np.ndarray
    selected_waterfall: np.ndarray
    event_waterfall: np.ndarray
    offpulse_bins: np.ndarray
    freqs_mhz: np.ndarray
    tsamp_sec: float


@dataclass(frozen=True)
class DMMetricPreparedTrial:
    """Intermediate per-trial metric state.

    ``scalar_score`` is used by metrics that can be scored independently per
    DM trial. DMphase stores a phase-coherent Fourier power spectrum per trial
    and converts the stack of spectra into final scores after all trials have
    been prepared.
    """

    scalar_score: float | None = None
    dmphase_power_spectrum: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    dmphase_channel_count: int = 0


@dataclass(frozen=True)
class DMMetricAlgorithm:
    """Callable pair that defines one registered DM scoring metric."""

    prepare_trial: Callable[[DMMetricInput], DMMetricPreparedTrial]
    finalize_scores: Callable[[list[DMMetricPreparedTrial]], "DMMetricFinalizeResult"]


@dataclass(frozen=True)
class DMMetricFitResult:
    """Peak-refinement result for a metric-specific DM score curve."""

    best_dm: float
    best_score: float
    best_dm_uncertainty: float | None
    fit_status: str


@dataclass(frozen=True)
class DMMetricFinalizeResult:
    """Final score vector and optional payload for metric-specific peak fits."""

    scores: np.ndarray
    fit_result: DMMetricFitResult | None = None
    fit_payload: DMPhaseFitInput | None = None


@dataclass(frozen=True)
class DMPhaseFitInput:
    """DMphase-only payload needed to refine the peak score curve.

    Parameters
    ----------
    power_spectra
        Stacked coherent-power spectra with one column per trial DM.
    low_idx
        Lower fluctuation-frequency index selected by the automatic DMphase
        cutoff logic.
    dstd
        Score-curve error scale used by the weighted polynomial peak fit.
    weights
        Per-trial weights derived from the automatic DMphase S/N curve.
    """

    power_spectra: np.ndarray
    low_idx: int
    dstd: float
    weights: np.ndarray


def _best_dm_uncertainty_detail(
    *,
    value: float | None,
    metric: str,
    fit_status: str,
) -> UncertaintyDetail:
    metric_basis = (
        "Weighted-polynomial width from the local DMphase score peak."
        if metric == "dm_phase"
        else "Quadratic width from the local DM score peak."
    )
    basis = (
        f"{metric_basis} FLITS still evaluates the sweep with integer-bin dedispersion on the active "
        "analysis grid, so this width is a heuristic local-fit diagnostic rather than a publishable 1-sigma DM uncertainty."
    )
    tooltip = (
        "Diagnostic width of the local DM fit around the sampled peak. It is retained for comparison and UI context, "
        "but it is not exported as a formal 1-sigma uncertainty while integer-bin dedispersion sets a quantization floor."
    )
    warning_flags = ["integer_bin_dedispersion", str(fit_status)]
    if value is None:
        warning_flags.append("uncertainty_unavailable")
    return UncertaintyDetail(
        value=None if value is None else float(value),
        units="pc cm^-3",
        classification="heuristic_local_fit",
        is_formal_1sigma=False,
        publishable=False,
        basis=basis,
        tooltip=tooltip,
        warning_flags=warning_flags,
    )


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
    "dm_phase": DmMetricDefinition(
        key="dm_phase",
        label="DMphase",
        summary="Use the automatic DMphase coherent-power curve on the selected reduced waterfall.",
        formula="score = DMphase automatic coherent-power curve derived from phase-only Fourier sums across frequency channels.",
        origin=(
            "Clean-room FLITS implementation aligned to the automatic non-interactive "
            "DM_phase algorithm. FLITS reproduces the coherent-power and automatic DM-curve "
            "construction while keeping the runtime implementation native."
        ),
        references=[
            _dm_ref(
                label="DM_phase",
                citation="Seymour, Michilli, and Pleunis, DM_phase repository",
                url="https://github.com/danielemichilli/DM_phase",
                note="FLITS follows the automatic `get_dm(...)` path rather than the manual GUI workflow.",
            ),
            _dm_ref(
                label="ASCL",
                citation="ascl:1910.004, DM_phase",
                url="https://ascl.net/1910.004",
                note="Software reference for the DMphase method.",
            ),
        ],
    ),
}


def available_dm_metrics() -> list[dict[str, Any]]:
    """Return JSON-compatible metadata for all selectable DM metrics."""
    return [definition.to_dict() for definition in DM_METRIC_METADATA.values()]


def dm_metric_definition(metric: str) -> DmMetricDefinition | None:
    """Return the metadata definition for ``metric``, if it is registered."""
    return DM_METRIC_METADATA.get(str(metric))


def _run_integrated_event_snr_prepare(metric_input: DMMetricInput) -> DMMetricPreparedTrial:
    context = metric_input.context
    score = float(event_snr(context.selected_profile_sn, context.event_rel_start, context.event_rel_end))
    return DMMetricPreparedTrial(scalar_score=score)


def _finalize_scalar_scores(prepared_trials: list[DMMetricPreparedTrial]) -> DMMetricFinalizeResult:
    """Convert independently prepared scalar trials into the final score vector."""
    scores = np.asarray(
        [
            float("-inf")
            if trial.scalar_score is None or not np.isfinite(trial.scalar_score)
            else float(trial.scalar_score)
            for trial in prepared_trials
        ],
        dtype=float,
    )
    return DMMetricFinalizeResult(scores=scores)


def _dmphase_window(profile: np.ndarray) -> int:
    """Estimate the automatic DMphase smoothing window from profile autocorrelation."""
    values = np.asarray(profile, dtype=float)
    if values.size < 3 or not np.isfinite(values).any():
        return 1
    smooth_profile = signal.detrend(values)
    autocorrelation = np.correlate(smooth_profile, smooth_profile, "same")
    negative = np.flatnonzero(autocorrelation < 0)
    if negative.size < 2:
        return max(1, values.size // 8)
    return max(1, int(np.max(np.diff(negative))))


def _dmphase_frequency_cutoff(power_spectra: np.ndarray, nchan: int) -> tuple[int, int]:
    """Return the fluctuation-frequency range used by the DMphase score curve."""
    spectra = np.asarray(power_spectra, dtype=float)
    if spectra.ndim != 2 or spectra.size == 0 or nchan <= 0:
        return 0, 0
    peak_power = np.max(spectra, axis=1)
    std = float(nchan) / np.sqrt(2.0)
    if not np.isfinite(std) or std <= 0:
        return 0, 0
    snr = (peak_power - float(nchan)) / std
    kern = int(np.round(_dmphase_window(snr) / 2.0))
    return 0, max(5, kern)


def _prepare_dmphase_waterfall(waterfall: np.ndarray) -> tuple[np.ndarray, int]:
    """Drop rows that cannot contribute stable phase-only Fourier information."""
    values = np.asarray(waterfall, dtype=float)
    if values.ndim != 2 or values.size == 0:
        return np.array([], dtype=float), 0

    filtered_rows: list[np.ndarray] = []
    for row in values:
        finite = np.isfinite(row)
        if not finite.all():
            continue
        if row.size < 4:
            continue
        filtered_rows.append(np.asarray(row, dtype=float))

    if len(filtered_rows) < 2:
        return np.array([], dtype=float), 0
    return np.asarray(filtered_rows, dtype=float), len(filtered_rows)


def _dmphase_coherent_power_spectrum(waterfall: np.ndarray) -> np.ndarray:
    """Compute the DMphase phase-coherent power spectrum for one trial waterfall."""
    fourier_transform = np.fft.fft(np.asarray(waterfall, dtype=float), axis=-1)
    amplitude = np.abs(fourier_transform)
    amplitude[amplitude == 0] = 1.0
    coherence_spectrum = np.sum(fourier_transform / amplitude, axis=0)
    return np.abs(coherence_spectrum) ** 2


def _run_dmphase_prepare(metric_input: DMMetricInput) -> DMMetricPreparedTrial:
    """Prepare one trial for DMphase finalization."""
    dmphase_waterfall, nchan = _prepare_dmphase_waterfall(metric_input.selected_waterfall)
    if nchan < 2 or dmphase_waterfall.shape[1] < 4:
        return DMMetricPreparedTrial()

    power_spectrum = _dmphase_coherent_power_spectrum(dmphase_waterfall)
    half_spectrum_bins = dmphase_waterfall.shape[1] // 2
    if half_spectrum_bins < 2 or power_spectrum.size < half_spectrum_bins:
        return DMMetricPreparedTrial()

    return DMMetricPreparedTrial(
        dmphase_power_spectrum=np.asarray(power_spectrum[:half_spectrum_bins], dtype=float),
        dmphase_channel_count=int(nchan),
    )


def _dmphase_curve(
    power_spectra: np.ndarray,
    dpower_spectra: np.ndarray,
    nchan: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Construct the automatic DMphase coherent-power curve.

    Parameters
    ----------
    power_spectra
        Phase-coherent spectra with shape ``(n_fluctuation_bins, n_trials)``.
    dpower_spectra
        ``power_spectra`` weighted by squared fluctuation-frequency index.
    nchan
        Median number of usable frequency channels across the trial sweep.

    Returns
    -------
    dm_curve, dm_curve_error, snr
        Automatic DMphase score curve, uncertainty scale, and S/N-like weights
        for each trial DM.
    """
    n = int(power_spectra.shape[0])
    m = int(power_spectra.shape[1])
    if n <= 0 or m <= 0 or nchan <= 0:
        return (
            np.zeros(m, dtype=float),
            np.zeros(m, dtype=float),
            np.zeros(m, dtype=float),
        )

    _, y_grid = np.meshgrid(np.arange(m), np.arange(n))
    num_el = (n - y_grid).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        cumulative = np.cumsum(power_spectra, axis=0)
        cumulative_sq = np.cumsum(power_spectra ** 2, axis=0)
        s = np.divide(np.sum(power_spectra, axis=0).T - cumulative, num_el)
        s2 = np.divide(np.sum(power_spectra ** 2, axis=0).T - cumulative_sq, num_el)
        variance = np.divide(s2 - s ** 2, num_el)

    variance = np.where(np.isfinite(variance), variance, 0.0)
    variance_smoothed = signal.convolve2d(
        variance,
        np.ones((9, 3), dtype=float) / 27.0,
        mode="same",
        boundary="wrap",
    )
    usable_variance = variance_smoothed[:-10, :] if variance_smoothed.shape[0] > 10 else variance_smoothed
    idx_f = np.argmin(usable_variance, axis=0)
    idx_c = np.convolve(idx_f, np.ones(3, dtype=float) / 3.0, mode="same").astype(int)
    idx_c[idx_c == 0] = 1
    idx_c = np.ones(np.shape(idx_c), dtype=float) * idx_c
    i2_sum = np.multiply(np.multiply(idx_c, idx_c + 1.0), 2.0 * idx_c + 1.0) / 6.0
    i4_sum = (
        np.multiply(np.multiply(np.multiply(idx_c, idx_c + 1.0), 2.0 * idx_c + 1.0), 6.0 * idx_c - 1.0)
        / 30.0
    )

    lo = np.multiply(y_grid <= (np.ones((n, 1), dtype=float) * idx_c), dpower_spectra)
    lo1 = np.multiply(y_grid <= (np.ones((n, 1), dtype=float) * idx_c), np.multiply(power_spectra, dpower_spectra))
    average_noise_power = 2.0 * float(nchan) * np.ones(np.shape(idx_c), dtype=float)
    dm_curve = lo.sum(axis=0)
    dn_term = lo1.sum(axis=0)
    noise_curve = np.multiply(average_noise_power, i2_sum)
    variance_dp = (2.0 * float(nchan) ** 2 * i4_sum) + dn_term
    dm_curve_error = np.sqrt(np.clip(variance_dp, a_min=0.0, a_max=None))
    denominator = np.sqrt(np.clip(2.0 * float(nchan) ** 2 * (i4_sum + 2.0 * i2_sum), a_min=0.0, a_max=None))

    with np.errstate(invalid="ignore", divide="ignore"):
        snr = np.divide(dm_curve - noise_curve, denominator)
    snr = np.where(np.isfinite(snr), snr, 0.0)
    return (
        np.asarray(dm_curve, dtype=float),
        np.asarray(dm_curve_error, dtype=float),
        np.asarray(snr, dtype=float),
    )


def _dmphase_poly_max(
    x: np.ndarray,
    y: np.ndarray,
    err: float,
    w: np.ndarray | None,
) -> tuple[float, float, np.ndarray, float]:
    """Fit a local polynomial and return its maximum and uncertainty scale."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size == 0 or y.size == 0 or x.size != y.size:
        raise ValueError("DMphase polynomial fit requires matching non-empty x/y arrays.")

    if x.shape[0] < 7:
        order = int(np.linalg.matrix_rank(np.vander(y)))
    else:
        order = 6
    order = max(1, min(order, x.shape[0] - 1))
    dx = x - float(np.mean(x))

    if w is None:
        poly = np.polyfit(dx, y, order)
        err = max(float(np.std(y - np.polyval(poly, dx))), float(err))
    else:
        weights = np.asarray(w, dtype=float)
        finite = np.isfinite(weights) & (weights > 0)
        if not finite.any():
            weights = None
            poly = np.polyfit(dx, y, order)
            err = max(float(np.std(y - np.polyval(poly, dx))), float(err))
        else:
            weights = weights[finite]
            dx = dx[finite]
            y = y[finite]
            x = x[finite]
            poly = np.polyfit(dx, y, order, w=weights)
            weighted_residual = float(
                np.sqrt(np.sum(weights * (y - np.polyval(poly, dx)) ** 2.0) / np.sum(weights))
            )
            err = max(weighted_residual, float(err))

    dpoly = np.polyder(poly)
    ddpoly = np.polyder(dpoly)
    candidates = np.roots(dpoly)
    curvatures = np.polyval(ddpoly, candidates)
    valid = (
        np.isclose(candidates.imag, 0.0)
        & (candidates.real >= float(np.min(dx)))
        & (candidates.real <= float(np.max(dx)))
        & (curvatures < 0)
    )
    first_cut = candidates[valid]
    if first_cut.size > 0:
        values = np.polyval(poly, first_cut)
        best = float(np.real(first_cut[int(np.argmax(values))]))
        curvature = float(np.polyval(ddpoly, best))
        delta_x = float(np.sqrt(abs(2.0 * float(err) / curvature))) if curvature != 0 else 0.0
    else:
        best = 0.0
        delta_x = 0.0

    x_mean = float(np.mean(x))
    return float(best + x_mean), delta_x, np.asarray(poly, dtype=float), x_mean


def _fit_dmphase_peak(
    trial_dms: np.ndarray,
    scores: np.ndarray,
    power_spectra: np.ndarray,
    low_idx: int,
    dstd: float,
    weights: np.ndarray,
) -> DMMetricFitResult:
    """Refine the DMphase peak using the method's weighted polynomial fit."""
    sampled_peak_index = int(np.nanargmax(scores))
    sampled_best_dm = float(trial_dms[sampled_peak_index])
    sampled_best_score = float(scores[sampled_peak_index])

    heavy_weights = np.argwhere(scores > 0.5 * sampled_best_score)
    if len(heavy_weights) < 5:
        heavy_weights = np.argwhere(scores > 0.25 * sampled_best_score)
    if len(heavy_weights) < 5:
        heavy_weights = np.argwhere(scores > 0.1 * sampled_best_score)
    if len(heavy_weights) == 0:
        return DMMetricFitResult(
            best_dm=sampled_best_dm,
            best_score=sampled_best_score,
            best_dm_uncertainty=None,
            fit_status="dmphase_weighted_polyfit_fallback",
        )

    heavy_weights = np.asarray(heavy_weights, dtype=int).reshape(-1)
    peak_center = float(np.mean(heavy_weights))
    width = int(np.max(heavy_weights) - np.min(heavy_weights))
    start = int(heavy_weights[int(np.argmin(np.abs((peak_center - width) - heavy_weights)))])
    stop = int(heavy_weights[int(np.argmin(np.abs((peak_center + width) - heavy_weights)))])
    start = max(0, start)
    stop = min(int(scores.size), max(start + 2, stop))
    plot_range = np.arange(start, stop, dtype=int)
    if plot_range.size < 3:
        return DMMetricFitResult(
            best_dm=sampled_best_dm,
            best_score=sampled_best_score,
            best_dm_uncertainty=None,
            fit_status="dmphase_weighted_polyfit_fallback",
        )

    x = np.asarray(trial_dms[plot_range], dtype=float)
    y = np.asarray(scores[plot_range], dtype=float)
    new_weights = np.asarray(weights[plot_range], dtype=float)
    if not np.isfinite(new_weights).all() or float(np.sum(new_weights)) <= 0:
        normalized_weights = None
    else:
        normalized_weights = new_weights / float(np.sum(new_weights))

    try:
        best_dm, uncertainty, poly, mean_x = _dmphase_poly_max(x, y, float(dstd), normalized_weights)
        best_score = float(np.polyval(poly, best_dm - mean_x))
    except Exception:
        return DMMetricFitResult(
            best_dm=sampled_best_dm,
            best_score=sampled_best_score,
            best_dm_uncertainty=None,
            fit_status="dmphase_weighted_polyfit_failed",
        )

    if not np.isfinite(best_dm):
        return DMMetricFitResult(
            best_dm=sampled_best_dm,
            best_score=sampled_best_score,
            best_dm_uncertainty=None,
            fit_status="dmphase_weighted_polyfit_failed",
        )

    if not np.isfinite(best_score):
        best_score = sampled_best_score

    fit_status = "dmphase_weighted_polyfit"
    if not np.isfinite(uncertainty) or uncertainty <= 0:
        uncertainty_value: float | None = None
        fit_status = "dmphase_weighted_polyfit_uncertainty_unavailable"
    else:
        uncertainty_value = float(uncertainty)

    return DMMetricFitResult(
        best_dm=float(best_dm),
        best_score=float(best_score),
        best_dm_uncertainty=uncertainty_value,
        fit_status=fit_status,
    )


def _finalize_dmphase_scores(prepared_trials: list[DMMetricPreparedTrial]) -> DMMetricFinalizeResult:
    """Stack per-trial DMphase spectra and convert them into a score curve."""
    if not prepared_trials:
        return DMMetricFinalizeResult(scores=np.array([], dtype=float))

    if any(trial.dmphase_power_spectrum.size == 0 or trial.dmphase_channel_count < 2 for trial in prepared_trials):
        return DMMetricFinalizeResult(scores=np.full(len(prepared_trials), float("-inf"), dtype=float))

    nbin = min(int(trial.dmphase_power_spectrum.size) for trial in prepared_trials)
    if nbin < 2:
        return DMMetricFinalizeResult(scores=np.full(len(prepared_trials), float("-inf"), dtype=float))

    power_spectra = np.column_stack(
        [np.asarray(trial.dmphase_power_spectrum[:nbin], dtype=float) for trial in prepared_trials]
    )
    nchan = int(np.median([trial.dmphase_channel_count for trial in prepared_trials]))
    low_idx, up_idx = _dmphase_frequency_cutoff(power_spectra, nchan)
    if up_idx <= low_idx:
        return DMMetricFinalizeResult(scores=np.full(len(prepared_trials), float("-inf"), dtype=float))

    fluctuation_index = np.arange(nbin, dtype=float)
    dpower_spectra = power_spectra * fluctuation_index[:, np.newaxis] ** 2
    dm_curve, dm_curve_error, snr = _dmphase_curve(power_spectra, dpower_spectra, nchan)
    dm_curve = np.asarray(dm_curve, dtype=float)
    dm_curve[snr < 5.0] = dm_curve[snr < 5.0] / 1e6
    scores = np.where(np.isfinite(dm_curve), dm_curve, float("-inf"))

    weights = np.asarray(snr, dtype=float)
    weights[~np.isfinite(weights)] = 0.0
    weights[snr < 5.0] = 1e-6
    weight_sum = float(np.sum(weights))
    if weight_sum > 0 and np.isfinite(weight_sum):
        weights = weights / weight_sum

    return DMMetricFinalizeResult(
        scores=scores,
        fit_payload=DMPhaseFitInput(
            power_spectra=np.asarray(power_spectra, dtype=float),
            low_idx=int(low_idx),
            dstd=float(np.nanmax(dm_curve_error)) if dm_curve_error.size else 0.0,
            weights=np.asarray(weights, dtype=float),
        ),
    )


DM_METRIC_REGISTRY: dict[str, DMMetricAlgorithm] = {
    "integrated_event_snr": DMMetricAlgorithm(
        prepare_trial=_run_integrated_event_snr_prepare,
        finalize_scores=_finalize_scalar_scores,
    ),
    "dm_phase": DMMetricAlgorithm(
        prepare_trial=_run_dmphase_prepare,
        finalize_scores=_finalize_dmphase_scores,
    ),
}


def dm_trial_grid(center_dm: float, half_range: float, step: float) -> tuple[np.ndarray, float]:
    """Build the symmetric trial-DM grid for a sweep.

    Parameters
    ----------
    center_dm
        Center of the requested sweep, in ``pc cm^-3``.
    half_range
        Requested half-width around ``center_dm``, in ``pc cm^-3``.
    step
        Trial-DM spacing, in ``pc cm^-3``.

    Returns
    -------
    trial_dms, actual_half_range
        Rounded trial DM values and the realized half-range. The realized
        half-range can be smaller than requested when ``half_range`` is not an
        exact multiple of ``step``.

    Raises
    ------
    ValueError
        If inputs are non-finite, non-positive where required, produce fewer
        than 5 trials, or produce more than 121 trials.
    """
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
    """Return the score decrement used to quote a one-parameter DM uncertainty."""
    if metric == "integrated_event_snr":
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
    """Refine a sampled DM-score peak with a local quadratic fit.

    Parameters
    ----------
    trial_dms
        Trial DM grid in ``pc cm^-3``.
    scores
        Metric values for each trial DM. Larger values are better.
    peak_index
        Index of the best sampled trial in ``scores``.
    metric
        Metric key. Used to choose the score drop that defines the reported
        uncertainty.

    Returns
    -------
    best_dm, best_score, uncertainty, status
        Refined DM, refined score, optional uncertainty in ``pc cm^-3``, and a
        machine-readable fit status. If the local fit is invalid, the sampled
        peak is returned with an explanatory status.
    """
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
    """Summarize subband arrival residuals as RMS and slope versus frequency."""
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
    score_data: np.ndarray | None = None,
    score_current_dm: float | None = None,
    score_freqs_mhz: np.ndarray | None = None,
    score_tsamp_sec: float | None = None,
    center_dm: float,
    half_range: float,
    step: float,
    metric_input_builder: Callable[[np.ndarray], DMMetricInput],
    residuals: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray, str]],
    provenance: DmOptimizationProvenance,
    metric: str = "integrated_event_snr",
) -> DmOptimizationResult:
    """Run a DM trial sweep and return the optimized DM result.

    Parameters
    ----------
    data
        Native-resolution dynamic spectrum with shape ``(n_channels, n_time)``.
        This array is used for residual diagnostics at the applied and best-fit
        DMs.
    current_dm
        DM currently applied to ``data``, in ``pc cm^-3``.
    freqs_mhz
        Native frequency axis in MHz matching ``data`` rows.
    tsamp_sec
        Native sampling interval in seconds.
    score_data
        Optional waterfall used only for scoring. DMphase passes the current
        reduced grid here so the score respects the user's analysis resolution.
        Metrics that should score native ``data`` leave this as ``None``.
    score_current_dm
        Applied DM for ``score_data``. Defaults to ``current_dm``.
    score_freqs_mhz
        Frequency axis in MHz matching ``score_data`` rows. Defaults to
        ``freqs_mhz``.
    score_tsamp_sec
        Sampling interval in seconds for ``score_data``. Defaults to
        ``tsamp_sec``.
    center_dm, half_range, step
        Trial grid controls, all in ``pc cm^-3``.
    metric_input_builder
        Callback that converts each trial-dedispersed scoring waterfall into a
        :class:`DMMetricInput` for the selected metric.
    residuals
        Callback that computes subband arrival residuals for a waterfall and
        returns ``(freqs_mhz, arrivals_ms, residuals_ms, status)``.
    provenance
        Serialized context describing the selection and effective analysis
        resolution used for the sweep.
    metric
        Registered metric key. Supported values are defined by
        ``DM_METRIC_REGISTRY``.

    Returns
    -------
    DmOptimizationResult
        Score curve, sampled and refined DM estimates, residual diagnostics,
        settings, and provenance.

    Raises
    ------
    ValueError
        If the metric key is unsupported, the trial grid is invalid, a metric
        returns an invalid score shape, or no finite score can be computed for
        the current selection.
    """
    metric_algorithm = DM_METRIC_REGISTRY.get(metric)
    if metric_algorithm is None:
        valid = ", ".join(sorted(DM_METRIC_REGISTRY))
        raise ValueError(f"Unsupported DM metric '{metric}'. Valid metrics: {valid}.")

    trial_dms, actual_half_range = dm_trial_grid(center_dm, half_range, step)
    data = np.asarray(data, dtype=float)
    freqs_mhz = np.asarray(freqs_mhz, dtype=float)
    score_source = data if score_data is None else np.asarray(score_data, dtype=float)
    score_dm = float(current_dm if score_current_dm is None else score_current_dm)
    score_freqs = freqs_mhz if score_freqs_mhz is None else np.asarray(score_freqs_mhz, dtype=float)
    score_tsamp = float(tsamp_sec if score_tsamp_sec is None else score_tsamp_sec)

    prepared_trials: list[DMMetricPreparedTrial] = []
    for trial_dm in trial_dms:
        if np.isclose(trial_dm, score_dm):
            trial_data = score_source
        else:
            trial_data = dedisperse(score_source, float(trial_dm - score_dm), score_freqs, score_tsamp)

        metric_input = metric_input_builder(trial_data)
        prepared_trials.append(metric_algorithm.prepare_trial(metric_input))

    finalized = metric_algorithm.finalize_scores(prepared_trials)
    scores = np.asarray(finalized.scores, dtype=float)
    if scores.shape != trial_dms.shape:
        raise ValueError(f"DM metric '{metric}' returned an invalid score vector shape.")
    if not np.isfinite(scores).any():
        raise ValueError("Unable to compute DM sweep for the current selection.")

    peak_index = int(np.nanargmax(scores))
    sampled_best_dm = float(trial_dms[peak_index])
    sampled_best_score = float(scores[peak_index])
    if metric == "dm_phase" and finalized.fit_payload is not None:
        dmphase_fit = _fit_dmphase_peak(
            trial_dms,
            scores,
            finalized.fit_payload.power_spectra,
            finalized.fit_payload.low_idx,
            finalized.fit_payload.dstd,
            finalized.fit_payload.weights,
        )
        best_dm = dmphase_fit.best_dm
        best_score = dmphase_fit.best_score
        best_dm_uncertainty = dmphase_fit.best_dm_uncertainty
        fit_status = dmphase_fit.fit_status
    else:
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
    best_dm_detail = _best_dm_uncertainty_detail(
        value=best_dm_uncertainty,
        metric=metric,
        fit_status=fit_status,
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
        best_dm_uncertainty=compatible_scalar_uncertainty(best_dm_detail),
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
        uncertainty_details={"best_dm": best_dm_detail},
        settings=settings,
        provenance=provenance,
    )
