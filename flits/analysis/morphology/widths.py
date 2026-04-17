"""Width-analysis routines for selected burst profiles.

This module measures burst widths from the selected-band time profile using a
small set of complementary morphology estimators. Each method returns a
structured :class:`~flits.models.WidthResult` in milliseconds together with
quality flags, and the full set of results is bundled into a
:class:`~flits.models.WidthAnalysisSummary`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
from scipy.optimize import curve_fit

from flits.models import (
    AcceptedWidthSelection,
    NoiseEstimateSummary,
    WidthAnalysisSettings,
    WidthAnalysisSummary,
    WidthResult,
)
from flits.signal import gaussian_1d


FWHM_PER_SIGMA = float(2.0 * np.sqrt(2.0 * np.log(2.0)))


def _prepare_event_profile(
    profile: np.ndarray,
    time_axis_ms: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    tsamp_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Extract the event-window profile and convert bin starts to bin centers."""
    event = np.asarray(profile[event_rel_start:event_rel_end], dtype=float)
    event = np.where(np.isfinite(event), event, 0.0)
    event_times = np.asarray(time_axis_ms[event_rel_start:event_rel_end], dtype=float) + (float(tsamp_ms) / 2.0)
    return event, event_times


def _boxcar_width_ms(event_profile: np.ndarray, event_times_ms: np.ndarray, _: WidthAnalysisSettings) -> float | None:
    """Estimate the boxcar-equivalent width from fluence divided by peak height."""
    positive = np.clip(np.asarray(event_profile, dtype=float), a_min=0.0, a_max=None)
    peak = float(np.nanmax(positive)) if positive.size else 0.0
    if peak <= 0 or not np.isfinite(peak):
        return None
    spacing = 1.0
    if event_times_ms.size > 1:
        spacing = float(np.nanmedian(np.diff(event_times_ms)))
    return float((np.nansum(positive) / peak) * spacing)


def _fit_gaussian(
    event_profile: np.ndarray,
    event_times_ms: np.ndarray,
) -> tuple[float | None, list[str]]:
    """Fit a 1D Gaussian and return its sigma in milliseconds plus fit flags."""
    if event_profile.size < 4 or event_times_ms.size != event_profile.size:
        return None, ["insufficient_time_bins"]
    if not np.isfinite(event_profile).any():
        return None, ["non_finite_profile"]

    positive = np.clip(np.asarray(event_profile, dtype=float), a_min=0.0, a_max=None)
    if not np.any(positive > 0):
        return None, ["non_positive_profile"]

    amp_guess = float(np.nanmax(positive))
    mu_guess = float(event_times_ms[int(np.nanargmax(positive))])
    sigma_guess = float(max(np.diff(event_times_ms).min(initial=1.0), 1e-9))
    sigma_bound = float(max(event_times_ms[-1] - event_times_ms[0], sigma_guess))
    try:
        params, _ = curve_fit(
            gaussian_1d,
            event_times_ms,
            event_profile,
            p0=(amp_guess, mu_guess, sigma_guess, 0.0),
            bounds=([0.0, event_times_ms[0], 0.0, -np.inf], [np.inf, event_times_ms[-1], sigma_bound, np.inf]),
            maxfev=10000,
        )
    except Exception:
        return None, ["fit_failed"]

    sigma = float(params[2])
    if not np.isfinite(sigma) or sigma <= 0:
        return None, ["fit_failed"]
    return sigma, []


def _gaussian_sigma_ms(event_profile: np.ndarray, event_times_ms: np.ndarray, _: WidthAnalysisSettings) -> float | None:
    """Return the fitted Gaussian sigma in milliseconds."""
    sigma, _flags = _fit_gaussian(event_profile, event_times_ms)
    return sigma


def _gaussian_fwhm_ms(event_profile: np.ndarray, event_times_ms: np.ndarray, _: WidthAnalysisSettings) -> float | None:
    """Return the fitted Gaussian FWHM in milliseconds."""
    sigma, _flags = _fit_gaussian(event_profile, event_times_ms)
    if sigma is None:
        return None
    return float(sigma * FWHM_PER_SIGMA)


def _fluence_percentile_width_ms(
    event_profile: np.ndarray,
    event_times_ms: np.ndarray,
    settings: WidthAnalysisSettings,
) -> float | None:
    """Measure the width between cumulative-fluence percentiles."""
    positive = np.clip(np.asarray(event_profile, dtype=float), a_min=0.0, a_max=None)
    if positive.size == 0 or not np.any(positive > 0):
        return None

    cumulative = np.cumsum(positive)
    total = float(cumulative[-1]) if cumulative.size else 0.0
    if total <= 0 or not np.isfinite(total):
        return None

    low_target = total * (float(settings.percentile_low) / 100.0)
    high_target = total * (float(settings.percentile_high) / 100.0)
    low = float(np.interp(low_target, cumulative, event_times_ms))
    high = float(np.interp(high_target, cumulative, event_times_ms))
    if not (np.isfinite(low) and np.isfinite(high)):
        return None
    return float(max(0.0, high - low))


WIDTH_METHODS: dict[str, tuple[str, Callable[[np.ndarray, np.ndarray, WidthAnalysisSettings], float | None], str]] = {
    "boxcar_equivalent": ("Boxcar Equivalent", _boxcar_width_ms, "boxcar_equivalent_width"),
    "gaussian_sigma": ("Gaussian Sigma", _gaussian_sigma_ms, "gaussian_fit_sigma"),
    "gaussian_fwhm": ("Gaussian FWHM", _gaussian_fwhm_ms, "gaussian_fit_fwhm"),
    "fluence_percentile": ("Fluence Percentile", _fluence_percentile_width_ms, "fluence_percentile_width"),
}


def _trial_uncertainty(
    method: Callable[[np.ndarray, np.ndarray, WidthAnalysisSettings], float | None],
    *,
    event_profile: np.ndarray,
    event_times_ms: np.ndarray,
    noise_sigma: float,
    settings: WidthAnalysisSettings,
) -> tuple[float | None, list[str]]:
    """Estimate a width uncertainty from noise-perturbed Monte Carlo trials."""
    if event_profile.size == 0 or not np.isfinite(noise_sigma) or noise_sigma <= 0:
        return None, ["uncertainty_unavailable"]

    rng = np.random.default_rng(0)
    successes: list[float] = []
    for _ in range(int(settings.uncertainty_trials)):
        trial = np.asarray(event_profile, dtype=float) + rng.normal(0.0, noise_sigma, size=event_profile.shape)
        value = method(trial, event_times_ms, settings)
        if value is not None and np.isfinite(value) and value >= 0:
            successes.append(float(value))

    if len(successes) < int(settings.min_successful_trials):
        return None, ["uncertainty_unavailable", "insufficient_successful_trials"]

    return float(np.nanstd(np.asarray(successes, dtype=float), ddof=1)), []


def _result_flags(
    *,
    event_profile: np.ndarray,
    noise_summary: NoiseEstimateSummary,
    extra_flags: Sequence[str],
) -> list[str]:
    """Assemble shared quality flags for all width methods."""
    flags = list(extra_flags)
    if noise_summary.basis != "explicit":
        flags.append("implicit_offpulse")
    flags.extend(noise_summary.warning_flags)
    if event_profile.size:
        peak = float(np.nanmax(np.clip(event_profile, a_min=0.0, a_max=None)))
        if noise_summary.sigma > 0 and np.isfinite(peak) and (peak / noise_summary.sigma) < 6.0:
            flags.append("low_sn")
    return sorted(set(str(flag) for flag in flags))


def compute_width_analysis(
    *,
    selected_profile: np.ndarray,
    time_axis_ms: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    tsamp_ms: float,
    noise_summary: NoiseEstimateSummary,
    settings: WidthAnalysisSettings | None = None,
    event_window_ms: list[float],
    spectral_extent_mhz: list[float],
    offpulse_windows_ms: list[list[float]],
    masked_channels: list[int],
    effective_bandwidth_mhz: float | None,
    existing_accepted_method: str | None = None,
    extra_flags: Sequence[str] = (),
) -> WidthAnalysisSummary:
    """Compute the full set of morphology-based burst-width measurements.

    Parameters
    ----------
    selected_profile
        Selected-band time profile for the current crop, typically already in
        signal-to-noise units.
    time_axis_ms
        Time axis in milliseconds matching ``selected_profile``.
    event_rel_start, event_rel_end
        Half-open event window in profile-bin coordinates.
    tsamp_ms
        Effective time resolution of the profile in milliseconds.
    noise_summary
        Off-pulse noise estimate and warning flags associated with the current
        selection.
    settings
        Width-analysis settings. If omitted, defaults from
        :class:`WidthAnalysisSettings` are used.
    event_window_ms
        Event-window bounds in milliseconds for provenance.
    spectral_extent_mhz
        Selected spectral extent in MHz for provenance.
    offpulse_windows_ms
        Off-pulse windows in milliseconds for provenance.
    masked_channels
        Masked channel indices for provenance.
    effective_bandwidth_mhz
        Effective unmasked bandwidth in MHz, if known.
    existing_accepted_method
        Previously user-accepted width method. If present and still available,
        it is preserved as the accepted width.
    extra_flags
        Additional quality flags propagated to every method result.

    Returns
    -------
    WidthAnalysisSummary
        Structured width results for all registered methods plus the accepted
        width selection and the noise summary used to derive uncertainties.

    Notes
    -----
    All width values and uncertainties are reported in milliseconds.
    """
    settings = WidthAnalysisSettings() if settings is None else settings
    event_profile, event_times_ms = _prepare_event_profile(
        np.asarray(selected_profile, dtype=float),
        np.asarray(time_axis_ms, dtype=float),
        int(event_rel_start),
        int(event_rel_end),
        float(tsamp_ms),
    )
    results: list[WidthResult] = []
    shared_flags = _result_flags(event_profile=event_profile, noise_summary=noise_summary, extra_flags=extra_flags)

    for method_name, (label, calculator, algorithm_name) in WIDTH_METHODS.items():
        method_flags = list(shared_flags)
        value = calculator(event_profile, event_times_ms, settings)
        if value is None or not np.isfinite(value):
            method_flags.append("measurement_unavailable")
            uncertainty = None
        else:
            uncertainty, uncertainty_flags = _trial_uncertainty(
                calculator,
                event_profile=event_profile,
                event_times_ms=event_times_ms,
                noise_sigma=float(noise_summary.sigma),
                settings=settings,
            )
            method_flags.extend(uncertainty_flags)

        if method_name in {"gaussian_sigma", "gaussian_fwhm"}:
            sigma, fit_flags = _fit_gaussian(event_profile, event_times_ms)
            if sigma is None:
                method_flags.extend(fit_flags)
                if method_name == "gaussian_sigma":
                    value = None
                else:
                    value = None
                uncertainty = None

        results.append(
            WidthResult(
                method=method_name,
                label=label,
                value=None if value is None else float(value),
                uncertainty=None if uncertainty is None else float(uncertainty),
                units="ms",
                event_window_ms=[float(value) for value in event_window_ms],
                spectral_extent_mhz=[float(value) for value in spectral_extent_mhz],
                offpulse_windows_ms=[[float(value) for value in window] for window in offpulse_windows_ms],
                masked_channels=[int(value) for value in masked_channels],
                effective_bandwidth_mhz=(
                    None if effective_bandwidth_mhz is None else float(effective_bandwidth_mhz)
                ),
                algorithm_name=algorithm_name,
                quality_flags=sorted(set(method_flags)),
            )
        )

    accepted_method = existing_accepted_method or "boxcar_equivalent"
    accepted_result = next((result for result in results if result.method == accepted_method), None)
    if accepted_result is None and results:
        accepted_result = results[0]
    accepted = None if accepted_result is None else AcceptedWidthSelection.from_result(accepted_result)

    return WidthAnalysisSummary(
        settings=settings,
        results=results,
        accepted_width=accepted,
        noise_summary=noise_summary,
    )
