"""Noise-subtracted multiscale temporal power for transient profiles.

The estimator uses maximum-overlap Haar differences: at each scale, it
compares the means of adjacent windows across every possible time offset.
The mean squared response measured in contiguous off-pulse runs is then
subtracted from the event response.  This produces a power distribution
across timescales without selecting the first scale that crosses a detection
threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MIN_COEFFICIENTS = 4


@dataclass(frozen=True)
class HaarExcessPowerResult:
    """Empirical-noise-subtracted Haar power as a function of scale."""

    status: str
    message: str | None
    scales_bins: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    scales_ms: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    event_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    noise_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    excess_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    normalized_excess_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    event_coefficient_count: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    noise_coefficient_count: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    characteristic_scale_ms: float | None = None
    dominant_scale_ms: float | None = None
    warning_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "scales_bins": self.scales_bins.astype(int).tolist(),
            "scales_ms": self.scales_ms.astype(float).tolist(),
            "event_power": self.event_power.astype(float).tolist(),
            "noise_power": self.noise_power.astype(float).tolist(),
            "excess_power": self.excess_power.astype(float).tolist(),
            "normalized_excess_power": self.normalized_excess_power.astype(float).tolist(),
            "event_coefficient_count": self.event_coefficient_count.astype(int).tolist(),
            "noise_coefficient_count": self.noise_coefficient_count.astype(int).tolist(),
            "characteristic_scale_ms": self.characteristic_scale_ms,
            "dominant_scale_ms": self.dominant_scale_ms,
            "warning_flags": list(self.warning_flags),
        }


def _profile(values: np.ndarray | Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float).ravel()


def _finite_runs(values: np.ndarray | Sequence[float]) -> list[np.ndarray]:
    """Split a profile at non-finite samples without joining across gaps."""
    profile = _profile(values)
    finite = np.isfinite(profile)
    if not finite.any():
        return []
    padded = np.concatenate(([False], finite, [False]))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return [profile[start:end] for start, end in zip(changes[::2], changes[1::2], strict=False)]


def _haar_differences(profile: np.ndarray, scale_bins: int) -> np.ndarray:
    """Adjacent-window mean differences at every possible time offset."""
    scale = int(scale_bins)
    if scale < 1 or profile.size < 2 * scale:
        return np.array([], dtype=float)
    cumulative = np.concatenate(([0.0], np.cumsum(profile, dtype=float)))
    window_sums = cumulative[scale:] - cumulative[:-scale]
    return (window_sums[scale:] - window_sums[:-scale]) / float(scale)


def _default_scales(event_bins: int) -> np.ndarray:
    maximum = max(0, int(event_bins) // 4)
    if maximum < 1:
        return np.array([], dtype=int)
    scales: list[int] = []
    scale = 1
    while scale <= maximum:
        scales.append(scale)
        scale *= 2
    return np.asarray(scales, dtype=int)


def _coerce_scales(scales_bins: Sequence[int] | np.ndarray | None, event_bins: int) -> np.ndarray:
    if scales_bins is None:
        return _default_scales(event_bins)
    scales = np.asarray(scales_bins, dtype=int).ravel()
    if scales.size == 0 or np.any(scales < 1):
        raise ValueError("scales_bins must contain positive integers")
    scales = np.unique(scales)
    return scales[2 * scales <= int(event_bins)]


def haar_excess_power(
    event_profile: np.ndarray | Sequence[float],
    offpulse_runs: Sequence[np.ndarray | Sequence[float]],
    *,
    tsamp_ms: float,
    scales_bins: Sequence[int] | np.ndarray | None = None,
    min_coefficients: int = MIN_COEFFICIENTS,
) -> HaarExcessPowerResult:
    """Measure temporal signal power above an empirical off-pulse floor.

    Parameters
    ----------
    event_profile
        One-dimensional on-burst time profile.
    offpulse_runs
        Contiguous off-pulse profile sections. Runs are never joined across a
        gap, so artificial Haar coefficients are not created at boundaries.
    tsamp_ms
        Native or effective sampling interval in milliseconds.
    scales_bins
        Optional positive scales. By default FLITS uses dyadic scales up to a
        quarter of the event length.
    min_coefficients
        Minimum event and pooled off-pulse coefficient count retained at each
        scale.

    Notes
    -----
    ``characteristic_scale_ms`` is the excess-power-weighted geometric mean
    scale. It is an aggregate descriptor, not a minimum resolvable timescale.
    Its selection dependence should still be assessed with injection/recovery
    tests when comparing a burst population.
    """
    if not np.isfinite(tsamp_ms) or float(tsamp_ms) <= 0.0:
        raise ValueError("tsamp_ms must be finite and positive")
    if int(min_coefficients) < 1:
        raise ValueError("min_coefficients must be positive")

    event = _profile(event_profile)
    if event.size and not np.isfinite(event).all():
        return HaarExcessPowerResult(
            "nonfinite_event",
            "Event profiles must be finite so time gaps are not silently joined.",
        )
    noise_runs = [finite_run for run in offpulse_runs for finite_run in _finite_runs(run)]
    if event.size < 4:
        return HaarExcessPowerResult("insufficient_event", "At least four finite event bins are required.")
    if not noise_runs:
        return HaarExcessPowerResult("insufficient_noise", "At least one finite off-pulse run is required.")

    requested_scales = _coerce_scales(scales_bins, event.size)
    kept_scales: list[int] = []
    event_power: list[float] = []
    noise_power: list[float] = []
    event_counts: list[int] = []
    noise_counts: list[int] = []
    for scale in requested_scales:
        event_coefficients = _haar_differences(event, int(scale))
        noise_coefficients = [
            coefficients for run in noise_runs if (coefficients := _haar_differences(run, int(scale))).size
        ]
        pooled_noise = np.concatenate(noise_coefficients) if noise_coefficients else np.array([], dtype=float)
        if event_coefficients.size < int(min_coefficients) or pooled_noise.size < int(min_coefficients):
            continue
        kept_scales.append(int(scale))
        event_power.append(float(np.mean(np.square(event_coefficients))))
        noise_power.append(float(np.mean(np.square(pooled_noise))))
        event_counts.append(int(event_coefficients.size))
        noise_counts.append(int(pooled_noise.size))

    scale_array = np.asarray(kept_scales, dtype=int)
    if not scale_array.size:
        return HaarExcessPowerResult(
            "insufficient_scales",
            "No scale has enough event and off-pulse Haar coefficients.",
        )

    event_array = np.asarray(event_power, dtype=float)
    noise_array = np.asarray(noise_power, dtype=float)
    excess_array = event_array - noise_array
    positive_excess = np.clip(excess_array, 0.0, None)
    total_positive = float(np.sum(positive_excess))
    normalized = positive_excess / total_positive if total_positive > 0.0 else np.zeros_like(positive_excess)
    scales_ms = scale_array.astype(float) * float(tsamp_ms)
    warning_flags: list[str] = []
    characteristic_scale_ms = None
    dominant_scale_ms = None
    status = "ok"
    message = None
    if total_positive <= 0.0:
        status = "no_excess_power"
        message = "Event Haar power does not exceed the empirical off-pulse power."
        warning_flags.append("no_positive_excess")
    else:
        characteristic_scale_ms = float(np.exp(np.sum(normalized * np.log(scales_ms))))
        dominant_scale_ms = float(scales_ms[int(np.argmax(positive_excess))])
    if np.any(excess_array < 0.0):
        warning_flags.append("negative_excess_at_some_scales")

    return HaarExcessPowerResult(
        status=status,
        message=message,
        scales_bins=scale_array,
        scales_ms=scales_ms,
        event_power=event_array,
        noise_power=noise_array,
        excess_power=excess_array,
        normalized_excess_power=normalized,
        event_coefficient_count=np.asarray(event_counts, dtype=int),
        noise_coefficient_count=np.asarray(noise_counts, dtype=int),
        characteristic_scale_ms=characteristic_scale_ms,
        dominant_scale_ms=dominant_scale_ms,
        warning_flags=warning_flags,
    )


def excess_power_fraction_below(result: HaarExcessPowerResult, max_scale_ms: float) -> float | None:
    """Fraction of positive excess power at scales no larger than a limit."""
    if not np.isfinite(max_scale_ms) or float(max_scale_ms) <= 0.0:
        raise ValueError("max_scale_ms must be finite and positive")
    if result.normalized_excess_power.size == 0 or not np.any(result.normalized_excess_power > 0.0):
        return None
    keep = result.scales_ms <= float(max_scale_ms)
    return float(np.sum(result.normalized_excess_power[keep]))


__all__ = [
    "HaarExcessPowerResult",
    "excess_power_fraction_below",
    "haar_excess_power",
]
