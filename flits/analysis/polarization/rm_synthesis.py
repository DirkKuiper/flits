"""Weighted one-dimensional rotation-measure synthesis.

The implementation follows the standard dirty Faraday-spectrum transform. It
does not perform RM-CLEAN or ionospheric/instrumental polarization correction;
those omissions are reported in the result so a dirty-spectrum peak cannot be
mistaken for a fully calibrated polarization measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


_C_M_S = 299_792_458.0
_MIN_CHANNELS = 8
_MAX_PHI_SAMPLES = 20_001


@dataclass(frozen=True)
class RMSynthesisResult:
    status: str
    message: str
    peak_rm_rad_m2: float | None
    peak_rm_uncertainty_rad_m2: float | None
    peak_snr: float | None
    peak_polarized_amplitude: float | None
    reference_lambda2_m2: float | None
    rmsf_fwhm_rad_m2: float | None
    max_scale_rad_m2: float | None
    max_abs_rm_rad_m2: float | None
    channel_count: int
    phi_rad_m2: np.ndarray
    faraday_real: np.ndarray
    faraday_imag: np.ndarray
    polarized_amplitude: np.ndarray
    rmsf_amplitude: np.ndarray
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "peak_rm_rad_m2": self.peak_rm_rad_m2,
            "peak_rm_uncertainty_rad_m2": self.peak_rm_uncertainty_rad_m2,
            "peak_snr": self.peak_snr,
            "peak_polarized_amplitude": self.peak_polarized_amplitude,
            "reference_lambda2_m2": self.reference_lambda2_m2,
            "rmsf_fwhm_rad_m2": self.rmsf_fwhm_rad_m2,
            "max_scale_rad_m2": self.max_scale_rad_m2,
            "max_abs_rm_rad_m2": self.max_abs_rm_rad_m2,
            "channel_count": self.channel_count,
            "phi_rad_m2": self.phi_rad_m2.tolist(),
            "faraday_real": self.faraday_real.tolist(),
            "faraday_imag": self.faraday_imag.tolist(),
            "polarized_amplitude": self.polarized_amplitude.tolist(),
            "rmsf_amplitude": self.rmsf_amplitude.tolist(),
            "warnings": list(self.warnings),
            "method": "weighted_dirty_rm_synthesis",
        }


def _failure(status: str, message: str, channel_count: int = 0) -> RMSynthesisResult:
    empty = np.array([], dtype=float)
    return RMSynthesisResult(
        status=status,
        message=message,
        peak_rm_rad_m2=None,
        peak_rm_uncertainty_rad_m2=None,
        peak_snr=None,
        peak_polarized_amplitude=None,
        reference_lambda2_m2=None,
        rmsf_fwhm_rad_m2=None,
        max_scale_rad_m2=None,
        max_abs_rm_rad_m2=None,
        channel_count=int(channel_count),
        phi_rad_m2=empty,
        faraday_real=empty,
        faraday_imag=empty,
        polarized_amplitude=empty,
        rmsf_amplitude=empty,
        warnings=(),
    )


def run_rm_synthesis(
    *,
    freqs_mhz: np.ndarray,
    stokes_q: np.ndarray,
    stokes_u: np.ndarray,
    sigma_q: np.ndarray | float | None = None,
    sigma_u: np.ndarray | float | None = None,
    phi_min_rad_m2: float | None = None,
    phi_max_rad_m2: float | None = None,
    phi_step_rad_m2: float | None = None,
) -> RMSynthesisResult:
    """Compute a weighted dirty Faraday spectrum from channelized Q/U spectra."""
    freq = np.asarray(freqs_mhz, dtype=float).reshape(-1)
    q = np.asarray(stokes_q, dtype=float).reshape(-1)
    u = np.asarray(stokes_u, dtype=float).reshape(-1)
    if not (freq.size == q.size == u.size):
        return _failure("invalid_shape", "Frequency, Stokes Q, and Stokes U must have equal lengths.")

    finite = np.isfinite(freq) & np.isfinite(q) & np.isfinite(u) & (freq > 0.0)
    if sigma_q is not None or sigma_u is not None:
        if sigma_q is None or sigma_u is None:
            return _failure("invalid_uncertainty", "Provide both sigma_q and sigma_u, or neither.")
        sq = np.broadcast_to(np.asarray(sigma_q, dtype=float), q.shape)
        su = np.broadcast_to(np.asarray(sigma_u, dtype=float), u.shape)
        finite &= np.isfinite(sq) & np.isfinite(su) & (sq > 0.0) & (su > 0.0)
    else:
        sq = su = np.ones_like(q)

    freq, q, u, sq, su = (values[finite] for values in (freq, q, u, sq, su))
    if freq.size < _MIN_CHANNELS:
        return _failure(
            "insufficient_channels",
            f"RM synthesis requires at least {_MIN_CHANNELS} finite Q/U channels.",
            int(freq.size),
        )

    lambda2 = (_C_M_S / (freq * 1e6)) ** 2
    order = np.argsort(lambda2)
    lambda2, q, u, sq, su = (values[order] for values in (lambda2, q, u, sq, su))
    weights = 2.0 / (sq**2 + su**2)
    weight_sum = float(np.sum(weights))
    lambda0 = float(np.sum(weights * lambda2) / weight_sum)
    span = float(lambda2[-1] - lambda2[0])
    if span <= 0.0:
        return _failure("invalid_frequency_axis", "Frequency channels must span more than one wavelength.", int(freq.size))

    rmsf_fwhm = float(2.0 * np.sqrt(3.0) / span)
    max_scale = float(np.pi / lambda2[0])
    delta_lambda2 = np.diff(lambda2)
    positive_delta = delta_lambda2[delta_lambda2 > 0.0]
    max_abs_rm = float(np.sqrt(3.0) / np.max(positive_delta)) if positive_delta.size else rmsf_fwhm

    phi_min = -max_abs_rm if phi_min_rad_m2 is None else float(phi_min_rad_m2)
    phi_max = max_abs_rm if phi_max_rad_m2 is None else float(phi_max_rad_m2)
    phi_step = rmsf_fwhm / 5.0 if phi_step_rad_m2 is None else float(phi_step_rad_m2)
    if not all(np.isfinite(value) for value in (phi_min, phi_max, phi_step)) or phi_max <= phi_min or phi_step <= 0.0:
        return _failure("invalid_phi_grid", "Faraday-depth bounds and step must be finite, ordered, and positive.", int(freq.size))
    sample_count = int(np.floor((phi_max - phi_min) / phi_step)) + 1
    if sample_count > _MAX_PHI_SAMPLES:
        return _failure(
            "phi_grid_too_large",
            f"Requested Faraday-depth grid has {sample_count} samples; limit is {_MAX_PHI_SAMPLES}.",
            int(freq.size),
        )

    phi = phi_min + phi_step * np.arange(sample_count, dtype=float)
    phase = np.exp(-2j * phi[:, None] * (lambda2[None, :] - lambda0))
    polarization = q + 1j * u
    faraday = (phase @ (weights * polarization)) / weight_sum
    rmsf = (phase @ weights) / weight_sum
    amplitude = np.abs(faraday)
    peak_index = int(np.argmax(amplitude))

    # Estimate the dirty-spectrum noise away from the main RMSF lobe.
    exclusion = np.abs(phi - phi[peak_index]) <= rmsf_fwhm
    noise_values = np.concatenate((faraday.real[~exclusion], faraday.imag[~exclusion]))
    if noise_values.size >= 8:
        center = float(np.median(noise_values))
        noise = float(1.4826 * np.median(np.abs(noise_values - center)))
    else:
        noise = float("nan")
    peak_snr = float(amplitude[peak_index] / noise) if np.isfinite(noise) and noise > 0.0 else None
    uncertainty = float(rmsf_fwhm / (2.0 * peak_snr)) if peak_snr is not None and peak_snr > 0.0 else None

    warnings = ["dirty_spectrum_not_rm_cleaned", "ionospheric_rm_not_corrected", "instrumental_leakage_not_corrected"]
    if peak_snr is None or peak_snr < 6.0:
        warnings.append("low_significance_peak")
    if abs(float(phi[peak_index])) >= 0.9 * max_abs_rm:
        warnings.append("peak_near_instrumental_rm_limit")

    return RMSynthesisResult(
        status="ok",
        message="Weighted dirty RM synthesis completed.",
        peak_rm_rad_m2=float(phi[peak_index]),
        peak_rm_uncertainty_rad_m2=uncertainty,
        peak_snr=peak_snr,
        peak_polarized_amplitude=float(amplitude[peak_index]),
        reference_lambda2_m2=lambda0,
        rmsf_fwhm_rad_m2=rmsf_fwhm,
        max_scale_rad_m2=max_scale,
        max_abs_rm_rad_m2=max_abs_rm,
        channel_count=int(freq.size),
        phi_rad_m2=phi,
        faraday_real=faraday.real,
        faraday_imag=faraday.imag,
        polarized_amplitude=amplitude,
        rmsf_amplitude=np.abs(rmsf),
        warnings=tuple(warnings),
    )
