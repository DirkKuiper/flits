"""In-session polarimetry: an RM measured from the session's own selections."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import FullStokesWaterfall, write_full_stokes_filterbank

from flits.io.errors import PolarizationUnavailableError
from flits.session import BurstSession


def _session(waterfall: FullStokesWaterfall, **kwargs) -> BurstSession:
    """Open a session on `waterfall` with the burst and its noise reference selected."""
    session = BurstSession.from_file(str(waterfall.path), dm=0.0, **kwargs)
    burst = waterfall.burst_time_idx
    session.set_event_ms(session.bin_to_ms(burst - 10), session.bin_to_ms(burst + 10))
    session.add_offpulse_ms(session.bin_to_ms(10), session.bin_to_ms(burst - 60))
    session.add_offpulse_ms(session.bin_to_ms(burst + 60), session.bin_to_ms(waterfall.ntime - 10))
    return session


@pytest.fixture
def polarized_session(full_stokes_waterfall: FullStokesWaterfall) -> BurstSession:
    return _session(full_stokes_waterfall)


def test_capability_reports_what_the_file_can_and_cannot_do(
    polarized_session: BurstSession, synthetic_waterfall
) -> None:
    capability = polarized_session.polarization_capability()
    assert capability == {
        "supported": True,
        "available": True,
        "products": 4,
        "basis": "coherency_linear",
        "basis_source": "preset:nrt",
        "loaded": False,
        "reason": None,
    }

    stokes_i_only = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0)
    assert stokes_i_only.polarization_capability()["reason"] == "insufficient_products"
    assert not stokes_i_only.polarization_capability()["available"]


def test_run_polarization_analysis_recovers_the_injected_rotation_measure(
    polarized_session: BurstSession,
    full_stokes_waterfall: FullStokesWaterfall,
) -> None:
    result = polarized_session.run_polarization_analysis({"min_linear_snr": 3.0})

    assert result.polarization_basis == "coherency_linear"
    assert result.polarization_basis_source == "preset:nrt"
    assert result.rm_synthesis["status"] == "ok"
    assert result.rm_synthesis["peak_rm_rad_m2"] == pytest.approx(full_stokes_waterfall.rm_rad_m2, abs=1.0)
    assert result.linear_fraction == pytest.approx(full_stokes_waterfall.linear_fraction, abs=0.1)
    assert result.event_bins == [polarized_session.event_start, polarized_session.event_end]
    assert result.offpulse_regions == [list(region) for region in polarized_session.offpulse_regions]
    assert polarized_session.polarization is result


def test_the_analysis_is_driven_by_the_session_mask_not_by_imported_arrays(polarized_session: BurstSession) -> None:
    unmasked = polarized_session.run_polarization_analysis({"min_linear_snr": 3.0})
    for channel in range(0, 20):
        polarized_session.mask_channel_freq(float(polarized_session.freqs[channel]))
    masked = polarized_session.run_polarization_analysis({"min_linear_snr": 3.0})

    assert len(masked.freqs_mhz) < len(unmasked.freqs_mhz)
    assert masked.masked_channels == list(range(0, 20))
    assert not set(masked.channel_indices.tolist()) & set(range(0, 20))


def test_an_uncalibrated_run_refuses_to_call_itself_a_source_rm(polarized_session: BurstSession) -> None:
    uncalibrated = polarized_session.run_polarization_analysis({"min_linear_snr": 3.0})
    assert uncalibrated.calibration_status == "unknown"
    assert uncalibrated.status == "calibration_required"
    assert "polarization_calibration_required" in uncalibrated.warnings

    calibrated = polarized_session.run_polarization_analysis(
        {"min_linear_snr": 3.0, "calibration_confirmed": True},
    )
    assert calibrated.calibration_status == "calibrated"
    assert calibrated.status == "ok"
    assert "polarization_calibration_required" not in calibrated.warnings


def test_polarization_needs_an_off_pulse_region_and_an_event_window(full_stokes_waterfall: FullStokesWaterfall) -> None:
    session = BurstSession.from_file(str(full_stokes_waterfall.path), dm=0.0)
    with pytest.raises(ValueError, match="off-pulse region"):
        session.run_polarization_analysis()


def test_a_stokes_i_only_file_fails_with_an_actionable_reason(synthetic_waterfall) -> None:
    session = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0)
    session.add_offpulse_ms(session.bin_to_ms(0), session.bin_to_ms(64))
    with pytest.raises(PolarizationUnavailableError) as excinfo:
        session.run_polarization_analysis()
    assert excinfo.value.reason == "insufficient_products"


def test_the_cube_follows_the_session_when_the_dm_is_retuned(polarized_session: BurstSession) -> None:
    before = polarized_session.load_stokes_cube()
    np.testing.assert_allclose(before[0], polarized_session.data, rtol=1e-4, atol=1e-4)

    polarized_session.set_dm(12.0)
    after = polarized_session.load_stokes_cube()
    # Retuning does not re-read the file, and Stokes I still equals `data`.
    np.testing.assert_allclose(after[0], polarized_session.data, rtol=1e-4, atol=1e-4)
    assert not np.allclose(before[0], after[0])


def test_changing_the_dm_discards_a_stale_polarization_result(polarized_session: BurstSession) -> None:
    polarized_session.run_polarization_analysis({"min_linear_snr": 3.0})
    assert polarized_session.polarization is not None
    polarized_session.set_dm(3.0)
    assert polarized_session.polarization is None


def test_an_explicit_basis_override_reloads_the_cube(polarized_session: BurstSession) -> None:
    linear = polarized_session.run_polarization_analysis({"min_linear_snr": 3.0})
    circular = polarized_session.run_polarization_analysis(
        {"min_linear_snr": 3.0, "polarization_basis": "coherency_circular"},
    )
    assert circular.polarization_basis == "coherency_circular"
    assert circular.polarization_basis_source == "config_override"
    # Reading Q as V destroys the Faraday signal the linear reading recovers.
    assert circular.linear_fraction < linear.linear_fraction


def test_polarization_survives_a_snapshot_round_trip_and_replays_identically(polarized_session: BurstSession) -> None:
    result = polarized_session.run_polarization_analysis({"min_linear_snr": 3.0, "phi_max_rad_m2": 900.0})
    snapshot = polarized_session.snapshot_dict()

    assert snapshot["polarization"]["rm_synthesis"]["peak_rm_rad_m2"] == pytest.approx(
        result.rm_synthesis["peak_rm_rad_m2"]
    )
    assert snapshot["polarization_settings"]["phi_max_rad_m2"] == 900.0

    restored = BurstSession.from_snapshot(snapshot)
    assert restored.polarization is not None
    assert restored.polarization.rm_synthesis["peak_rm_rad_m2"] == pytest.approx(result.rm_synthesis["peak_rm_rad_m2"])
    assert restored.polarization_settings.phi_max_rad_m2 == 900.0

    recomputed = restored.run_polarization_analysis()
    assert recomputed.rm_synthesis["peak_rm_rad_m2"] == pytest.approx(result.rm_synthesis["peak_rm_rad_m2"], rel=1e-9)


def test_a_snapshot_carries_an_explicit_basis_override_back(tmp_path: Path) -> None:
    session = _session(
        write_full_stokes_filterbank(tmp_path / "override.fil", telescope_id=0),
        telescope="generic",
        polarization_basis="coherency_linear",
    )
    session.run_polarization_analysis({"min_linear_snr": 3.0})
    snapshot = session.snapshot_dict()
    assert snapshot["polarization_basis"] == "coherency_linear"

    restored = BurstSession.from_snapshot(snapshot)
    assert restored.config.polarization_basis == "coherency_linear"
    assert restored.run_polarization_analysis().rm_synthesis["status"] == "ok"
