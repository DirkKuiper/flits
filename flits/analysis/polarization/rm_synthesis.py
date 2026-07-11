"""Weighted one-dimensional rotation-measure synthesis and RM-CLEAN.

The transform follows Brentjens & de Bruyn (2005).  The implementation keeps
the dirty spectrum, RMSF, and optional RM-CLEAN product separate so callers do
not accidentally present a deconvolved result as an independent measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


_C_M_S = 299_792_458.0
_MIN_CHANNELS = 8
_MAX_PHI_SAMPLES = 100_001
_TRANSFORM_TARGET_ELEMENTS = 4_000_000
_DEFAULT_OVERSAMPLING = 5.0


@dataclass(frozen=True)
class RMSynthesisResult:
    status: str
    message: str
    peak_rm_rad_m2: float | None
    peak_rm_uncertainty_rad_m2: float | None
    peak_snr: float | None
    false_alarm_probability: float | None
    peak_polarized_amplitude: float | None
    peak_polarized_amplitude_debiased: float | None
    faraday_noise: float | None
    noise_source: str | None
    polarization_angle_deg: float | None
    intrinsic_polarization_angle_deg: float | None
    reference_lambda2_m2: float | None
    rmsf_fwhm_rad_m2: float | None
    rmsf_max_sidelobe: float | None
    max_scale_rad_m2: float | None
    max_abs_rm_rad_m2: float | None
    lambda2_min_m2: float | None
    lambda2_max_m2: float | None
    reduced_chi_square: float | None
    channel_count: int
    rejected_channel_count: int
    independent_faraday_samples: int
    phi_step_rad_m2: float | None
    phi_rad_m2: np.ndarray
    faraday_real: np.ndarray
    faraday_imag: np.ndarray
    polarized_amplitude: np.ndarray
    rmsf_real: np.ndarray
    rmsf_imag: np.ndarray
    rmsf_amplitude: np.ndarray
    cleaned_faraday_real: np.ndarray
    cleaned_faraday_imag: np.ndarray
    cleaned_polarized_amplitude: np.ndarray
    clean_component_real: np.ndarray
    clean_component_imag: np.ndarray
    clean_iterations: int
    clean_cutoff: float | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "peak_rm_rad_m2": self.peak_rm_rad_m2,
            "peak_rm_uncertainty_rad_m2": self.peak_rm_uncertainty_rad_m2,
            "peak_snr": self.peak_snr,
            "false_alarm_probability": self.false_alarm_probability,
            "peak_polarized_amplitude": self.peak_polarized_amplitude,
            "peak_polarized_amplitude_debiased": self.peak_polarized_amplitude_debiased,
            "faraday_noise": self.faraday_noise,
            "noise_source": self.noise_source,
            "polarization_angle_deg": self.polarization_angle_deg,
            "intrinsic_polarization_angle_deg": self.intrinsic_polarization_angle_deg,
            "reference_lambda2_m2": self.reference_lambda2_m2,
            "rmsf_fwhm_rad_m2": self.rmsf_fwhm_rad_m2,
            "rmsf_max_sidelobe": self.rmsf_max_sidelobe,
            "max_scale_rad_m2": self.max_scale_rad_m2,
            "max_abs_rm_rad_m2": self.max_abs_rm_rad_m2,
            "lambda2_min_m2": self.lambda2_min_m2,
            "lambda2_max_m2": self.lambda2_max_m2,
            "reduced_chi_square": self.reduced_chi_square,
            "channel_count": self.channel_count,
            "rejected_channel_count": self.rejected_channel_count,
            "independent_faraday_samples": self.independent_faraday_samples,
            "phi_step_rad_m2": self.phi_step_rad_m2,
            "phi_rad_m2": self.phi_rad_m2.tolist(),
            "faraday_real": self.faraday_real.tolist(),
            "faraday_imag": self.faraday_imag.tolist(),
            "polarized_amplitude": self.polarized_amplitude.tolist(),
            "rmsf_real": self.rmsf_real.tolist(),
            "rmsf_imag": self.rmsf_imag.tolist(),
            "rmsf_amplitude": self.rmsf_amplitude.tolist(),
            "cleaned_faraday_real": self.cleaned_faraday_real.tolist(),
            "cleaned_faraday_imag": self.cleaned_faraday_imag.tolist(),
            "cleaned_polarized_amplitude": self.cleaned_polarized_amplitude.tolist(),
            "clean_component_real": self.clean_component_real.tolist(),
            "clean_component_imag": self.clean_component_imag.tolist(),
            "clean_iterations": self.clean_iterations,
            "clean_cutoff": self.clean_cutoff,
            "warnings": list(self.warnings),
            # Retained for API compatibility; clean_applied identifies the
            # optional deconvolution product without changing dirty semantics.
            "method": "weighted_dirty_rm_synthesis",
            "method_version": "2.0",
            "clean_applied": bool(self.cleaned_polarized_amplitude.size),
        }


def _failure(
    status: str,
    message: str,
    *,
    channel_count: int = 0,
    rejected_channel_count: int = 0,
) -> RMSynthesisResult:
    empty = np.array([], dtype=float)
    return RMSynthesisResult(
        status=status,
        message=message,
        peak_rm_rad_m2=None,
        peak_rm_uncertainty_rad_m2=None,
        peak_snr=None,
        false_alarm_probability=None,
        peak_polarized_amplitude=None,
        peak_polarized_amplitude_debiased=None,
        faraday_noise=None,
        noise_source=None,
        polarization_angle_deg=None,
        intrinsic_polarization_angle_deg=None,
        reference_lambda2_m2=None,
        rmsf_fwhm_rad_m2=None,
        rmsf_max_sidelobe=None,
        max_scale_rad_m2=None,
        max_abs_rm_rad_m2=None,
        lambda2_min_m2=None,
        lambda2_max_m2=None,
        reduced_chi_square=None,
        channel_count=int(channel_count),
        rejected_channel_count=int(rejected_channel_count),
        independent_faraday_samples=0,
        phi_step_rad_m2=None,
        phi_rad_m2=empty,
        faraday_real=empty,
        faraday_imag=empty,
        polarized_amplitude=empty,
        rmsf_real=empty,
        rmsf_imag=empty,
        rmsf_amplitude=empty,
        cleaned_faraday_real=empty,
        cleaned_faraday_imag=empty,
        cleaned_polarized_amplitude=empty,
        clean_component_real=empty,
        clean_component_imag=empty,
        clean_iterations=0,
        clean_cutoff=None,
        warnings=(),
    )


def _broadcast_optional(values: np.ndarray | float, shape: tuple[int, ...]) -> np.ndarray | None:
    try:
        return np.array(np.broadcast_to(np.asarray(values, dtype=float), shape), dtype=float, copy=True)
    except (TypeError, ValueError):
        return None


def _transform(
    phi: np.ndarray,
    lambda2: np.ndarray,
    lambda0: float,
    weighted_values: np.ndarray,
    weight_sum: float,
) -> np.ndarray:
    """Evaluate a transform in bounded chunks to avoid a phi-by-channel spike."""
    result = np.empty(phi.size, dtype=complex)
    chunk_size = max(1, _TRANSFORM_TARGET_ELEMENTS // max(1, lambda2.size))
    centered_lambda2 = lambda2 - lambda0
    for start in range(0, phi.size, chunk_size):
        stop = min(phi.size, start + chunk_size)
        phase = np.exp(-2j * phi[start:stop, None] * centered_lambda2[None, :])
        result[start:stop] = (phase @ weighted_values) / weight_sum
    return result


def _refined_peak(phi: np.ndarray, amplitude: np.ndarray, index: int) -> float:
    """Refine a uniformly sampled peak with a three-point parabola in power."""
    if index <= 0 or index >= phi.size - 1:
        return float(phi[index])
    y0, y1, y2 = np.square(amplitude[index - 1 : index + 2])
    denominator = y0 - (2.0 * y1) + y2
    if not np.isfinite(denominator) or denominator >= 0.0 or denominator == 0.0:
        return float(phi[index])
    offset = float(np.clip(0.5 * (y0 - y2) / denominator, -1.0, 1.0))
    return float(phi[index] + offset * (phi[1] - phi[0]))


def _wrap_angle_deg(angle_rad: float) -> float:
    return float(np.degrees(angle_rad) % 180.0)


def _infer_channel_widths_mhz(freq: np.ndarray) -> np.ndarray | None:
    """Infer non-overlapping channel widths from centre frequencies."""
    unique = np.unique(np.sort(freq))
    if unique.size < 2:
        return None
    gaps = np.diff(unique)
    positive = gaps[gaps > 0.0]
    if positive.size == 0:
        return None
    typical = float(np.median(positive))
    widths = np.empty(freq.size, dtype=float)
    for index, value in enumerate(freq):
        position = int(np.searchsorted(unique, value))
        left = unique[position] - unique[position - 1] if position > 0 else typical
        right = unique[position + 1] - unique[position] if position < unique.size - 1 else typical
        widths[index] = min(left, right)
    return widths


def _lambda2_channel_width(freq_mhz: np.ndarray, width_mhz: np.ndarray) -> np.ndarray:
    half_width = np.minimum(width_mhz / 2.0, np.nextafter(freq_mhz, 0.0))
    low_hz = (freq_mhz - half_width) * 1e6
    high_hz = (freq_mhz + half_width) * 1e6
    return (_C_M_S / low_hz) ** 2 - (_C_M_S / high_hz) ** 2


def _robust_noise_from_residual(residual: np.ndarray, weights: np.ndarray) -> float | None:
    values = np.concatenate((residual.real, residual.imag))
    center = float(np.median(values))
    component_sigma = float(1.4826 * np.median(np.abs(values - center)))
    if not np.isfinite(component_sigma) or component_sigma <= np.finfo(float).eps:
        return None
    # Approximate the propagated noise after combining heteroscedastic channels.
    normalized = weights / np.sum(weights)
    return float(component_sigma * np.sqrt(np.sum(normalized**2)))


def _rm_clean(
    dirty: np.ndarray,
    phi: np.ndarray,
    lambda2: np.ndarray,
    lambda0: float,
    weights: np.ndarray,
    rmsf_fwhm: float,
    cutoff: float,
    gain: float,
    max_iterations: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Deconvolve the dirty FDF using the one-dimensional RM-CLEAN loop."""
    residual = np.array(dirty, dtype=complex, copy=True)
    components = np.zeros_like(residual)
    offsets = (np.arange(-(phi.size - 1), phi.size, dtype=float) * (phi[1] - phi[0]))
    rmsf_offsets = _transform(offsets, lambda2, lambda0, weights.astype(complex), float(np.sum(weights)))
    center = phi.size - 1
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        peak_index = int(np.argmax(np.abs(residual)))
        if float(np.abs(residual[peak_index])) <= cutoff:
            iterations -= 1
            break
        component = gain * residual[peak_index]
        components[peak_index] += component
        start = center - peak_index
        residual -= component * rmsf_offsets[start : start + phi.size]
    else:
        iterations = max_iterations

    sigma_bins = rmsf_fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)) * (phi[1] - phi[0]))
    radius = min(max(1, int(np.ceil(5.0 * sigma_bins))), max(1, (phi.size - 1) // 2))
    kernel_axis = np.arange(-radius, radius + 1, dtype=float)
    clean_beam = np.exp(-0.5 * np.square(kernel_axis / sigma_bins))
    restored = (
        np.convolve(components.real, clean_beam, mode="same")
        + 1j * np.convolve(components.imag, clean_beam, mode="same")
        + residual
    )
    return restored, components, residual, int(iterations)


def run_rm_synthesis(
    *,
    freqs_mhz: np.ndarray,
    stokes_q: np.ndarray,
    stokes_u: np.ndarray,
    sigma_q: np.ndarray | float | None = None,
    sigma_u: np.ndarray | float | None = None,
    channel_width_mhz: np.ndarray | float | None = None,
    phi_min_rad_m2: float | None = None,
    phi_max_rad_m2: float | None = None,
    phi_step_rad_m2: float | None = None,
    clean: bool = False,
    clean_gain: float = 0.1,
    clean_threshold_sigma: float = 3.0,
    clean_max_iterations: int = 1000,
) -> RMSynthesisResult:
    """Compute a weighted dirty Faraday spectrum and optional RM-CLEAN product.

    Q and U must be channelized spectra on the supplied frequency centres.  If
    per-channel uncertainties are available they drive both inverse-variance
    weighting and the propagated Faraday-spectrum noise.  Otherwise FLITS uses
    equal weights and estimates noise from the residual of the best thin model.
    """
    freq = np.asarray(freqs_mhz, dtype=float).reshape(-1)
    q = np.asarray(stokes_q, dtype=float).reshape(-1)
    u = np.asarray(stokes_u, dtype=float).reshape(-1)
    input_count = int(freq.size)
    if not (freq.size == q.size == u.size):
        return _failure("invalid_shape", "Frequency, Stokes Q, and Stokes U must have equal lengths.")

    has_uncertainties = sigma_q is not None or sigma_u is not None
    if has_uncertainties:
        if sigma_q is None or sigma_u is None:
            return _failure("invalid_uncertainty", "Provide both sigma_q and sigma_u, or neither.")
        sq = _broadcast_optional(sigma_q, q.shape)
        su = _broadcast_optional(sigma_u, u.shape)
        if sq is None or su is None:
            return _failure("invalid_uncertainty", "sigma_q and sigma_u must be scalars or match the Q/U channel count.")
    else:
        sq = su = np.ones_like(q)

    widths = None
    if channel_width_mhz is not None:
        widths = _broadcast_optional(channel_width_mhz, freq.shape)
        if widths is None:
            return _failure("invalid_channel_width", "channel_width_mhz must be a scalar or match the channel count.")

    finite = np.isfinite(freq) & np.isfinite(q) & np.isfinite(u) & (freq > 0.0)
    if has_uncertainties:
        finite &= np.isfinite(sq) & np.isfinite(su) & (sq > 0.0) & (su > 0.0)
    if widths is not None:
        finite &= np.isfinite(widths) & (widths > 0.0) & (widths < 2.0 * freq)
    rejected_count = int(input_count - np.count_nonzero(finite))
    freq, q, u, sq, su = (values[finite] for values in (freq, q, u, sq, su))
    if widths is not None:
        widths = widths[finite]
    if freq.size < _MIN_CHANNELS:
        return _failure(
            "insufficient_channels",
            f"RM synthesis requires at least {_MIN_CHANNELS} usable Q/U channels.",
            channel_count=int(freq.size),
            rejected_channel_count=rejected_count,
        )

    lambda2 = (_C_M_S / (freq * 1e6)) ** 2
    order = np.argsort(lambda2)
    lambda2, freq, q, u, sq, su = (values[order] for values in (lambda2, freq, q, u, sq, su))
    if widths is not None:
        widths = widths[order]
    weights = 2.0 / (sq**2 + su**2) if has_uncertainties else np.ones_like(q)
    weight_sum = float(np.sum(weights))
    if not np.isfinite(weight_sum) or weight_sum <= 0.0:
        return _failure("invalid_uncertainty", "Channel uncertainties produced invalid synthesis weights.")
    lambda0 = float(np.sum(weights * lambda2) / weight_sum)
    span = float(lambda2[-1] - lambda2[0])
    if span <= np.finfo(float).eps:
        return _failure(
            "invalid_frequency_axis",
            "Frequency channels must span more than one wavelength-squared value.",
            channel_count=int(freq.size),
            rejected_channel_count=rejected_count,
        )

    rmsf_fwhm = float(2.0 * np.sqrt(3.0) / span)
    max_scale = float(np.pi / lambda2[0])
    inferred_widths = widths if widths is not None else _infer_channel_widths_mhz(freq)
    if inferred_widths is None:
        max_abs_rm = rmsf_fwhm
    else:
        lambda2_width = _lambda2_channel_width(freq, inferred_widths)
        max_abs_rm = float(np.sqrt(3.0) / np.max(lambda2_width))

    phi_min = -max_abs_rm if phi_min_rad_m2 is None else float(phi_min_rad_m2)
    phi_max = max_abs_rm if phi_max_rad_m2 is None else float(phi_max_rad_m2)
    phi_step = rmsf_fwhm / _DEFAULT_OVERSAMPLING if phi_step_rad_m2 is None else float(phi_step_rad_m2)
    if not all(np.isfinite(value) for value in (phi_min, phi_max, phi_step)) or phi_max <= phi_min or phi_step <= 0.0:
        return _failure(
            "invalid_phi_grid",
            "Faraday-depth bounds and step must be finite, ordered, and positive.",
            channel_count=int(freq.size),
            rejected_channel_count=rejected_count,
        )
    sample_count = int(np.floor((phi_max - phi_min) / phi_step)) + 1
    if sample_count < 3:
        return _failure("invalid_phi_grid", "Faraday-depth grid must contain at least three samples.")
    if sample_count > _MAX_PHI_SAMPLES:
        return _failure(
            "phi_grid_too_large",
            f"Requested grid has {sample_count:,} samples; limit is {_MAX_PHI_SAMPLES:,}. Increase the step or narrow the bounds.",
            channel_count=int(freq.size),
            rejected_channel_count=rejected_count,
        )

    if not np.isfinite(clean_gain) or not 0.0 < float(clean_gain) <= 1.0:
        return _failure("invalid_clean_settings", "RM-CLEAN gain must be greater than 0 and at most 1.")
    if not np.isfinite(clean_threshold_sigma) or float(clean_threshold_sigma) <= 0.0:
        return _failure("invalid_clean_settings", "RM-CLEAN threshold must be positive.")
    if int(clean_max_iterations) < 1 or int(clean_max_iterations) > 100_000:
        return _failure("invalid_clean_settings", "RM-CLEAN iterations must be between 1 and 100,000.")

    phi = phi_min + phi_step * np.arange(sample_count, dtype=float)
    polarization = q + 1j * u
    faraday = _transform(phi, lambda2, lambda0, weights * polarization, weight_sum)
    rmsf = _transform(phi, lambda2, lambda0, weights.astype(complex), weight_sum)
    amplitude = np.abs(faraday)
    coarse_peak_index = int(np.argmax(amplitude))
    peak_rm = _refined_peak(phi, amplitude, coarse_peak_index)
    peak_faraday = _transform(
        np.asarray([peak_rm]), lambda2, lambda0, weights * polarization, weight_sum
    )[0]
    peak_amplitude = float(np.abs(peak_faraday))

    model = peak_faraday * np.exp(2j * peak_rm * (lambda2 - lambda0))
    residual = polarization - model
    if has_uncertainties:
        faraday_noise = float(1.0 / np.sqrt(weight_sum))
        noise_source = "propagated_channel_uncertainties"
        chi_square = float(np.sum((residual.real / sq) ** 2 + (residual.imag / su) ** 2))
        reduced_chi_square = chi_square / max(1, (2 * int(freq.size)) - 3)
    else:
        faraday_noise = _robust_noise_from_residual(residual, weights)
        noise_source = "thin_component_residual_mad" if faraday_noise is not None else "unavailable"
        reduced_chi_square = None
    peak_snr = peak_amplitude / faraday_noise if faraday_noise is not None and faraday_noise > 0.0 else None
    uncertainty = rmsf_fwhm / (2.0 * peak_snr) if peak_snr is not None and peak_snr > 0.0 else None
    debiased = (
        float(np.sqrt(max(0.0, peak_amplitude**2 - faraday_noise**2)))
        if faraday_noise is not None
        else None
    )
    independent_samples = max(1, int(np.ceil((phi[-1] - phi[0]) / rmsf_fwhm)))
    if peak_snr is not None:
        single_trial = float(np.exp(-0.5 * peak_snr**2))
        if single_trial <= 0.0:
            false_alarm_probability = 0.0
        elif single_trial >= 1.0:
            false_alarm_probability = 1.0
        else:
            false_alarm_probability = float(-np.expm1(independent_samples * np.log1p(-single_trial)))
    else:
        false_alarm_probability = None

    reference_angle = 0.5 * float(np.angle(peak_faraday))
    intrinsic_angle = reference_angle - (peak_rm * lambda0)

    rmsf_centered = _transform(phi - peak_rm, lambda2, lambda0, weights.astype(complex), weight_sum)
    sidelobe_mask = np.abs(phi - peak_rm) > rmsf_fwhm
    rmsf_max_sidelobe = float(np.max(np.abs(rmsf_centered[sidelobe_mask]))) if np.any(sidelobe_mask) else None

    cleaned = components = np.array([], dtype=complex)
    clean_iterations = 0
    clean_cutoff = None
    if clean:
        if faraday_noise is not None:
            clean_cutoff = float(clean_threshold_sigma * faraday_noise)
            cleaned, components, _, clean_iterations = _rm_clean(
                faraday,
                phi,
                lambda2,
                lambda0,
                weights,
                rmsf_fwhm,
                clean_cutoff,
                float(clean_gain),
                int(clean_max_iterations),
            )

    warnings = ["ionospheric_rm_not_corrected", "instrumental_leakage_not_corrected"]
    if not clean:
        warnings.append("dirty_spectrum_not_rm_cleaned")
    elif faraday_noise is None:
        warnings.append("rm_clean_skipped_noise_unavailable")
    elif clean_iterations >= int(clean_max_iterations):
        warnings.append("rm_clean_iteration_limit")
    if widths is None:
        warnings.append("channel_width_inferred")
    if not has_uncertainties:
        warnings.append("channel_uncertainties_not_provided")
    if rejected_count:
        warnings.append("invalid_channels_rejected")
    if peak_snr is None or peak_snr < 8.0:
        warnings.append("low_significance_peak")
    if abs(peak_rm) >= 0.9 * max_abs_rm:
        warnings.append("peak_near_instrumental_rm_limit")
    if peak_rm <= phi[1] or peak_rm >= phi[-2]:
        warnings.append("peak_near_search_boundary")
    if phi_step > rmsf_fwhm / 4.0:
        warnings.append("faraday_grid_undersampled")
    if reduced_chi_square is not None and reduced_chi_square > 2.0:
        warnings.append("single_faraday_component_poor_fit")
    if rmsf_max_sidelobe is not None and rmsf_max_sidelobe > 0.5:
        warnings.append("high_rmsf_sidelobes")

    return RMSynthesisResult(
        status="ok",
        message="Weighted RM synthesis completed." + (" RM-CLEAN was applied." if cleaned.size else ""),
        peak_rm_rad_m2=float(peak_rm),
        peak_rm_uncertainty_rad_m2=None if uncertainty is None else float(uncertainty),
        peak_snr=None if peak_snr is None else float(peak_snr),
        false_alarm_probability=false_alarm_probability,
        peak_polarized_amplitude=peak_amplitude,
        peak_polarized_amplitude_debiased=debiased,
        faraday_noise=faraday_noise,
        noise_source=noise_source,
        polarization_angle_deg=_wrap_angle_deg(reference_angle),
        intrinsic_polarization_angle_deg=_wrap_angle_deg(intrinsic_angle),
        reference_lambda2_m2=lambda0,
        rmsf_fwhm_rad_m2=rmsf_fwhm,
        rmsf_max_sidelobe=rmsf_max_sidelobe,
        max_scale_rad_m2=max_scale,
        max_abs_rm_rad_m2=max_abs_rm,
        lambda2_min_m2=float(lambda2[0]),
        lambda2_max_m2=float(lambda2[-1]),
        reduced_chi_square=None if reduced_chi_square is None else float(reduced_chi_square),
        channel_count=int(freq.size),
        rejected_channel_count=rejected_count,
        independent_faraday_samples=independent_samples,
        phi_step_rad_m2=float(phi_step),
        phi_rad_m2=phi,
        faraday_real=faraday.real,
        faraday_imag=faraday.imag,
        polarized_amplitude=amplitude,
        rmsf_real=rmsf.real,
        rmsf_imag=rmsf.imag,
        rmsf_amplitude=np.abs(rmsf),
        cleaned_faraday_real=cleaned.real,
        cleaned_faraday_imag=cleaned.imag,
        cleaned_polarized_amplitude=np.abs(cleaned),
        clean_component_real=components.real,
        clean_component_imag=components.imag,
        clean_iterations=clean_iterations,
        clean_cutoff=clean_cutoff,
        warnings=tuple(warnings),
    )
