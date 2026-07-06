"""Automatic burst localization in time and frequency.

Given a masked dynamic spectrum (channels x time, NaN rows for masked
channels), find where the burst lives: the event window in time, the
spectral extent in frequency, a peak bin, and off-pulse windows. The
search iterates between the two axes so band-limited bursts are detected
against the noise of their own sub-band rather than diluted across the
full bandwidth.

The result is expressed in bins/channels relative to the array that was
passed in; callers map to absolute session coordinates.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np

DETECTION_SNR_THRESHOLD = 6.0
EXTENT_EXIT_SN = 1.0
SPECTRUM_EXIT_SN = 1.0
FULL_BAND_FRACTION = 0.95
MAX_ITERATIONS = 4
EDGE_FRACTION = 0.02
WIDE_EVENT_FRACTION = 0.25
MIN_OFFPULSE_BINS = 64


@dataclass(frozen=True)
class BurstLocalization:
    """Result of :func:`localize_burst`.

    Bins follow the half-open convention ``[event_start_bin, event_end_bin)``.
    Channels follow the inclusive FLITS convention ``[spec_lo, spec_hi]``.
    """

    status: str  # "ok" | "low_sn" | "no_detection"
    peak_bin: int
    event_start_bin: int
    event_end_bin: int
    spec_lo: int
    spec_hi: int
    band_limited: bool
    best_width_bins: int
    detection_snr: float
    integrated_snr: float
    offpulse_regions: list[tuple[int, int]] = field(default_factory=list)
    iterations: int = 0
    warning_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "peak_bin": int(self.peak_bin),
            "event_start_bin": int(self.event_start_bin),
            "event_end_bin": int(self.event_end_bin),
            "spec_lo": int(self.spec_lo),
            "spec_hi": int(self.spec_hi),
            "band_limited": bool(self.band_limited),
            "best_width_bins": int(self.best_width_bins),
            "detection_snr": float(self.detection_snr),
            "integrated_snr": float(self.integrated_snr),
            "offpulse_regions": [[int(a), int(b)] for a, b in self.offpulse_regions],
            "iterations": int(self.iterations),
            "warning_flags": list(self.warning_flags),
        }


def _robust_stats(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    center = float(np.median(finite))
    scale = float(1.4826 * np.median(np.abs(finite - center)))
    if not np.isfinite(scale) or scale <= 0.0:
        scale = float(np.std(finite))
    if not np.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    return center, scale


def _normalize_channels(masked: np.ndarray, exclude: tuple[int, int] | None) -> np.ndarray:
    """Per-channel robust z-scores, with stats taken outside `exclude`."""
    reference = masked
    if exclude is not None:
        lo, hi = exclude
        keep = np.ones(masked.shape[1], dtype=bool)
        keep[max(0, lo):max(0, hi)] = False
        if keep.sum() >= 16:
            reference = masked[:, keep]
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        center = np.nanmedian(reference, axis=1, keepdims=True)
        scale = 1.4826 * np.nanmedian(np.abs(reference - center), axis=1, keepdims=True)
        fallback = np.nanstd(reference, axis=1, keepdims=True)
    scale = np.where(np.isfinite(scale) & (scale > 0), scale, fallback)
    valid = np.isfinite(scale) & (scale > 0)
    z = np.full(masked.shape, np.nan, dtype=float)
    np.subtract(masked, center, out=z, where=valid)
    np.divide(z, scale, out=z, where=valid)
    return z


def _band_profile_sn(z: np.ndarray, spec_lo: int, spec_hi: int, exclude: tuple[int, int] | None) -> np.ndarray:
    """S/N time profile of the selected band, robustly re-normalized off-event."""
    band = z[spec_lo:spec_hi + 1, :]
    active = np.isfinite(band).any(axis=1)
    if not active.any():
        return np.zeros(z.shape[1], dtype=float)
    with np.errstate(invalid="ignore"):
        summed = np.nansum(band[active], axis=0)
        counts = np.isfinite(band[active]).sum(axis=0).astype(float)
    profile = np.where(counts > 0, summed / np.sqrt(np.maximum(counts, 1.0)), 0.0)
    reference = profile
    if exclude is not None:
        lo, hi = exclude
        keep = np.ones(profile.size, dtype=bool)
        keep[max(0, lo):max(0, hi)] = False
        if keep.sum() >= 16:
            reference = profile[keep]
    center, scale = _robust_stats(reference)
    return (profile - center) / scale


def _boxcar_snr(profile_sn: np.ndarray, width: int) -> np.ndarray:
    """Matched-filter S/N for a boxcar of `width` bins (same-length output).

    The convolved series is re-normalized by its own robust off-burst
    scatter rather than the white-noise sqrt(width) expectation: slow
    baseline wander (red noise) dominates wide smoothing scales, and the
    white-noise scaling would let broad baseline swells masquerade as
    significant burst extent. Median/MAD stay valid while the burst
    occupies a minority of the samples.
    """
    if width <= 1:
        return profile_sn.copy()
    kernel = np.ones(int(width), dtype=float) / np.sqrt(float(width))
    smoothed = np.convolve(profile_sn, kernel, mode="same")
    center, scale = _robust_stats(smoothed)
    return (smoothed - center) / scale


def _width_ladder(ntime: int, max_width_bins: int | None) -> list[int]:
    cap = ntime // 4 if max_width_bins is None else int(max_width_bins)
    cap = max(1, min(cap, ntime // 2 if ntime >= 4 else 1))
    widths: list[int] = []
    w = 1
    while w <= cap:
        widths.append(int(w))
        w = max(w + 1, int(round(w * 1.5)))
    if widths[-1] != cap:
        widths.append(cap)
    return widths


def _matched_filter_peak(profile_sn: np.ndarray, widths: list[int]) -> tuple[int, int, float]:
    best_width, best_bin, best_snr = 1, int(np.nanargmax(profile_sn)), float(np.nanmax(profile_sn))
    for width in widths:
        snr = _boxcar_snr(profile_sn, width)
        peak = int(np.nanargmax(snr))
        value = float(snr[peak])
        if value > best_snr:
            best_width, best_bin, best_snr = width, peak, value
    return best_width, best_bin, best_snr


def _contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) runs of True values."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask, [False]])
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(changes[i]), int(changes[i + 1])) for i in range(0, changes.size, 2)]


def _event_extent(
    profile_sn: np.ndarray,
    peak_bin: int,
    width: int,
    exit_sn: float,
    component_sn: float,
) -> tuple[int, int]:
    """Tight event window: the contiguous above-noise region around the
    matched peak, unioned with nearby independently-detected components.

    Extension is driven by detections, not by single noise bins poking
    above the exit threshold — a gap-bridging walk at ~1 sigma grows
    without bound on pure noise because the smoothed exceedance
    probability per effective sample is ~16%.
    """
    smoothed = _boxcar_snr(profile_sn, width)
    ntime = smoothed.size
    above = smoothed >= exit_sn
    above[peak_bin] = True
    runs = _contiguous_runs(above)

    main = next((run for run in runs if run[0] <= peak_bin < run[1]), None)
    if main is None:
        main = (peak_bin, peak_bin + 1)

    component_runs = [
        run for run in runs if float(np.nanmax(smoothed[run[0]:run[1]])) >= component_sn
    ]
    merge_gap = max(2, 2 * int(width))
    start, end = main
    changed = True
    while changed:
        changed = False
        for run_start, run_end in component_runs:
            if run_start >= end and run_start - end <= merge_gap:
                end = run_end
                changed = True
            elif run_end <= start and start - run_end <= merge_gap:
                start = run_start
                changed = True

    # The width-w boxcar carries signal ~w bins beyond the burst, so the
    # coarse >= exit region overshoots by about the matched width per side
    # for bright bursts. Shrink the boundaries using significant runs of a
    # finer smoothing so they land near the true signal edge; isolated
    # noise bins above the exit threshold do not anchor the boundary.
    fine_width = max(1, int(width) // 6)
    if fine_width < width:
        fine = _boxcar_snr(profile_sn, fine_width)
        section = fine[start:end]
        fine_runs = _contiguous_runs(section >= exit_sn)
        run_gate = 3.0
        kept = [
            (run_start, run_end)
            for run_start, run_end in fine_runs
            if float(np.nanmax(section[run_start:run_end])) >= run_gate
            or run_start + start <= peak_bin < run_end + start
        ]
        if kept:
            tight_start = start + min(run_start for run_start, _ in kept)
            tight_end = start + max(run_end for _, run_end in kept)
            start = min(tight_start, peak_bin)
            end = max(tight_end, peak_bin + 1)

    return max(0, int(start)), min(ntime, int(end))


def _smooth_channels(values: np.ndarray, kernel_channels: int) -> np.ndarray:
    if kernel_channels <= 1:
        return values.copy()
    kernel = np.ones(int(kernel_channels), dtype=float)
    finite = np.isfinite(values)
    padded = np.where(finite, values, 0.0)
    weight = np.convolve(finite.astype(float), kernel, mode="same")
    total = np.convolve(padded, kernel, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        smoothed = np.where(weight > 0, total / weight, np.nan)
    return smoothed


def _spectral_extent(
    z: np.ndarray,
    event: tuple[int, int],
    exit_sn: float,
    full_band_fraction: float,
) -> tuple[int, int, bool]:
    """Channel range [lo, hi] containing the burst, and whether it is band-limited."""
    nchan = z.shape[0]
    ev_lo, ev_hi = event
    n_event = max(1, ev_hi - ev_lo)
    with np.errstate(invalid="ignore"):
        channel_sn = np.nansum(z[:, ev_lo:ev_hi], axis=1) / np.sqrt(float(n_event))
    channel_sn[~np.isfinite(channel_sn)] = np.nan
    dead = ~np.isfinite(z).any(axis=1)
    channel_sn[dead] = np.nan

    usable = np.flatnonzero(~dead)
    if usable.size == 0:
        return 0, max(0, nchan - 1), False

    kernel = max(3, usable.size // 64)
    smoothed = _smooth_channels(channel_sn, kernel)

    finite_smoothed = smoothed[np.isfinite(smoothed)]
    if finite_smoothed.size == 0 or not np.isfinite(np.nanmax(smoothed)):
        return int(usable[0]), int(usable[-1]), False

    peak_channel = int(np.nanargmax(np.where(np.isfinite(smoothed), smoothed, -np.inf)))
    above = np.isfinite(smoothed) & (smoothed >= float(exit_sn))
    above[:usable[0]] = False
    above[usable[-1] + 1:] = False
    above[peak_channel] = True
    runs = _contiguous_runs(above)

    main = next((run for run in runs if run[0] <= peak_channel < run[1]), None)
    if main is None:
        main = (peak_channel, peak_channel + 1)

    def run_max(run: tuple[int, int]) -> float:
        section = smoothed[run[0]:run[1]]
        finite = section[np.isfinite(section)]
        return float(np.max(finite)) if finite.size else float("-inf")

    component_sn = max(4.0, float(exit_sn))
    component_runs = [run for run in runs if run_max(run) >= component_sn]
    merge_gap = max(2 * kernel, nchan // 16)
    lo, hi_exclusive = main
    changed = True
    while changed:
        changed = False
        for run_start, run_end in component_runs:
            if run_start >= hi_exclusive and run_start - hi_exclusive <= merge_gap:
                hi_exclusive = run_end
                changed = True
            elif run_end <= lo and lo - run_end <= merge_gap:
                lo = run_start
                changed = True
    hi = hi_exclusive - 1

    pad = kernel
    lo = max(int(usable[0]), lo - pad)
    hi = min(int(usable[-1]), hi + pad)

    covered = np.flatnonzero(~dead[lo:hi + 1]).size
    band_limited = covered < full_band_fraction * usable.size
    if not band_limited:
        lo, hi = int(usable[0]), int(usable[-1])
    return lo, hi, band_limited


def _offpulse_windows(
    ntime: int,
    event: tuple[int, int],
    width: int,
    min_bins: int,
) -> list[tuple[int, int]]:
    ev_lo, ev_hi = event
    guard = max(3 * width, (ev_hi - ev_lo))
    windows: list[tuple[int, int]] = []
    left_hi = max(0, ev_lo - guard)
    if left_hi >= 2:
        windows.append((0, left_hi))
    right_lo = min(ntime, ev_hi + guard)
    if ntime - right_lo >= 2:
        windows.append((right_lo, ntime))
    total = sum(hi - lo for lo, hi in windows)
    if total < min_bins:
        # Relax the guard so at least some off-pulse reference exists.
        guard = max(width, (ev_hi - ev_lo) // 2)
        windows = []
        left_hi = max(0, ev_lo - guard)
        if left_hi >= 2:
            windows.append((0, left_hi))
        right_lo = min(ntime, ev_hi + guard)
        if ntime - right_lo >= 2:
            windows.append((right_lo, ntime))
    return windows


def _integrated_snr(profile_sn: np.ndarray, event: tuple[int, int]) -> float:
    lo, hi = event
    section = profile_sn[max(0, lo):max(0, hi)]
    finite = section[np.isfinite(section)]
    if finite.size == 0:
        return 0.0
    return float(np.sum(finite) / np.sqrt(finite.size))


def localize_burst(
    masked: np.ndarray,
    *,
    detection_snr_threshold: float = DETECTION_SNR_THRESHOLD,
    extent_exit_sn: float = EXTENT_EXIT_SN,
    spectrum_exit_sn: float = SPECTRUM_EXIT_SN,
    full_band_fraction: float = FULL_BAND_FRACTION,
    max_iterations: int = MAX_ITERATIONS,
    max_width_bins: int | None = None,
    min_offpulse_bins: int = MIN_OFFPULSE_BINS,
) -> BurstLocalization:
    """Localize a burst in a masked dynamic spectrum (channels x time).

    Masked channels must be NaN rows. Returns bins relative to the input
    array. When nothing crosses `detection_snr_threshold` the status is
    "no_detection" and the selections fall back to the array centre.
    """
    masked = np.asarray(masked, dtype=float)
    if masked.ndim != 2 or masked.size == 0:
        raise ValueError("localize_burst expects a non-empty 2D (channels x time) array")
    nchan, ntime = masked.shape

    dead = ~np.isfinite(masked).any(axis=1)
    usable_channels = np.flatnonzero(~dead)
    if usable_channels.size == 0 or ntime < 8:
        return BurstLocalization(
            status="no_detection",
            peak_bin=ntime // 2,
            event_start_bin=max(0, ntime // 2 - 1),
            event_end_bin=min(ntime, ntime // 2 + 1),
            spec_lo=0,
            spec_hi=max(0, nchan - 1),
            band_limited=False,
            best_width_bins=1,
            detection_snr=0.0,
            integrated_snr=0.0,
            warning_flags=["unusable_data"],
        )

    widths = _width_ladder(ntime, max_width_bins)

    spec_lo, spec_hi = int(usable_channels[0]), int(usable_channels[-1])
    event: tuple[int, int] | None = None
    band_limited = False
    best_width, peak_bin, detection_snr = 1, ntime // 2, 0.0
    profile_sn = np.zeros(ntime, dtype=float)
    iterations = 0

    previous: tuple[int, int, int, int] | None = None
    for iteration in range(max_iterations):
        iterations = iteration + 1
        z = _normalize_channels(masked, exclude=event)
        profile_sn = _band_profile_sn(z, spec_lo, spec_hi, exclude=event)
        best_width, peak_bin, detection_snr = _matched_filter_peak(profile_sn, widths)
        event = _event_extent(
            profile_sn, peak_bin, best_width, extent_exit_sn, detection_snr_threshold
        )
        spec_lo, spec_hi, band_limited = _spectral_extent(
            z,
            event,
            spectrum_exit_sn,
            full_band_fraction,
        )
        state = (event[0], event[1], spec_lo, spec_hi)
        if previous is not None and all(abs(a - b) <= 2 for a, b in zip(state, previous)):
            break
        previous = state

    assert event is not None
    # Final profile on the converged band for peak/integrated S/N.
    z = _normalize_channels(masked, exclude=event)
    profile_sn = _band_profile_sn(z, spec_lo, spec_hi, exclude=event)
    best_width, peak_bin, detection_snr = _matched_filter_peak(profile_sn, widths)
    event = _event_extent(
        profile_sn, peak_bin, best_width, extent_exit_sn, detection_snr_threshold
    )
    event_section = profile_sn[event[0]:event[1]]
    if event_section.size and np.isfinite(event_section).any():
        peak_bin = int(event[0] + np.nanargmax(event_section))
    integrated_snr = _integrated_snr(profile_sn, event)

    # Store a modestly padded window so slow rise/decay wings are kept.
    pad = max(1, int(round(0.25 * best_width)))
    event = (max(0, event[0] - pad), min(ntime, event[1] + pad))

    warning_flags: list[str] = []
    edge_bins = max(1, int(EDGE_FRACTION * ntime))
    if event[0] <= edge_bins or event[1] >= ntime - edge_bins:
        warning_flags.append("event_near_edge")
    if (event[1] - event[0]) > WIDE_EVENT_FRACTION * ntime:
        warning_flags.append("wide_event")
    if band_limited and (spec_lo <= usable_channels[0] or spec_hi >= usable_channels[-1]):
        warning_flags.append("band_touches_edge")

    if detection_snr < detection_snr_threshold:
        status = "no_detection"
        warning_flags.append("below_detection_threshold")
    elif integrated_snr < detection_snr_threshold:
        status = "low_sn"
        warning_flags.append("low_integrated_snr")
    else:
        status = "ok"

    offpulse = _offpulse_windows(ntime, event, best_width, min_offpulse_bins)
    if not offpulse:
        warning_flags.append("no_offpulse_window")

    return BurstLocalization(
        status=status,
        peak_bin=int(peak_bin),
        event_start_bin=int(event[0]),
        event_end_bin=int(event[1]),
        spec_lo=int(spec_lo),
        spec_hi=int(spec_hi),
        band_limited=bool(band_limited),
        best_width_bins=int(best_width),
        detection_snr=float(detection_snr),
        integrated_snr=float(integrated_snr),
        offpulse_regions=offpulse,
        iterations=iterations,
        warning_flags=warning_flags,
    )


__all__ = ["BurstLocalization", "localize_burst"]
