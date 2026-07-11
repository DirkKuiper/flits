import numpy as np
import pytest
import json

from flits.analysis.polarization import run_rm_synthesis
from flits.web.app import RMSynthesisRequest, rm_synthesis


def _synthetic_qu(rm_rad_m2: float, *, seed: int = 31) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    freqs_mhz = np.linspace(900.0, 1500.0, 256)
    lambda2 = (299_792_458.0 / (freqs_mhz * 1e6)) ** 2
    polarization = np.exp(2j * (0.23 + rm_rad_m2 * lambda2))
    noise = np.random.default_rng(seed).normal(0.0, 0.015, size=(2, freqs_mhz.size))
    return freqs_mhz, polarization.real + noise[0], polarization.imag + noise[1]


def test_rm_synthesis_recovers_injected_faraday_depth() -> None:
    freqs, q, u = _synthetic_qu(347.0)
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=0.015,
        sigma_u=0.015,
        phi_min_rad_m2=-800.0,
        phi_max_rad_m2=800.0,
        phi_step_rad_m2=1.0,
    )

    assert result.status == "ok"
    assert result.peak_rm_rad_m2 == pytest.approx(347.0, abs=1.0)
    assert result.channel_count == 256
    assert result.rmsf_fwhm_rad_m2 is not None and result.rmsf_fwhm_rad_m2 > 0.0
    assert result.peak_rm_uncertainty_rad_m2 is not None
    assert "dirty_spectrum_not_rm_cleaned" in result.warnings
    assert result.phi_rad_m2.shape == result.polarized_amplitude.shape


def test_rm_synthesis_rejects_unusable_inputs() -> None:
    mismatch = run_rm_synthesis(
        freqs_mhz=np.arange(8.0) + 1000.0,
        stokes_q=np.ones(7),
        stokes_u=np.ones(8),
    )
    assert mismatch.status == "invalid_shape"

    too_few = run_rm_synthesis(
        freqs_mhz=np.arange(7.0) + 1000.0,
        stokes_q=np.ones(7),
        stokes_u=np.ones(7),
    )
    assert too_few.status == "insufficient_channels"


def test_rm_synthesis_api_returns_json_ready_spectrum() -> None:
    freqs, q, u = _synthetic_qu(-125.0)
    payload = rm_synthesis(
        RMSynthesisRequest(
            freqs_mhz=freqs.tolist(),
            stokes_q=q.tolist(),
            stokes_u=u.tolist(),
            sigma_q=0.015,
            sigma_u=0.015,
            phi_min_rad_m2=-300.0,
            phi_max_rad_m2=300.0,
            phi_step_rad_m2=1.0,
        )
    )

    assert payload["status"] == "ok"
    assert payload["peak_rm_rad_m2"] == pytest.approx(-125.0, abs=1.0)
    assert payload["method"] == "weighted_dirty_rm_synthesis"
    assert payload["method_version"] == "2.0"
    assert payload["false_alarm_probability"] is not None
    assert payload["noise_source"] == "propagated_channel_uncertainties"
    assert payload["rmsf_real"]
    assert len(payload["phi_rad_m2"]) == len(payload["polarized_amplitude"])
    json.dumps(payload, allow_nan=False)


def test_rm_synthesis_refines_peak_below_grid_spacing_and_recovers_angles() -> None:
    freqs_mhz = np.linspace(850.0, 1650.0, 384)
    lambda2 = (299_792_458.0 / (freqs_mhz * 1e6)) ** 2
    injected_rm = 213.37
    intrinsic_angle = np.deg2rad(31.0)
    polarization = 2.5 * np.exp(2j * (intrinsic_angle + injected_rm * lambda2))
    result = run_rm_synthesis(
        freqs_mhz=freqs_mhz,
        stokes_q=polarization.real,
        stokes_u=polarization.imag,
        sigma_q=0.02,
        sigma_u=0.02,
        channel_width_mhz=2.0,
        phi_min_rad_m2=-500.0,
        phi_max_rad_m2=500.0,
        phi_step_rad_m2=5.0,
    )

    assert result.status == "ok"
    assert result.peak_rm_rad_m2 == pytest.approx(injected_rm, abs=0.15)
    assert result.peak_rm_rad_m2 != pytest.approx(round(injected_rm / 5.0) * 5.0)
    assert result.intrinsic_polarization_angle_deg == pytest.approx(31.0, abs=0.1)
    expected_reference = np.degrees(intrinsic_angle + injected_rm * result.reference_lambda2_m2) % 180.0
    assert result.polarization_angle_deg == pytest.approx(expected_reference, abs=0.1)


def test_rm_synthesis_propagates_declared_noise_and_reports_fit_quality() -> None:
    freqs, clean_q, clean_u = _synthetic_qu(88.0, seed=11)
    sigma = 0.015
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=clean_q,
        stokes_u=clean_u,
        sigma_q=sigma,
        sigma_u=sigma,
        phi_min_rad_m2=-300.0,
        phi_max_rad_m2=300.0,
        phi_step_rad_m2=1.0,
    )

    assert result.faraday_noise == pytest.approx(sigma / np.sqrt(freqs.size))
    assert result.reduced_chi_square == pytest.approx(1.0, abs=0.25)
    assert result.peak_snr is not None and result.peak_snr > 100.0
    assert result.false_alarm_probability is not None and result.false_alarm_probability < 1e-20
    assert result.peak_polarized_amplitude_debiased is not None


def test_rm_synthesis_estimates_noise_when_uncertainties_are_absent() -> None:
    freqs, q, u = _synthetic_qu(-42.0, seed=19)
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        phi_min_rad_m2=-200.0,
        phi_max_rad_m2=200.0,
        phi_step_rad_m2=1.0,
    )

    assert result.status == "ok"
    assert result.noise_source == "thin_component_residual_mad"
    assert result.faraday_noise is not None and result.faraday_noise > 0.0
    assert result.peak_snr is not None
    assert result.reduced_chi_square is None
    assert "channel_uncertainties_not_provided" in result.warnings


def test_rm_synthesis_clean_produces_restored_and_component_spectra() -> None:
    freqs, q, u = _synthetic_qu(176.0, seed=7)
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=0.015,
        sigma_u=0.015,
        phi_min_rad_m2=-600.0,
        phi_max_rad_m2=600.0,
        phi_step_rad_m2=2.0,
        clean=True,
        clean_gain=0.1,
        clean_threshold_sigma=3.0,
        clean_max_iterations=500,
    )

    assert result.status == "ok"
    assert result.clean_iterations > 0
    assert result.clean_cutoff == pytest.approx(3.0 * result.faraday_noise)
    assert result.cleaned_polarized_amplitude.shape == result.phi_rad_m2.shape
    assert result.clean_component_real.shape == result.phi_rad_m2.shape
    assert np.count_nonzero(result.clean_component_real) > 0
    assert "dirty_spectrum_not_rm_cleaned" not in result.warnings
    assert result.to_dict()["clean_applied"] is True


def test_channel_width_controls_bandwidth_depolarization_limit_across_gaps() -> None:
    full = np.linspace(900.0, 1500.0, 256)
    freqs = np.concatenate((full[:80], full[150:]))
    lambda2 = (299_792_458.0 / (freqs * 1e6)) ** 2
    polarization = np.exp(2j * 50.0 * lambda2)
    width_mhz = float(full[1] - full[0])
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=polarization.real,
        stokes_u=polarization.imag,
        sigma_q=0.02,
        sigma_u=0.02,
        channel_width_mhz=width_mhz,
    )
    lambda2_width = (
        (299_792_458.0 / ((freqs - width_mhz / 2.0) * 1e6)) ** 2
        - (299_792_458.0 / ((freqs + width_mhz / 2.0) * 1e6)) ** 2
    )

    assert result.status == "ok"
    assert result.max_abs_rm_rad_m2 == pytest.approx(np.sqrt(3.0) / np.max(lambda2_width))
    assert "channel_width_inferred" not in result.warnings


def test_rm_synthesis_rejects_invalid_optional_shapes_and_settings_without_raising() -> None:
    freqs, q, u = _synthetic_qu(10.0)
    invalid_sigma = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=np.ones(3),
        sigma_u=0.1,
    )
    invalid_width = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        channel_width_mhz=np.ones(3),
    )
    invalid_clean = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        clean=True,
        clean_gain=1.1,
    )

    assert invalid_sigma.status == "invalid_uncertainty"
    assert invalid_width.status == "invalid_channel_width"
    assert invalid_clean.status == "invalid_clean_settings"


def test_rm_synthesis_filters_invalid_channels_and_reports_count() -> None:
    freqs, q, u = _synthetic_qu(64.0)
    freqs[0] = np.nan
    q[1] = np.inf
    u[2] = np.nan
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=0.015,
        sigma_u=0.015,
        phi_min_rad_m2=-200.0,
        phi_max_rad_m2=200.0,
        phi_step_rad_m2=1.0,
    )

    assert result.status == "ok"
    assert result.channel_count == freqs.size - 3
    assert result.rejected_channel_count == 3
    assert "invalid_channels_rejected" in result.warnings


def test_rm_synthesis_reports_weight_concentration_and_measured_rmsf_width() -> None:
    freqs, q, u = _synthetic_qu(125.0)
    sigma = np.ones(freqs.size)
    sigma[:4] = 0.01
    result = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=sigma,
        sigma_u=sigma,
        channel_width_mhz=float(freqs[1] - freqs[0]),
        phi_min_rad_m2=-1000.0,
        phi_max_rad_m2=1000.0,
        phi_step_rad_m2=2.0,
    )

    assert result.status == "ok"
    assert result.effective_channel_count is not None and result.effective_channel_count < 8.0
    assert result.maximum_weight_fraction is not None and result.maximum_weight_fraction > 0.2
    assert result.rmsf_fwhm_rad_m2 > result.rmsf_fwhm_theoretical_rad_m2
    assert "channel_weights_highly_concentrated" in result.warnings
    assert "effective_lambda2_coverage_reduced" in result.warnings


def test_rmsf_measurement_is_not_coarsened_by_large_instrumental_rm_limit() -> None:
    freqs, q, u = _synthetic_qu(125.0)
    regular = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=0.02,
        sigma_u=0.02,
        channel_width_mhz=float(freqs[1] - freqs[0]),
        phi_min_rad_m2=-500.0,
        phi_max_rad_m2=500.0,
        phi_step_rad_m2=1.0,
    )
    narrow_channels = run_rm_synthesis(
        freqs_mhz=freqs,
        stokes_q=q,
        stokes_u=u,
        sigma_q=0.02,
        sigma_u=0.02,
        channel_width_mhz=0.001,
        phi_min_rad_m2=-500.0,
        phi_max_rad_m2=500.0,
        phi_step_rad_m2=1.0,
    )

    assert regular.status == narrow_channels.status == "ok"
    assert narrow_channels.max_abs_rm_rad_m2 > 1_000_000.0
    assert narrow_channels.rmsf_fwhm_rad_m2 == pytest.approx(regular.rmsf_fwhm_rad_m2, rel=1e-8)
