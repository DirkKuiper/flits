"""Find interference-dominated channels from statistics of their off-burst samples.

For each channel, a statistic of its samples over time is computed: the
skewness, which interference that comes and goes pushes away from zero, or the
standard deviation. Across channels these statistics follow the receiver
bandpass, so a running median over neighbouring channels is subtracted, and a
channel is flagged when its residual lies more than `sigma` standard deviations
from the median residual. The standard deviation is estimated from the median
absolute deviation, so the flagged channels do not inflate it.

Within half a window of a band edge the running median would be one-sided,
so the trend there is a straight line fitted robustly (Theil-Sen) to the outer
channels instead. The trend is re-estimated without the flagged channels until
the flags stop changing, so a block of interference, at an edge or inside the
band, cannot pull the trend towards itself.

This follows the statistical channel tests of jess (Kania et al.,
https://github.com/josephwkania/jess), which FLITS used before.
"""

from __future__ import annotations

import numpy as np

CHANNEL_TESTS = ("skew", "stand-dev")
DEFAULT_SIGMA = 3.0
# Running-median width across channels for the bandpass trend.
TREND_CHANNELS = 31
MAX_ITERATIONS = 5
_MAD_TO_SIGMA = 1.4826


def channel_statistic(spectra: np.ndarray, test: str) -> np.ndarray:
    """Return one statistic per channel of a ``(time, channel)`` array.

    Sums are accumulated in float64. The standard deviation squares float32
    deviations, which is accurate to ~1e-9 and twice as fast; the skewness
    cubes deviations, so it works in float64 throughout.
    """
    values = np.asarray(spectra)
    if not np.issubdtype(values.dtype, np.floating):
        values = values.astype(np.float64)
    mean = values.mean(axis=0, dtype=np.float64)
    if test == "stand-dev":
        squares = values - mean.astype(values.dtype)
        squares *= squares
        return np.sqrt(squares.mean(axis=0, dtype=np.float64))
    if test == "skew":
        centred = values - mean
        powers = centred * centred
        variance = powers.mean(axis=0)
        powers *= centred
        third = powers.mean(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            skewness = third / variance**1.5
        return np.where(variance > 0, skewness, 0.0)
    raise ValueError(f"Unknown channel test {test!r}; expected one of {', '.join(CHANNEL_TESTS)}.")


def running_median(values: np.ndarray, width: int) -> np.ndarray:
    """Median of each value's neighbours within `width`, ignoring NaN.

    Windows are truncated at the ends rather than padded. A window holding only
    NaN gives NaN.
    """
    values = np.asarray(values, dtype=np.float64)
    half = max(0, int(width) // 2)
    padded = np.concatenate([np.full(half, np.nan), values, np.full(half, np.nan)])
    windows = np.sort(np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1), axis=1)
    count = np.count_nonzero(~np.isnan(windows), axis=1)
    rows = np.arange(values.size)
    low = windows[rows, np.maximum(count - 1, 0) // 2]
    high = windows[rows, count // 2]
    return np.where(count > 0, (low + high) / 2, np.nan)


def _theil_sen_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Slope and intercept of the median-of-pairwise-slopes line through the finite points."""
    finite = np.isfinite(y)
    x, y = x[finite].astype(np.float64), y[finite]
    if x.size == 0:
        return 0.0, float("nan")
    if x.size == 1:
        return 0.0, float(y[0])
    i, j = np.triu_indices(x.size, k=1)
    slope = float(np.median((y[j] - y[i]) / (x[j] - x[i])))
    return slope, float(np.median(y - slope * x))


def bandpass_trend(values: np.ndarray, width: int) -> np.ndarray:
    """Robust smooth trend across channels, ignoring NaN entries.

    A running median of `width` channels in the interior, and a Theil-Sen line
    through the outer `width` channels within half a window of each edge, where
    a one-sided median would lag behind a sloping bandpass.
    """
    values = np.asarray(values, dtype=np.float64)
    n = values.size
    channels = np.arange(n)
    half = max(1, int(width) // 2)
    if n <= 2 * half:
        slope, intercept = _theil_sen_line(channels, values)
        return intercept + slope * channels
    trend = running_median(values, 2 * half + 1)
    span = min(n, 2 * half + 1)
    for edge, fit in ((channels[:half], channels[:span]), (channels[n - half :], channels[n - span :])):
        slope, intercept = _theil_sen_line(fit, values[fit])
        trend[edge] = intercept + slope * edge
    return trend


def flag_outlying_channels(
    spectra: np.ndarray,
    test: str,
    *,
    sigma: float = DEFAULT_SIGMA,
    trend_channels: int = TREND_CHANNELS,
) -> np.ndarray:
    """Flag channels of a ``(time, channel)`` array whose statistic stands out from the bandpass.

    Returns a boolean array with one entry per channel, True where the channel
    should be masked. The array must be finite: fill missing samples first.
    """
    statistic = channel_statistic(spectra, test)
    nchan = statistic.size
    flagged = np.zeros(nchan, dtype=bool)
    if nchan < 3:
        return flagged
    width = max(1, min(int(trend_channels), nchan))
    for _ in range(MAX_ITERATIONS):
        trend = bandpass_trend(np.where(flagged, np.nan, statistic), width)
        trend = np.where(np.isfinite(trend), trend, bandpass_trend(statistic, width))
        residual = statistic - trend
        centre = float(np.median(residual))
        spread = _MAD_TO_SIGMA * float(np.median(np.abs(residual - centre)))
        if not np.isfinite(spread) or spread <= 0.0:
            spread = float(np.std(residual))
        if not np.isfinite(spread) or spread <= 0.0:
            return np.zeros(nchan, dtype=bool)
        updated = np.abs(residual - centre) > sigma * spread
        if np.array_equal(updated, flagged):
            break
        flagged = updated
    return flagged


__all__ = [
    "CHANNEL_TESTS",
    "DEFAULT_SIGMA",
    "bandpass_trend",
    "channel_statistic",
    "flag_outlying_channels",
    "running_median",
]
