"""Preparation of channelized Q/U spectra from full-Stokes burst data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


_MIN_OFFPULSE_BLOCKS = 4
_VALID_CALIBRATION_STATES = frozenset({"calibrated", "unknown", "uncalibrated"})


@dataclass(frozen=True)
class IntegratedPolarizationSpectrum:
    """A browser/API-ready normalized linear-polarization spectrum."""

    status: str
    message: str
    calibration_status: str
    normalization: str
    channel_indices: np.ndarray
    freqs_mhz: np.ndarray
    stokes_q: np.ndarray
    stokes_u: np.ndarray
    sigma_q: np.ndarray
    sigma_u: np.ndarray
    integrated_stokes_i: np.ndarray
    integrated_stokes_q: np.ndarray
    integrated_stokes_u: np.ndarray
    integrated_stokes_v: np.ndarray
    linear_snr: np.ndarray
    channel_width_mhz: float | None
    event_bins: tuple[int, int]
    offpulse_regions: tuple[tuple[int, int], ...]
    offpulse_block_count: int
    warnings: tuple[str, ...]

    def to_rm_input(self, *, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return JSON compatible with the FLITS polarization workspace."""
        return {
            "freqs_mhz": self.freqs_mhz.tolist(),
            "stokes_q": self.stokes_q.tolist(),
            "stokes_u": self.stokes_u.tolist(),
            "sigma_q": self.sigma_q.tolist(),
            "sigma_u": self.sigma_u.tolist(),
            "channel_width_mhz": self.channel_width_mhz,
            "data_semantics": self.normalization,
            "calibration_status": self.calibration_status,
            "preparation": {
                "status": self.status,
                "message": self.message,
                "channel_indices": self.channel_indices.astype(int).tolist(),
                "event_bins": [int(value) for value in self.event_bins],
                "offpulse_regions": [list(region) for region in self.offpulse_regions],
                "offpulse_block_count": int(self.offpulse_block_count),
                "linear_snr": self.linear_snr.tolist(),
                "warnings": list(self.warnings),
            },
            "provenance": {} if provenance is None else provenance,
        }


def _coerce_regions(regions: Iterable[tuple[int, int]], time_bins: int) -> tuple[tuple[int, int], ...]:
    normalized: list[tuple[int, int]] = []
    for start, end in regions:
        lo, hi = int(start), int(end)
        if lo < 0 or hi > time_bins or hi <= lo:
            raise ValueError(f"Invalid off-pulse region [{lo}, {hi}) for {time_bins} time bins.")
        normalized.append((lo, hi))
    if not normalized:
        raise ValueError("At least one explicit off-pulse region is required for polarization extraction.")
    return tuple(normalized)


def _robust_block_sigma(blocks: np.ndarray) -> np.ndarray:
    median = np.median(blocks, axis=0)
    sigma = 1.4826 * np.median(np.abs(blocks - median[None, ...]), axis=0)
    fallback = np.std(blocks, axis=0, ddof=1) if blocks.shape[0] > 1 else np.zeros_like(sigma)
    return np.where(np.isfinite(sigma) & (sigma > 0.0), sigma, fallback)


def extract_normalized_linear_spectrum(
    *,
    stokes_iquv: np.ndarray,
    freqs_mhz: np.ndarray,
    event_bins: tuple[int, int],
    offpulse_regions: Iterable[tuple[int, int]],
    channel_mask: np.ndarray | None = None,
    spectral_channels: tuple[int, int] | None = None,
    channel_width_mhz: float | None = None,
    min_linear_snr: float = 5.0,
    calibration_status: str = "unknown",
) -> IntegratedPolarizationSpectrum:
    """Integrate a burst and prepare weighted ``Q/L`` and ``U/L`` spectra.

    Noise is estimated from non-overlapping, event-length integrations within
    the explicit off-pulse regions. This carries native time correlation into
    the channel uncertainties more faithfully than dividing a per-sample RMS by
    ``sqrt(N)``. The normalization supports calibrated linear-polarization
    Q/U fitting without assuming a per-sample white-noise correction.
    """
    values = np.asarray(stokes_iquv, dtype=float)
    if values.ndim != 3 or values.shape[0] < 4:
        raise ValueError("stokes_iquv must have shape (at least 4, channels, time) in I/Q/U/V order.")
    channel_count, time_bins = int(values.shape[1]), int(values.shape[2])
    freqs = np.asarray(freqs_mhz, dtype=float).reshape(-1)
    if freqs.size != channel_count:
        raise ValueError("Frequency count must match the full-Stokes channel axis.")
    event_start, event_end = (int(event_bins[0]), int(event_bins[1]))
    if event_start < 0 or event_end > time_bins or event_end <= event_start:
        raise ValueError(f"Invalid event window [{event_start}, {event_end}) for {time_bins} time bins.")
    event_length = event_end - event_start
    regions = _coerce_regions(offpulse_regions, time_bins)
    calibration = str(calibration_status).strip().lower()
    if calibration not in _VALID_CALIBRATION_STATES:
        raise ValueError(f"Unsupported calibration status: {calibration_status}")
    if not np.isfinite(min_linear_snr) or float(min_linear_snr) < 0.0:
        raise ValueError("Minimum linear-polarization S/N must be finite and non-negative.")

    masked = np.zeros(channel_count, dtype=bool)
    if channel_mask is not None:
        supplied_mask = np.asarray(channel_mask, dtype=bool).reshape(-1)
        if supplied_mask.size != channel_count:
            raise ValueError("Channel mask must match the full-Stokes channel axis.")
        masked |= supplied_mask
    if spectral_channels is not None:
        lo, hi = sorted((int(spectral_channels[0]), int(spectral_channels[1])))
        if lo < 0 or hi >= channel_count:
            raise ValueError("Spectral channel bounds fall outside the data.")
        masked[:lo] = True
        masked[hi + 1 :] = True

    offpulse_indices = np.concatenate([np.arange(start, end, dtype=int) for start, end in regions])
    baseline = np.median(values[:4, :, offpulse_indices], axis=2)
    centered = values[:4] - baseline[:, :, None]
    integrated = np.sum(centered[:, :, event_start:event_end], axis=2)

    blocks: list[np.ndarray] = []
    for start, end in regions:
        for block_start in range(start, end - event_length + 1, event_length):
            blocks.append(np.sum(centered[:, :, block_start : block_start + event_length], axis=2))
    if len(blocks) < 2:
        raise ValueError("Off-pulse regions must contain at least two complete event-length noise blocks.")
    block_values = np.stack(blocks, axis=0)
    sigma = _robust_block_sigma(block_values)

    stokes_i, stokes_q_sum, stokes_u_sum, stokes_v = integrated
    sigma_q_sum, sigma_u_sum = sigma[1], sigma[2]
    linear = np.hypot(stokes_q_sum, stokes_u_sum)
    linear_sigma = np.sqrt(
        np.square(stokes_q_sum * sigma_q_sum) + np.square(stokes_u_sum * sigma_u_sum)
    ) / np.maximum(linear, np.finfo(float).tiny)
    linear_snr_all = linear / np.maximum(linear_sigma, np.finfo(float).tiny)

    # Q/L and U/L lie on the unit circle. The common tangent-plane error is
    # twice the polarization-angle error and provides stable relative weights
    # without pretending the two normalized coordinates are independent.
    normalized_sigma = np.sqrt(
        np.square(stokes_u_sum * sigma_q_sum) + np.square(stokes_q_sum * sigma_u_sum)
    ) / np.maximum(np.square(linear), np.finfo(float).tiny)
    usable = (
        ~masked
        & np.isfinite(freqs)
        & (freqs > 0.0)
        & np.isfinite(linear)
        & (linear > 0.0)
        & np.isfinite(normalized_sigma)
        & (normalized_sigma > 0.0)
        & np.isfinite(linear_snr_all)
        & (linear_snr_all >= float(min_linear_snr))
    )
    channel_indices = np.flatnonzero(usable)
    if channel_indices.size < 8:
        raise ValueError(
            f"Only {channel_indices.size} channels pass the mask and linear-S/N threshold; at least 8 are required."
        )

    warnings: list[str] = []
    if calibration != "calibrated":
        warnings.append("polarization_calibration_required")
    if len(blocks) < _MIN_OFFPULSE_BLOCKS:
        warnings.append("limited_offpulse_blocks")
    status = "ok" if calibration == "calibrated" else "calibration_required"
    message = (
        "Calibrated normalized Q/L and U/L spectrum prepared."
        if status == "ok"
        else "Full-Stokes spectrum prepared, but a calibrated source RM must not be reported until polarization calibration is proven."
    )
    return IntegratedPolarizationSpectrum(
        status=status,
        message=message,
        calibration_status=calibration,
        normalization="normalized_stokes_q_over_l_and_u_over_l",
        channel_indices=channel_indices,
        freqs_mhz=freqs[usable],
        stokes_q=stokes_q_sum[usable] / linear[usable],
        stokes_u=stokes_u_sum[usable] / linear[usable],
        sigma_q=normalized_sigma[usable],
        sigma_u=normalized_sigma[usable],
        integrated_stokes_i=stokes_i[usable],
        integrated_stokes_q=stokes_q_sum[usable],
        integrated_stokes_u=stokes_u_sum[usable],
        integrated_stokes_v=stokes_v[usable],
        linear_snr=linear_snr_all[usable],
        channel_width_mhz=None if channel_width_mhz is None else float(channel_width_mhz),
        event_bins=(event_start, event_end),
        offpulse_regions=regions,
        offpulse_block_count=len(blocks),
        warnings=tuple(warnings),
    )
