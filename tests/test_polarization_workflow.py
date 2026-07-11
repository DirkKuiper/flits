from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from flits.analysis.polarization import extract_normalized_linear_spectrum, run_rm_synthesis


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_full_stokes(*, rm_rad_m2: float = 241.0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(804)
    channels, time_bins = 96, 640
    freqs_mhz = np.linspace(1000.0, 1700.0, channels)
    lambda2 = np.square(299_792_458.0 / (freqs_mhz * 1e6))
    stokes = rng.normal(0.0, 1.0, size=(4, channels, time_bins))
    phase = 2.0 * (0.37 + rm_rad_m2 * lambda2)
    amplitude = 8.0 + 5.0 * np.sin(np.linspace(0.0, np.pi, channels))
    event = slice(300, 324)
    stokes[0, :, event] += amplitude[:, None]
    stokes[1, :, event] += (0.85 * amplitude * np.cos(phase))[:, None]
    stokes[2, :, event] += (0.85 * amplitude * np.sin(phase))[:, None]
    stokes[3, :, event] += (0.08 * amplitude)[:, None]
    return stokes, freqs_mhz


def test_extract_normalized_linear_spectrum_recovers_rm_from_full_stokes() -> None:
    stokes, freqs = _synthetic_full_stokes()
    channel_mask = np.zeros(freqs.size, dtype=bool)
    channel_mask[[4, 17, 88]] = True
    spectrum = extract_normalized_linear_spectrum(
        stokes_iquv=stokes,
        freqs_mhz=freqs,
        event_bins=(300, 324),
        offpulse_regions=((20, 260), (380, 620)),
        channel_mask=channel_mask,
        spectral_channels=(2, 93),
        channel_width_mhz=float(freqs[1] - freqs[0]),
        min_linear_snr=5.0,
        calibration_status="calibrated",
    )
    result = run_rm_synthesis(
        freqs_mhz=spectrum.freqs_mhz,
        stokes_q=spectrum.stokes_q,
        stokes_u=spectrum.stokes_u,
        sigma_q=spectrum.sigma_q,
        sigma_u=spectrum.sigma_u,
        channel_width_mhz=spectrum.channel_width_mhz,
        phi_min_rad_m2=-500.0,
        phi_max_rad_m2=500.0,
        phi_step_rad_m2=1.0,
    )

    assert spectrum.status == "ok"
    assert spectrum.calibration_status == "calibrated"
    assert spectrum.normalization == "normalized_stokes_q_over_l_and_u_over_l"
    assert spectrum.offpulse_block_count == 20
    assert spectrum.freqs_mhz.size >= 80
    assert result.status == "ok"
    assert result.peak_rm_rad_m2 == pytest.approx(241.0, abs=3.0)


def test_extract_normalized_linear_spectrum_fails_closed_on_unknown_calibration() -> None:
    stokes, freqs = _synthetic_full_stokes()
    spectrum = extract_normalized_linear_spectrum(
        stokes_iquv=stokes,
        freqs_mhz=freqs,
        event_bins=(300, 324),
        offpulse_regions=((20, 260), (380, 620)),
        channel_width_mhz=float(freqs[1] - freqs[0]),
        min_linear_snr=5.0,
        calibration_status="unknown",
    )
    payload = spectrum.to_rm_input(provenance={"source": "synthetic"})

    assert spectrum.status == "calibration_required"
    assert "polarization_calibration_required" in spectrum.warnings
    assert payload["calibration_status"] == "unknown"
    assert payload["provenance"] == {"source": "synthetic"}
    assert len(payload["freqs_mhz"]) == len(payload["stokes_q"])


def test_extract_normalized_linear_spectrum_validates_selection_and_channel_count() -> None:
    stokes, freqs = _synthetic_full_stokes()
    with pytest.raises(ValueError, match="At least one explicit off-pulse"):
        extract_normalized_linear_spectrum(
            stokes_iquv=stokes,
            freqs_mhz=freqs,
            event_bins=(300, 324),
            offpulse_regions=(),
        )
    with pytest.raises(ValueError, match="at least 8"):
        extract_normalized_linear_spectrum(
            stokes_iquv=stokes,
            freqs_mhz=freqs,
            event_bins=(300, 324),
            offpulse_regions=((20, 260), (380, 620)),
            spectral_channels=(0, 6),
            min_linear_snr=0.0,
        )


def test_documented_rmtools_example_reproduces_reference_end_to_end() -> None:
    rm_input = json.loads((ROOT / "docs/examples/rmtools-reference-input.json").read_text(encoding="utf-8"))
    summary = json.loads((ROOT / "docs/examples/rmtools-reference-summary.json").read_text(encoding="utf-8"))
    result = run_rm_synthesis(
        freqs_mhz=np.asarray(rm_input["freqs_mhz"]),
        stokes_q=np.asarray(rm_input["stokes_q"]),
        stokes_u=np.asarray(rm_input["stokes_u"]),
        sigma_q=np.asarray(rm_input["sigma_q"]),
        sigma_u=np.asarray(rm_input["sigma_u"]),
        channel_width_mhz=float(rm_input["channel_width_mhz"]),
        phi_min_rad_m2=-600.0,
        phi_max_rad_m2=600.0,
        phi_step_rad_m2=1.0,
        clean=True,
    )

    assert rm_input["calibration_status"] == "calibrated"
    assert rm_input["provenance"]["data_kind"] == "official synthetic RM-synthesis validation spectrum"
    assert summary["interpretation_status"] == "validation_passed"
    assert all(summary["validation_checks"].values())
    assert result.status == "ok"
    assert result.channel_count == 288
    assert result.peak_rm_rad_m2 == pytest.approx(summary["rmtools_reference"]["peak_rm_rad_m2"], abs=0.5)
    assert result.peak_snr == pytest.approx(summary["rmtools_reference"]["peak_snr"], abs=0.1)
