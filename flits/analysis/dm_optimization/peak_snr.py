"""Peak S/N of the burst against trial DM, and the Gaussian fitted to that curve.

At each trial DM the band-summed S/N profile is searched over the event window
for its highest value. ``peak_bin`` takes the highest S/N of a window one
analysis sample long. ``boxcar`` also tries longer boxcars and keeps the best,
as a matched filter. The peak S/N is highest at the DM that aligns the burst
across the band and falls as a wrong DM smears it in time.

The windows slide along the native samples rather than sitting on the analysis
grid. On a fixed grid the peak S/N also depends on where the burst falls
between two samples, and a DM change shifts the burst in time: at coarse
resolution that split the curve into two peaks with a dip at the true DM.

FLITS fits a Gaussian plus a constant to the whole curve. The Gaussian's centre
is the best DM. Its standard deviation measures how quickly the peak S/N falls
away from that DM, which depends on the burst's width and the band. The
standard deviation does not shrink as the burst gets brighter.

DM values are in ``pc cm^-3``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

PEAK_BIN = "peak_bin"
BOXCAR = "boxcar"
PEAK_SNR_FILTERS = (PEAK_BIN, BOXCAR)
DEFAULT_PEAK_SNR_FILTER = PEAK_BIN

# Independent boxcar lengths of off-pulse data needed before a smoothed
# profile's own scatter is trusted over the white-noise expectation.
MIN_INDEPENDENT_OFFPULSE_WIDTHS = 16
MIN_FIT_TRIALS = 5


def validate_peak_snr_filter(value: str) -> str:
    """Return `value` if it names a peak-S/N filter, else raise ValueError."""
    if value not in PEAK_SNR_FILTERS:
        valid = ", ".join(PEAK_SNR_FILTERS)
        raise ValueError(f"Unsupported peak S/N filter '{value}'. Valid filters: {valid}.")
    return value


def boxcar_widths(max_width: int, min_width: int = 1) -> list[int]:
    """Boxcar widths in samples from `min_width` up to `max_width`, growing by about 1.5 each step."""
    width = max(1, int(min_width))
    cap = max(width, int(max_width))
    widths: list[int] = []
    while width <= cap:
        widths.append(width)
        width = max(width + 1, int(round(width * 1.5)))
    if widths[-1] != cap:
        widths.append(cap)
    return widths


def _moving_sum(values: np.ndarray, width: int) -> np.ndarray:
    """``np.convolve(values, np.ones(width), mode="same")`` from a running sum.

    It costs the same for every width, which matters on native-resolution
    profiles where boxcars reach thousands of samples. `values` must be finite.
    """
    if width <= 1:
        return np.asarray(values, dtype=float).copy()
    count = values.size
    running = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    index = np.arange(count)
    upper = np.minimum(index + (width - 1) // 2 + 1, count)
    lower = np.maximum(index - width // 2, 0)
    return running[upper] - running[lower]


def _boxcar_profile(profile_sn: np.ndarray, width: int, offpulse_bins: np.ndarray) -> np.ndarray:
    """Matched-filter S/N of a `width`-sample boxcar centred on each sample.

    `profile_sn` is in white-noise units, so a boxcar sum divided by
    sqrt(width) is too. Slow baseline wander adds correlated noise that this
    scaling misses at wide widths. When the off-pulse data hold at least
    MIN_INDEPENDENT_OFFPULSE_WIDTHS boxcar lengths, the smoothed off-pulse
    scatter (scaled MAD) is measured, and the profile is divided by it if it
    exceeds 1. With fewer independent samples the scatter would be
    underestimated and the S/N inflated.
    """
    smoothed = _moving_sum(profile_sn, int(width)) / np.sqrt(float(width))
    offpulse = np.asarray(offpulse_bins, dtype=int)
    offpulse = offpulse[(offpulse >= 0) & (offpulse < smoothed.size)]
    if width <= 1 or offpulse.size < MIN_INDEPENDENT_OFFPULSE_WIDTHS * width:
        return smoothed
    reference = smoothed[offpulse]
    scale = 1.4826 * float(np.median(np.abs(reference - np.median(reference))))
    if not np.isfinite(scale) or scale <= 1.0:
        return smoothed
    return smoothed / scale


def profile_peak_snr(
    profile_sn: np.ndarray,
    event_start: int,
    event_end: int,
    *,
    offpulse_bins: np.ndarray,
    peak_snr_filter: str = DEFAULT_PEAK_SNR_FILTER,
    min_width: int = 1,
) -> tuple[float, int]:
    """Highest S/N inside the event window, and the window width in samples that reached it.

    Parameters
    ----------
    profile_sn
        Band-summed profile in off-pulse noise units, one value per sample.
    event_start, event_end
        Half-open event window in samples; the peak is searched only here.
    offpulse_bins
        Off-pulse sample indices, used to re-normalize smoothed profiles.
    peak_snr_filter
        ``peak_bin`` for windows `min_width` samples long, ``boxcar`` to also
        try longer boxcars up to the event window's length.
    min_width
        Shortest window, in samples. Pass the analysis downsampling factor
        with a native-resolution profile: the window then has the analysis
        resolution but slides one native sample at a time.
    """
    validate_peak_snr_filter(peak_snr_filter)
    values = np.asarray(profile_sn, dtype=float)
    start = max(0, int(event_start))
    end = min(values.size, int(event_end))
    if end <= start:
        return float("-inf"), 0
    finite = np.where(np.isfinite(values), values, 0.0)
    if not np.isfinite(values[start:end]).any():
        return float("-inf"), 0

    shortest = max(1, int(min_width))
    widths = boxcar_widths(end - start, min_width=shortest) if peak_snr_filter == BOXCAR else [shortest]
    best_snr, best_width = float("-inf"), shortest
    for width in widths:
        value = float(np.max(_boxcar_profile(finite, width, offpulse_bins)[start:end]))
        if value > best_snr:
            best_snr, best_width = value, width
    return best_snr, best_width


def _gaussian(x: np.ndarray, amplitude: float, center: float, sigma: float, baseline: float) -> np.ndarray:
    return amplitude * np.exp(-0.5 * ((x - center) / sigma) ** 2) + baseline


@dataclass(frozen=True)
class GaussianCurveFit:
    """A Gaussian plus a constant fitted to a score curve.

    `status` is ``gaussian_fit`` when the fit converged. Otherwise the
    parameters are None and the status says why. `warning_flags` note fits
    that converged but are poorly constrained by the sweep.
    """

    status: str
    amplitude: float | None = None
    center: float | None = None
    sigma: float | None = None
    baseline: float | None = None
    residual_rms: float | None = None
    warning_flags: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.center is not None

    @property
    def peak_value(self) -> float | None:
        if self.amplitude is None or self.baseline is None:
            return None
        return float(self.amplitude + self.baseline)

    @property
    def drop_one_half_width(self) -> float | None:
        """Distance from the centre at which the fitted curve has fallen by 1 in S/N."""
        if self.amplitude is None or self.sigma is None or self.amplitude <= 1.0:
            return None
        return float(self.sigma * np.sqrt(2.0 * np.log(self.amplitude / (self.amplitude - 1.0))))


def fit_gaussian_curve(trial_dms: np.ndarray, scores: np.ndarray) -> GaussianCurveFit:
    """Fit ``A exp(-(DM - DM_0)^2 / 2 sigma^2) + C`` to every finite trial of a sweep.

    Every trial is used: on simulated bursts, fitting only the trials around
    the peak let noise set the width of faint bursts' curves. The centre is
    kept inside the sweep. Flags: ``gaussian_center_on_sweep_edge``
    when it lies within one step of either end, so the far side of the peak
    was not sampled; ``gaussian_width_exceeds_sweep`` when sigma is more than half
    the sweep, so the sweep does not reach the wings and sigma trades off against
    the constant; ``gaussian_width_below_step`` when sigma is under one step, so
    only a few trials define the peak.
    """
    x_all = np.asarray(trial_dms, dtype=float)
    y_all = np.asarray(scores, dtype=float)
    finite = np.isfinite(x_all) & np.isfinite(y_all)
    order = np.argsort(x_all[finite])
    x = x_all[finite][order]
    y = y_all[finite][order]
    if x.size < MIN_FIT_TRIALS:
        return GaussianCurveFit(status="gaussian_fit_insufficient_trials")

    span = float(x[-1] - x[0])
    step = float(np.median(np.diff(x)))
    depth = float(y.max() - y.min())
    if span <= 0 or step <= 0 or depth <= 0:
        return GaussianCurveFit(status="gaussian_fit_flat_curve")

    peak = int(np.argmax(y))
    upper_half = x[y >= y.min() + 0.5 * depth]
    sigma_guess = max((float(upper_half.max() - upper_half.min()) + step) / 2.3548, 0.5 * step)
    p0 = [depth, float(x[peak]), min(sigma_guess, 5.0 * span), float(y.min())]
    lower = [0.0, float(x[0]), 1e-3 * step, -np.inf]
    upper = [np.inf, float(x[-1]), 10.0 * span, np.inf]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            popt, _ = curve_fit(_gaussian, x, y, p0=p0, bounds=(lower, upper), maxfev=20000)
    except (RuntimeError, ValueError):
        return GaussianCurveFit(status="gaussian_fit_failed")

    amplitude, center, sigma, baseline = (float(value) for value in popt)
    if not all(np.isfinite(value) for value in (amplitude, center, sigma, baseline)) or amplitude <= 0:
        return GaussianCurveFit(status="gaussian_fit_failed")

    flags: list[str] = []
    if center - float(x[0]) < step or float(x[-1]) - center < step:
        flags.append("gaussian_center_on_sweep_edge")
    if sigma > 0.5 * span:
        flags.append("gaussian_width_exceeds_sweep")
    if sigma < step:
        flags.append("gaussian_width_below_step")
    residual = y - _gaussian(x, amplitude, center, sigma, baseline)
    return GaussianCurveFit(
        status="gaussian_fit",
        amplitude=amplitude,
        center=center,
        sigma=sigma,
        baseline=baseline,
        residual_rms=float(np.sqrt(np.mean(residual**2))),
        warning_flags=flags,
    )
