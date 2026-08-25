"""Sub-burst drift-rate measurement for the selected event window.

Two independent estimators are computed, because they answer different
questions and fail in different ways:

``acf_2d``
    The mask-corrected two-dimensional autocorrelation of the dynamic
    spectrum, with a rotated Gaussian fitted to its central peak. The drift
    rate is the conditional-mean slope of that ellipse,
    ``d(nu)/dt = Sigma_{t,nu} / Sigma_{t,t}``, which is the rate at which the
    emission centroid moves in frequency as the burst proceeds. This is the
    measurement ``frbgui`` exists for, and it is the one that works on a single
    unresolved sub-burst.

``component_centroid``
    A weighted regression of each burst component's spectral centroid against
    its arrival time. This needs at least two components, and it measures the
    step-wise drift *between* sub-bursts rather than the slope *within* one.

Times are milliseconds, frequencies MHz, and a drift rate is reported in
MHz/ms with the usual sign convention: negative for the downward-drifting
"sad trombone" seen in repeaters.

Drift and DM are degenerate. A dispersion-measure error tilts the burst in the
time-frequency plane exactly the way intrinsic drift does, so every result here
carries the DM it was measured at, the sensitivity of the drift rate to that
DM, and the DM error that would on its own account for the whole measured
slope. Without a DM uncertainty the drift rate is reported as statistical-only
and is not marked publishable.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

from flits.models import DriftAnalysisResult, DriftAnalysisSettings, UncertaintyDetail
from flits.signal import DM_CONSTANT_S_MHZ2

MIN_TIME_BINS = 8
MIN_ACTIVE_CHANNELS = 8
MIN_FIT_PIXELS = 40
MIN_COMPONENTS = 2
HEAVILY_MASKED_FRACTION = 0.25
LOW_CONTRAST_RATIO = 3.0
MAX_SERIALIZED_ACF_BINS = 129

# Dispersion delay in milliseconds: t(nu) = DM_DELAY_MS_MHZ2 * DM * nu^-2.
DM_DELAY_MS_MHZ2 = 1e3 * DM_CONSTANT_S_MHZ2


@dataclass(frozen=True)
class DriftAnalysisInputs:
    """Everything the drift measurement needs from the session layer.

    Parameters
    ----------
    waterfall
        Dedispersed dynamic spectrum on the reduced analysis grid, shaped
        ``(n_channels, n_time)``. Masked channels and samples are ``NaN``.
    time_axis_ms
        Time axis of ``waterfall`` in milliseconds.
    freqs_mhz
        Channel centre frequencies in MHz. Either ordering is accepted; the
        signed channel spacing is what fixes the sign of the drift rate.
    event_rel_start, event_rel_end
        Half-open event-window bounds as column indices into ``waterfall``.
    spec_lo, spec_hi
        Inclusive selected-channel bounds as row indices into ``waterfall``.
    offpulse_bins
        Column indices used as the off-pulse reference for per-channel
        baselines and per-channel noise.
    component_windows
        ``(label, (start_bin, end_bin))`` pairs for the component-centroid
        estimator, as half-open column indices into ``waterfall``.
    dm_pc_cm3
        DM the waterfall is dedispersed at, in pc cm^-3.
    dm_uncertainty_pc_cm3
        1-sigma DM uncertainty, when one is known. Supplying it is what turns
        the drift rate from statistical-only into a publishable measurement.
    """

    waterfall: np.ndarray
    time_axis_ms: np.ndarray
    freqs_mhz: np.ndarray
    event_rel_start: int
    event_rel_end: int
    spec_lo: int
    spec_hi: int
    offpulse_bins: np.ndarray
    dm_pc_cm3: float
    component_windows: Sequence[tuple[str, tuple[int, int]]] = ()
    dm_uncertainty_pc_cm3: float | None = None


def drift_dm_sensitivity(drift_mhz_per_ms: float, reference_frequency_mhz: float) -> float:
    """Return ``d(drift)/d(DM)`` in MHz/ms per pc cm^-3.

    A DM error changes the time-frequency slope of a burst. Differentiating the
    dispersion delay gives ``d(dt/dnu)/d(DM) = -2 k nu^-3``; converting that to
    the drift rate ``d = (dt/dnu)^-1`` picks up a factor ``-d^2``.
    """
    drift = float(drift_mhz_per_ms)
    nu = float(reference_frequency_mhz)
    if not np.isfinite(drift) or not np.isfinite(nu) or nu <= 0:
        return float("nan")
    return float(2.0 * DM_DELAY_MS_MHZ2 * drift**2 / nu**3)


def dm_equivalent_of_slope(drift_mhz_per_ms: float, reference_frequency_mhz: float) -> float:
    """Return the DM error that would on its own produce ``drift_mhz_per_ms``.

    This is the honest statement of the drift/DM degeneracy: if this number is
    smaller than the DM uncertainty, the measured drift is not distinguishable
    from a dedispersion error.
    """
    drift = float(drift_mhz_per_ms)
    nu = float(reference_frequency_mhz)
    if not np.isfinite(drift) or drift == 0.0 or not np.isfinite(nu) or nu <= 0:
        return float("nan")
    return float(-(nu**3) / (2.0 * DM_DELAY_MS_MHZ2 * drift))


def _failure(
    status: str,
    message: str,
    *,
    inputs: DriftAnalysisInputs,
    tsamp_ms: float,
    freqres_mhz: float,
    event_window_ms: Sequence[float],
    spectral_extent_mhz: Sequence[float],
    settings: DriftAnalysisSettings,
    warning_flags: Sequence[str] = (),
) -> DriftAnalysisResult:
    return DriftAnalysisResult(
        status=status,
        message=message,
        method="acf_2d",
        event_window_ms=[float(event_window_ms[0]), float(event_window_ms[1])],
        spectral_extent_mhz=[float(spectral_extent_mhz[0]), float(spectral_extent_mhz[1])],
        tsamp_ms=float(tsamp_ms),
        freqres_mhz=float(freqres_mhz),
        dm_pc_cm3=float(inputs.dm_pc_cm3),
        dm_uncertainty_pc_cm3=(None if inputs.dm_uncertainty_pc_cm3 is None else float(inputs.dm_uncertainty_pc_cm3)),
        drift_rate_status="unavailable",
        component_drift_status="unavailable",
        warning_flags=sorted({str(flag) for flag in warning_flags}),
        settings=settings,
    )


def _uniform_step(values: np.ndarray) -> float:
    """Signed spacing of a monotonic axis, or NaN when it is not usable."""
    axis = np.asarray(values, dtype=float)
    if axis.size < 2:
        return float("nan")
    steps = np.diff(axis)
    finite = steps[np.isfinite(steps)]
    if finite.size == 0:
        return float("nan")
    return float(np.mean(finite))


def _channel_statistics(
    selected: np.ndarray,
    offpulse_bins: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Per-channel off-pulse baseline and sigma, plus the basis used."""
    bins = np.asarray(offpulse_bins, dtype=int)
    bins = bins[(bins >= 0) & (bins < selected.shape[1])]
    basis = "explicit_offpulse"
    reference = selected[:, np.unique(bins)] if bins.size >= 2 else selected
    if bins.size < 2:
        basis = "full_row"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        baseline = np.nanmean(reference, axis=1)
        sigma = np.nanstd(reference, axis=1)
    baseline = np.where(np.isfinite(baseline), baseline, 0.0)
    sigma = np.where(np.isfinite(sigma), sigma, 0.0)
    return np.asarray(baseline, dtype=float), np.asarray(sigma, dtype=float), basis


def _lag_axes(n_freq: int, n_time: int, freq_step_mhz: float, tsamp_ms: float) -> tuple[np.ndarray, np.ndarray]:
    freq_lags = np.arange(-(n_freq - 1), n_freq, dtype=float) * float(freq_step_mhz)
    time_lags = np.arange(-(n_time - 1), n_time, dtype=float) * float(tsamp_ms)
    return freq_lags, time_lags


def _linear_autocorrelation(field: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Full linear 2D autocorrelation, lags ordered from most negative to most positive."""
    spectrum = np.fft.rfft2(field, s=shape)
    correlation = np.fft.irfft2(spectrum * np.conj(spectrum), s=shape)
    rolled = np.roll(correlation, (field.shape[0] - 1, field.shape[1] - 1), axis=(0, 1))
    return np.asarray(rolled[: 2 * field.shape[0] - 1, : 2 * field.shape[1] - 1], dtype=float)


def _overlap_counts(valid: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Valid-pair counts per lag, and the counts an unmasked window would give."""
    observed = _linear_autocorrelation(valid.astype(float), shape)
    n_freq, n_time = valid.shape
    freq_full = (n_freq - np.abs(np.arange(-(n_freq - 1), n_freq))).astype(float)
    time_full = (n_time - np.abs(np.arange(-(n_time - 1), n_time))).astype(float)
    expected = np.outer(freq_full, time_full)
    return observed, expected


def _gaussian_2d_model(
    lags: tuple[np.ndarray, np.ndarray],
    amplitude: float,
    log_a: float,
    rho_raw: float,
    log_c: float,
    offset: float,
) -> np.ndarray:
    """Rotated 2D Gaussian written so every parameter is unconstrained.

    The quadratic form is ``a dt^2 + 2 b dt dnu + c dnu^2`` with
    ``b = tanh(rho_raw) * sqrt(a c)``, which keeps it positive definite for any
    real parameter vector and makes the drift rate ``-b/c`` a closed-form
    function of three of them.
    """
    time_lag, freq_lag = lags
    a = np.exp(log_a)
    c = np.exp(log_c)
    b = np.tanh(rho_raw) * np.sqrt(a * c)
    quadratic = a * time_lag**2 + 2.0 * b * time_lag * freq_lag + c * freq_lag**2
    return amplitude * np.exp(-np.clip(quadratic, 0.0, 700.0)) + offset


def _drift_from_params(log_a: float, rho_raw: float, log_c: float) -> float:
    """``-b/c`` for the parametrization used by :func:`_gaussian_2d_model`."""
    return float(-np.tanh(rho_raw) * np.exp(0.5 * (log_a - log_c)))


def _drift_jacobian(log_a: float, rho_raw: float, log_c: float) -> np.ndarray:
    """Gradient of the drift rate with respect to ``(A, log_a, rho_raw, log_c, C)``."""
    drift = _drift_from_params(log_a, rho_raw, log_c)
    scale = np.exp(0.5 * (log_a - log_c))
    d_rho = -scale / np.cosh(rho_raw) ** 2
    return np.array([0.0, 0.5 * drift, d_rho, -0.5 * drift, 0.0], dtype=float)


def _initial_guess(
    acf: np.ndarray,
    time_lag: np.ndarray,
    freq_lag: np.ndarray,
) -> np.ndarray:
    offset = float(np.nanmedian(acf))
    peak = float(np.nanmax(acf))
    amplitude = peak - offset
    if not np.isfinite(amplitude) or amplitude <= 0:
        amplitude = max(abs(peak), 1.0)

    weights = np.clip(acf - offset, 0.0, None)
    weights = np.where(np.isfinite(weights), weights, 0.0)
    total = float(weights.sum())
    if total > 0:
        var_t = float((weights * time_lag**2).sum() / total)
        var_f = float((weights * freq_lag**2).sum() / total)
        cov_tf = float((weights * time_lag * freq_lag).sum() / total)
    else:
        var_t = var_f = cov_tf = 0.0

    span_t = float(np.max(np.abs(time_lag))) or 1.0
    span_f = float(np.max(np.abs(freq_lag))) or 1.0
    var_t = var_t if var_t > 0 else (0.25 * span_t) ** 2
    var_f = var_f if var_f > 0 else (0.25 * span_f) ** 2
    correlation = cov_tf / np.sqrt(var_t * var_f) if var_t > 0 and var_f > 0 else 0.0
    correlation = float(np.clip(correlation, -0.95, 0.95))

    # Sigma_acf = [[var_t, cov], [cov, var_f]] implies the quadratic-form
    # coefficients a = 1 / (2 var_t (1 - r^2)) and c = 1 / (2 var_f (1 - r^2)).
    denominator = 2.0 * (1.0 - correlation**2)
    a = 1.0 / (denominator * var_t)
    c = 1.0 / (denominator * var_f)
    return np.array(
        [amplitude, float(np.log(a)), float(np.arctanh(-correlation)), float(np.log(c)), offset],
        dtype=float,
    )


def _fit_acf_gaussian(
    acf_values: np.ndarray,
    time_lag: np.ndarray,
    freq_lag: np.ndarray,
    guess: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=OptimizeWarning)
            warnings.simplefilter("ignore", category=RuntimeWarning)
            popt, pcov = curve_fit(
                _gaussian_2d_model,
                (time_lag, freq_lag),
                acf_values,
                p0=guess,
                maxfev=20000,
            )
    except (RuntimeError, ValueError, TypeError):
        return None, None
    if not np.all(np.isfinite(popt)):
        return None, None
    covariance = np.asarray(pcov, dtype=float) if pcov is not None else None
    if covariance is not None and not np.all(np.isfinite(covariance)):
        covariance = None
    return np.asarray(popt, dtype=float), covariance


def _decimate_for_transport(values: np.ndarray, limit: int) -> tuple[np.ndarray, int]:
    """Thin an axis to at most ``limit`` samples, keeping the centre sample."""
    size = int(values.size)
    if size <= limit:
        return np.arange(size, dtype=int), 1
    stride = int(np.ceil(size / limit))
    centre = size // 2
    offsets = np.arange(-(centre // stride), (size - centre) // stride + 1, dtype=int)
    indices = centre + offsets * stride
    return indices[(indices >= 0) & (indices < size)], stride


def _component_centroids(
    selected: np.ndarray,
    baseline: np.ndarray,
    sigma: np.ndarray,
    freqs_mhz: np.ndarray,
    time_axis_ms: np.ndarray,
    component_windows: Sequence[tuple[str, tuple[int, int]]],
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    """Spectral centroid and arrival time of each component, with uncertainties."""
    labels: list[str] = []
    times: list[float] = []
    time_errors: list[float] = []
    freqs: list[float] = []
    freq_errors: list[float] = []

    active = np.isfinite(sigma) & (sigma > 0)
    for label, (start, end) in component_windows:
        lo = max(0, int(start))
        hi = min(selected.shape[1], int(end))
        if hi - lo < 2:
            continue
        block = selected[:, lo:hi] - baseline[:, None]
        n_bins = hi - lo

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            spectrum = np.nanmean(block, axis=1)
        spectrum = np.where(np.isfinite(spectrum), spectrum, 0.0)
        spectrum_sigma = np.where(active, sigma / np.sqrt(n_bins), 0.0)

        weights = np.clip(np.where(active, spectrum, 0.0), 0.0, None)
        weight_sum = float(weights.sum())
        if weight_sum <= 0:
            continue
        centre_freq = float((weights * freqs_mhz).sum() / weight_sum)
        contributing = weights > 0
        freq_error = float(
            np.sqrt(
                np.sum(((freqs_mhz[contributing] - centre_freq) / weight_sum) ** 2 * spectrum_sigma[contributing] ** 2)
            )
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            profile = np.nanmean(block, axis=0)
        profile = np.where(np.isfinite(profile), profile, 0.0)
        channel_count = int(np.count_nonzero(np.isfinite(block).any(axis=1)))
        profile_sigma = float(np.sqrt(np.sum(sigma[active] ** 2)) / max(channel_count, 1)) if np.any(active) else 0.0
        profile_weights = np.clip(profile, 0.0, None)
        profile_sum = float(profile_weights.sum())
        if profile_sum <= 0:
            continue
        window_times = time_axis_ms[lo:hi]
        centre_time = float((profile_weights * window_times).sum() / profile_sum)
        contributing_times = profile_weights > 0
        time_error = float(
            np.sqrt(np.sum(((window_times[contributing_times] - centre_time) / profile_sum) ** 2)) * profile_sigma
        )

        labels.append(str(label))
        times.append(centre_time)
        time_errors.append(time_error)
        freqs.append(centre_freq)
        freq_errors.append(freq_error)

    if len(labels) < MIN_COMPONENTS:
        empty = np.array([], dtype=float)
        return [], empty, empty, empty, empty, "insufficient_components"

    order = np.argsort(np.asarray(times, dtype=float))
    return (
        [labels[index] for index in order],
        np.asarray(times, dtype=float)[order],
        np.asarray(time_errors, dtype=float)[order],
        np.asarray(freqs, dtype=float)[order],
        np.asarray(freq_errors, dtype=float)[order],
        "ok",
    )


def _regress_component_drift(
    times_ms: np.ndarray,
    time_errors_ms: np.ndarray,
    freqs_mhz: np.ndarray,
    freq_errors_mhz: np.ndarray,
) -> tuple[float | None, float | None, float | None, str]:
    """Weighted regression of centroid frequency on arrival time."""
    count = int(times_ms.size)
    if count < MIN_COMPONENTS:
        return None, None, None, "insufficient_components"
    if float(np.ptp(times_ms)) <= 0:
        return None, None, None, "degenerate_arrival_times"

    design = np.column_stack([times_ms, np.ones(count, dtype=float)])
    slope = float(np.polyfit(times_ms, freqs_mhz, 1)[0])
    covariance = None
    for _ in range(3):
        variance = freq_errors_mhz**2 + (slope * time_errors_ms) ** 2
        variance = np.where(np.isfinite(variance) & (variance > 0), variance, np.nan)
        if not np.all(np.isfinite(variance)):
            variance = np.ones(count, dtype=float)
        weights = 1.0 / variance
        normal = design.T @ (design * weights[:, None])
        try:
            covariance = np.linalg.inv(normal)
        except np.linalg.LinAlgError:
            return None, None, None, "singular_regression"
        solution = covariance @ (design.T @ (freqs_mhz * weights))
        slope = float(solution[0])
        intercept = float(solution[1])

    if covariance is None or not np.isfinite(slope):
        return None, None, None, "singular_regression"

    uncertainty = float(np.sqrt(abs(covariance[0, 0])))
    model = slope * times_ms + intercept
    total = float(np.sum((freqs_mhz - np.mean(freqs_mhz)) ** 2))
    residual = float(np.sum((freqs_mhz - model) ** 2))
    r_squared = None if total <= 0 or count < 3 else float(1.0 - residual / total)
    status = "ok" if count > 2 else "exactly_two_components"
    return slope, uncertainty, r_squared, status


def _drift_uncertainty_detail(
    *,
    value: float | None,
    classification: str,
    basis: str,
    tooltip: str,
    publishable: bool,
    warning_flags: Sequence[str],
) -> UncertaintyDetail:
    finite = None if value is None or not np.isfinite(value) else float(value)
    return UncertaintyDetail(
        value=finite,
        units="MHz/ms",
        classification=classification,
        is_formal_1sigma=(classification == "formal_1sigma"),
        publishable=bool(publishable),
        basis=str(basis),
        tooltip=str(tooltip),
        warning_flags=sorted({str(flag) for flag in warning_flags}),
    )


def _combine_with_dm(
    statistical: float | None,
    drift: float | None,
    reference_frequency_mhz: float | None,
    dm_uncertainty: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Return ``(dm_sensitivity, dm_systematic, combined_uncertainty)``."""
    if drift is None or reference_frequency_mhz is None or not np.isfinite(reference_frequency_mhz):
        return None, None, statistical
    sensitivity = drift_dm_sensitivity(drift, reference_frequency_mhz)
    if not np.isfinite(sensitivity):
        return None, None, statistical
    if dm_uncertainty is None or not np.isfinite(dm_uncertainty) or dm_uncertainty < 0:
        return float(sensitivity), None, statistical
    systematic = float(abs(sensitivity) * float(dm_uncertainty))
    if statistical is None or not np.isfinite(statistical):
        return float(sensitivity), systematic, systematic
    return float(sensitivity), systematic, float(np.hypot(statistical, systematic))


def run_drift_analysis(
    inputs: DriftAnalysisInputs,
    settings: DriftAnalysisSettings | None = None,
) -> DriftAnalysisResult:
    """Measure the sub-burst drift rate of the selected event window.

    Parameters
    ----------
    inputs
        Waterfall, axes, selection bounds and DM state; see
        :class:`DriftAnalysisInputs`.
    settings
        Fit-region, Monte-Carlo and seeding controls. Defaults are used when
        omitted.

    Returns
    -------
    DriftAnalysisResult
        ``status`` is ``"ok"`` when the 2D-ACF fit converged, and otherwise one
        of ``"insufficient_time_bins"``, ``"insufficient_channels"``,
        ``"insufficient_signal"``, ``"invalid_axes"`` or ``"fit_failed"``. The
        component-centroid estimator reports separately through
        ``component_drift_status`` and never blocks the primary result.

    Notes
    -----
    The statistical uncertainty comes from a seeded Monte Carlo: each trial
    adds an independent noise realisation at the measured per-channel off-pulse
    sigma and repeats the autocorrelation and the fit. That propagates the
    correlation between neighbouring autocorrelation pixels, which the fit
    covariance alone does not; the covariance value is kept alongside as a
    diagnostic. The result is publishable only when a DM uncertainty was
    supplied, because the DM systematic is usually the larger term.
    """
    settings = DriftAnalysisSettings() if settings is None else settings
    waterfall = np.asarray(inputs.waterfall, dtype=float)
    time_axis_ms = np.asarray(inputs.time_axis_ms, dtype=float)
    freqs_mhz = np.asarray(inputs.freqs_mhz, dtype=float)

    spec_lo, spec_hi = sorted((int(inputs.spec_lo), int(inputs.spec_hi)))
    event_start = max(0, int(inputs.event_rel_start))
    event_end = min(waterfall.shape[1] if waterfall.ndim == 2 else 0, int(inputs.event_rel_end))

    tsamp_ms = _uniform_step(time_axis_ms)
    freq_step_mhz = _uniform_step(freqs_mhz)
    selected_freqs = freqs_mhz[spec_lo : spec_hi + 1] if freqs_mhz.size else freqs_mhz
    event_window_ms = (
        (float(time_axis_ms[event_start]), float(time_axis_ms[event_end - 1]))
        if time_axis_ms.size and event_end > event_start
        else (0.0, 0.0)
    )
    spectral_extent_mhz = (
        (float(np.min(selected_freqs)), float(np.max(selected_freqs))) if selected_freqs.size else (0.0, 0.0)
    )
    fail = lambda status, message, flags=(): _failure(  # noqa: E731 - local shorthand for one function
        status,
        message,
        inputs=inputs,
        tsamp_ms=0.0 if not np.isfinite(tsamp_ms) else tsamp_ms,
        freqres_mhz=0.0 if not np.isfinite(freq_step_mhz) else abs(freq_step_mhz),
        event_window_ms=event_window_ms,
        spectral_extent_mhz=spectral_extent_mhz,
        settings=settings,
        warning_flags=flags,
    )

    if waterfall.ndim != 2 or waterfall.size == 0:
        return fail("insufficient_signal", "The selection does not contain a dynamic spectrum to correlate.")
    if not np.isfinite(tsamp_ms) or tsamp_ms <= 0 or not np.isfinite(freq_step_mhz) or freq_step_mhz == 0:
        return fail("invalid_axes", "The time and frequency axes must both be uniform and non-degenerate.")
    if event_end - event_start < MIN_TIME_BINS:
        return fail(
            "insufficient_time_bins",
            f"The event window spans fewer than {MIN_TIME_BINS} time bins, which cannot constrain a 2D fit.",
        )

    selected = np.asarray(waterfall[spec_lo : spec_hi + 1, :], dtype=float)
    if selected.shape[0] < MIN_ACTIVE_CHANNELS:
        return fail(
            "insufficient_channels",
            f"The spectral selection spans fewer than {MIN_ACTIVE_CHANNELS} channels.",
        )

    baseline, sigma, noise_basis = _channel_statistics(selected, np.asarray(inputs.offpulse_bins, dtype=int))
    event_block = selected[:, event_start:event_end] - baseline[:, None]
    valid = np.isfinite(event_block)
    active_channels = valid.any(axis=1)
    active_count = int(active_channels.sum())
    masked_fraction = float(1.0 - active_count / max(selected.shape[0], 1))
    if active_count < MIN_ACTIVE_CHANNELS:
        return fail(
            "insufficient_channels",
            f"Fewer than {MIN_ACTIVE_CHANNELS} channels survive masking inside the event window.",
        )

    field = np.where(valid, event_block, 0.0)
    n_freq, n_time = field.shape
    fft_shape = (2 * n_freq, 2 * n_time)
    observed_overlap, expected_overlap = _overlap_counts(valid, fft_shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        overlap_ratio = np.where(expected_overlap > 0, observed_overlap / expected_overlap, 0.0)

    freq_lag_axis, time_lag_axis = _lag_axes(n_freq, n_time, freq_step_mhz, tsamp_ms)
    max_freq_lag = max(1, int(round(settings.max_lag_fraction * (n_freq - 1))))
    max_time_lag = max(1, int(round(settings.max_lag_fraction * (n_time - 1))))
    freq_slice = slice(n_freq - 1 - max_freq_lag, n_freq + max_freq_lag)
    time_slice = slice(n_time - 1 - max_time_lag, n_time + max_time_lag)

    region_freq_lag = freq_lag_axis[freq_slice]
    region_time_lag = time_lag_axis[time_slice]
    region_ratio = overlap_ratio[freq_slice, time_slice]
    freq_grid, time_grid = np.meshgrid(region_freq_lag, region_time_lag, indexing="ij")

    usable = region_ratio >= float(settings.min_overlap_fraction)
    if settings.exclude_zero_lag:
        usable[max_freq_lag, max_time_lag] = False
    if int(usable.sum()) < MIN_FIT_PIXELS:
        return fail(
            "insufficient_signal",
            "Too few autocorrelation lags survive the channel mask to constrain a 2D fit.",
            ("heavily_masked",),
        )

    def correlate(sample: np.ndarray) -> np.ndarray:
        raw = _linear_autocorrelation(sample, fft_shape)[freq_slice, time_slice]
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(region_ratio > 0, raw / region_ratio, 0.0)

    acf = correlate(field)
    fit_time = time_grid[usable]
    fit_freq = freq_grid[usable]
    fit_values = acf[usable]

    guess = _initial_guess(fit_values, fit_time, fit_freq)
    popt, pcov = _fit_acf_gaussian(fit_values, fit_time, fit_freq, guess)
    if popt is None:
        return fail("fit_failed", "The rotated 2D Gaussian did not converge on the autocorrelation peak.")

    amplitude, log_a, rho_raw, log_c, offset = (float(value) for value in popt)
    drift = _drift_from_params(log_a, rho_raw, log_c)
    correlation = float(-np.tanh(rho_raw))
    shape_factor = max(1.0 - correlation**2, 1e-12)
    sigma_time_ms = float(0.5 / np.sqrt(np.exp(log_a) * shape_factor))
    sigma_freq_mhz = float(0.5 / np.sqrt(np.exp(log_c) * shape_factor))

    # Ellipse orientation in the conventional (ms, MHz) plane. The major-axis
    # slope is what frbgui reports; it is not the same number as the
    # conditional-mean drift rate unless the ellipse is very elongated, and it
    # depends on the choice of axis units.
    covariance_tt = sigma_time_ms**2
    covariance_ff = sigma_freq_mhz**2
    covariance_tf = correlation * sigma_time_ms * sigma_freq_mhz
    theta_rad = 0.5 * float(np.arctan2(2.0 * covariance_tf, covariance_tt - covariance_ff))
    major_axis_slope = float(np.tan(theta_rad)) if abs(np.cos(theta_rad)) > 1e-12 else float("inf")

    fit_covariance_error = None
    if pcov is not None:
        jacobian = _drift_jacobian(log_a, rho_raw, log_c)
        variance = float(jacobian @ np.asarray(pcov, dtype=float) @ jacobian)
        if np.isfinite(variance) and variance >= 0:
            fit_covariance_error = float(np.sqrt(variance))

    warning_flags: list[str] = []
    if masked_fraction >= HEAVILY_MASKED_FRACTION:
        warning_flags.append("heavily_masked")
    if noise_basis != "explicit_offpulse":
        warning_flags.append("implicit_offpulse")
    peak_contrast = abs(amplitude) / max(
        float(np.nanstd(fit_values - _gaussian_2d_model((fit_time, fit_freq), *popt))), 1e-12
    )
    if peak_contrast < LOW_CONTRAST_RATIO:
        warning_flags.append("low_acf_contrast")

    statistical_error, trials_used = _monte_carlo_uncertainty(
        field=field,
        valid=valid,
        sigma=sigma,
        correlate=correlate,
        usable=usable,
        fit_time=fit_time,
        fit_freq=fit_freq,
        popt=popt,
        settings=settings,
    )
    if trials_used == 0:
        warning_flags.append("monte_carlo_unavailable")

    reference_frequency_mhz = float(np.mean(selected_freqs[active_channels])) if active_count else None
    dm_uncertainty = None if inputs.dm_uncertainty_pc_cm3 is None else abs(float(inputs.dm_uncertainty_pc_cm3))
    sensitivity, dm_systematic, combined_error = _combine_with_dm(
        statistical_error, drift, reference_frequency_mhz, dm_uncertainty
    )
    raw_dm_equivalent = (
        dm_equivalent_of_slope(drift, reference_frequency_mhz) if reference_frequency_mhz is not None else float("nan")
    )
    dm_equivalent = None if not np.isfinite(raw_dm_equivalent) else float(raw_dm_equivalent)
    if dm_uncertainty is not None and dm_equivalent is not None and abs(dm_equivalent) <= dm_uncertainty:
        warning_flags.append("drift_consistent_with_dm_error")
    if dm_uncertainty is None:
        warning_flags.append("missing_dm_uncertainty")

    (
        component_labels,
        component_times,
        component_time_errors,
        component_freqs,
        component_freq_errors,
        component_status,
    ) = _component_centroids(
        selected,
        baseline,
        sigma,
        selected_freqs,
        time_axis_ms,
        inputs.component_windows,
    )
    component_drift = component_drift_error = component_r_squared = None
    if component_status == "ok":
        (
            component_drift,
            component_drift_error,
            component_r_squared,
            component_status,
        ) = _regress_component_drift(
            component_times,
            component_time_errors,
            component_freqs,
            component_freq_errors,
        )
    component_combined_error = component_drift_error
    if component_drift is not None:
        _, _, component_combined_error = _combine_with_dm(
            component_drift_error, component_drift, reference_frequency_mhz, dm_uncertainty
        )

    freq_indices, _ = _decimate_for_transport(region_freq_lag, MAX_SERIALIZED_ACF_BINS)
    time_indices, _ = _decimate_for_transport(region_time_lag, MAX_SERIALIZED_ACF_BINS)
    transport = np.ix_(freq_indices, time_indices)
    model_surface = _gaussian_2d_model((time_grid, freq_grid), *popt)

    drift_status = "ok"
    if (
        statistical_error is not None
        and np.isfinite(statistical_error)
        and drift != 0
        and abs(drift) < abs(statistical_error)
    ):
        drift_status = "unconstrained"

    uncertainty_details = _build_uncertainty_details(
        statistical_error=statistical_error,
        combined_error=combined_error,
        dm_uncertainty=dm_uncertainty,
        warning_flags=warning_flags,
        component_error=component_drift_error,
        component_combined_error=component_combined_error,
        component_status=component_status,
        trials_used=trials_used,
    )

    return DriftAnalysisResult(
        status="ok",
        message=_summary_message(drift, combined_error, dm_uncertainty, dm_equivalent),
        method="acf_2d",
        event_window_ms=[float(event_window_ms[0]), float(event_window_ms[1])],
        spectral_extent_mhz=[float(spectral_extent_mhz[0]), float(spectral_extent_mhz[1])],
        tsamp_ms=float(tsamp_ms),
        freqres_mhz=float(abs(freq_step_mhz)),
        dm_pc_cm3=float(inputs.dm_pc_cm3),
        dm_uncertainty_pc_cm3=dm_uncertainty,
        reference_frequency_mhz=reference_frequency_mhz,
        drift_rate_mhz_per_ms=float(drift),
        drift_rate_uncertainty_mhz_per_ms=combined_error,
        drift_rate_status=drift_status,
        drift_rate_statistical_mhz_per_ms=statistical_error,
        drift_rate_fit_covariance_mhz_per_ms=fit_covariance_error,
        drift_rate_dm_systematic_mhz_per_ms=dm_systematic,
        dm_sensitivity_mhz_per_ms_per_pc_cm3=sensitivity,
        dm_equivalent_pc_cm3=dm_equivalent,
        acf_amplitude=float(amplitude),
        acf_offset=float(offset),
        acf_sigma_time_ms=sigma_time_ms,
        acf_sigma_freq_mhz=sigma_freq_mhz,
        acf_correlation=correlation,
        acf_theta_deg=float(np.degrees(theta_rad)),
        acf_major_axis_slope_mhz_per_ms=major_axis_slope,
        acf_lag_time_ms=np.asarray(region_time_lag[time_indices], dtype=float),
        acf_lag_freq_mhz=np.asarray(region_freq_lag[freq_indices], dtype=float),
        acf=np.asarray(acf[transport], dtype=float),
        acf_model=np.asarray(model_surface[transport], dtype=float),
        monte_carlo_trials_used=int(trials_used),
        masked_channel_fraction=masked_fraction,
        component_labels=list(component_labels),
        component_times_ms=np.asarray(component_times, dtype=float),
        component_freqs_mhz=np.asarray(component_freqs, dtype=float),
        component_freq_uncertainty_mhz=np.asarray(component_freq_errors, dtype=float),
        component_drift_rate_mhz_per_ms=component_drift,
        component_drift_uncertainty_mhz_per_ms=component_combined_error,
        component_drift_status=component_status,
        component_drift_r_squared=component_r_squared,
        warning_flags=sorted(set(warning_flags)),
        uncertainty_details=uncertainty_details,
        settings=settings,
    )


def _monte_carlo_uncertainty(
    *,
    field: np.ndarray,
    valid: np.ndarray,
    sigma: np.ndarray,
    correlate: Callable[[np.ndarray], np.ndarray],
    usable: np.ndarray,
    fit_time: np.ndarray,
    fit_freq: np.ndarray,
    popt: np.ndarray,
    settings: DriftAnalysisSettings,
) -> tuple[float | None, int]:
    """Scatter of the drift rate across seeded noise realisations.

    Adding one further realisation at the measured per-channel sigma perturbs
    the fit by the same amount the noise already present does, so the spread of
    the refitted drift rates estimates its 1-sigma statistical error. The
    estimate is robust (scaled median absolute deviation) so that an occasional
    non-converged trial does not inflate it.
    """
    trials = max(0, int(settings.monte_carlo_trials))
    channel_sigma = np.where(np.isfinite(sigma) & (sigma > 0), sigma, 0.0)
    if trials < 4 or not np.any(channel_sigma > 0):
        return None, 0

    generator = np.random.default_rng(int(settings.random_seed))
    samples: list[float] = []
    for _ in range(trials):
        noise = generator.standard_normal(field.shape) * channel_sigma[:, None]
        perturbed = np.where(valid, field + noise, 0.0)
        trial_acf = correlate(perturbed)
        trial_popt, _ = _fit_acf_gaussian(trial_acf[usable], fit_time, fit_freq, popt)
        if trial_popt is None:
            continue
        value = _drift_from_params(float(trial_popt[1]), float(trial_popt[2]), float(trial_popt[3]))
        if np.isfinite(value):
            samples.append(value)

    if len(samples) < 4:
        return None, len(samples)
    values = np.asarray(samples, dtype=float)
    spread = float(np.median(np.abs(values - np.median(values))) * 1.4826)
    if not np.isfinite(spread) or spread <= 0:
        spread = float(np.std(values))
    return (float(spread) if np.isfinite(spread) and spread > 0 else None), len(samples)


def _build_uncertainty_details(
    *,
    statistical_error: float | None,
    combined_error: float | None,
    dm_uncertainty: float | None,
    warning_flags: Sequence[str],
    component_error: float | None,
    component_combined_error: float | None,
    component_status: str,
    trials_used: int,
) -> dict[str, UncertaintyDetail]:
    blocking = {"heavily_masked", "low_acf_contrast", "monte_carlo_unavailable", "implicit_offpulse"}
    publishable = dm_uncertainty is not None and not (set(warning_flags) & blocking)
    classification = "formal_1sigma" if dm_uncertainty is not None else "statistical_only"
    details: dict[str, UncertaintyDetail] = {}

    if statistical_error is not None or combined_error is not None:
        details["drift_rate_mhz_per_ms"] = _drift_uncertainty_detail(
            value=combined_error if combined_error is not None else statistical_error,
            classification=classification,
            basis=(
                f"Monte-Carlo scatter of the 2D-ACF drift rate over {trials_used} seeded noise realisations"
                + (
                    ", combined in quadrature with the DM systematic propagated through the drift/DM sensitivity."
                    if dm_uncertainty is not None
                    else ". No DM uncertainty was supplied, so the DM systematic is missing."
                )
            ),
            tooltip=(
                "Drift and DM are degenerate: a dedispersion error tilts the burst the same way intrinsic drift "
                "does. The bar is publishable only once a DM uncertainty is supplied and folded in."
            ),
            publishable=publishable,
            warning_flags=warning_flags,
        )

    if component_error is not None or component_combined_error is not None:
        details["component_drift_rate_mhz_per_ms"] = _drift_uncertainty_detail(
            value=component_combined_error if component_combined_error is not None else component_error,
            classification=classification,
            basis=(
                "Weighted regression of component spectral centroids against arrival times"
                + (
                    ", combined in quadrature with the DM systematic."
                    if dm_uncertainty is not None
                    else ". No DM uncertainty was supplied, so the DM systematic is missing."
                )
            ),
            tooltip=(
                "Inter-component drift from the centroid regression. It shares the DM degeneracy of the ACF "
                "estimator and is publishable only with a DM uncertainty folded in."
            ),
            publishable=publishable and component_status in {"ok", "exactly_two_components"},
            warning_flags=[*warning_flags, component_status],
        )
    return details


def _summary_message(
    drift: float,
    uncertainty: float | None,
    dm_uncertainty: float | None,
    dm_equivalent: float | None,
) -> str:
    if uncertainty is not None and np.isfinite(uncertainty):
        headline = f"Drift rate {drift:+.4g} +/- {uncertainty:.3g} MHz/ms."
    else:
        headline = f"Drift rate {drift:+.4g} MHz/ms (no uncertainty available)."
    if dm_equivalent is None or not np.isfinite(dm_equivalent):
        return headline
    degeneracy = f" A DM offset of {dm_equivalent:+.4g} pc cm^-3 would produce the same slope."
    if dm_uncertainty is None:
        return headline + degeneracy + " Supply a DM uncertainty to fold the systematic in."
    if abs(dm_equivalent) <= dm_uncertainty:
        return headline + degeneracy + " That is within the stated DM uncertainty, so the drift is not resolved."
    return headline + degeneracy
