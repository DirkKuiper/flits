import numpy as np
import pytest

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
    assert len(payload["phi_rad_m2"]) == len(payload["polarized_amplitude"])
