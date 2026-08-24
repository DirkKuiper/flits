"""A polarization analysis has to travel: into exports, snapshots, and replay."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import FullStokesWaterfall

from flits.cli import replay
from flits.exports import create_export_snapshot
from flits.session import BurstSession
from flits.web.app import SESSIONS, ActionRequest, session_action


@pytest.fixture
def measured_session(full_stokes_waterfall: FullStokesWaterfall) -> BurstSession:
    session = BurstSession.from_file(str(full_stokes_waterfall.path), dm=0.0)
    burst = full_stokes_waterfall.burst_time_idx
    session.set_event_ms(session.bin_to_ms(burst - 10), session.bin_to_ms(burst + 10))
    session.add_offpulse_ms(session.bin_to_ms(10), session.bin_to_ms(burst - 60))
    session.add_offpulse_ms(session.bin_to_ms(burst + 60), session.bin_to_ms(full_stokes_waterfall.ntime - 10))
    session.run_polarization_analysis({"min_linear_snr": 3.0})
    return session


def _artifact(snapshot, name_fragment: str) -> bytes:
    for artifact in snapshot.manifest.artifacts:
        if name_fragment in artifact.name and artifact.status == "ready":
            return snapshot.contents[artifact.name]
    raise AssertionError(f"No ready artifact matching {name_fragment!r}")


def test_the_science_json_carries_the_polarization_analysis(measured_session: BurstSession) -> None:
    snapshot = create_export_snapshot(measured_session, session_id="pol-json", include=["json"])
    payload = json.loads(_artifact(snapshot, "science.json"))

    polarization = payload["polarization"]
    assert polarization["polarization_basis"] == "coherency_linear"
    assert polarization["rm_synthesis"]["status"] == "ok"
    assert polarization["calibration_status"] == "unknown"
    assert len(polarization["freqs_mhz"]) == len(polarization["stokes_q"])


def test_the_catalog_csv_gains_rotation_measure_columns(measured_session: BurstSession) -> None:
    snapshot = create_export_snapshot(measured_session, session_id="pol-csv", include=["csv"])
    text = _artifact(snapshot, "catalog.csv").decode("utf-8")
    header, row = (line.split(",") for line in text.splitlines()[:2])
    columns = dict(zip(header, row, strict=True))

    assert columns["polarization_basis"] == "coherency_linear"
    assert columns["polarization_calibration_status"] == "unknown"
    assert float(columns["rm_rad_m2"]) == pytest.approx(measured_session.polarization.rm_synthesis["peak_rm_rad_m2"])
    assert float(columns["linear_fraction"]) > 0.5


def test_the_diagnostics_npz_carries_the_faraday_spectrum(measured_session: BurstSession) -> None:
    snapshot = create_export_snapshot(measured_session, session_id="pol-npz", include=["npz"])
    with np.load(io.BytesIO(_artifact(snapshot, "diagnostics.npz"))) as bundle:
        assert bundle["rm_phi_rad_m2"].size > 0
        assert bundle["rm_polarized_amplitude"].size == bundle["rm_phi_rad_m2"].size
        assert bundle["polarization_stokes_q"].size == bundle["polarization_freqs_mhz"].size
        peak = float(bundle["rm_phi_rad_m2"][int(np.argmax(bundle["rm_polarized_amplitude"]))])
    assert peak == pytest.approx(measured_session.polarization.rm_synthesis["peak_rm_rad_m2"], abs=20.0)


def test_a_faraday_spectrum_plot_is_planned_only_once_there_is_one(
    measured_session: BurstSession,
    full_stokes_waterfall: FullStokesWaterfall,
) -> None:
    with_analysis = create_export_snapshot(
        measured_session,
        session_id="pol-plot",
        include=["plots"],
        plot_formats=["png"],
    )
    names = {artifact.name: artifact for artifact in with_analysis.manifest.artifacts}
    faraday = next(item for name, item in names.items() if "faraday_spectrum" in name)
    assert faraday.status == "ready"
    assert with_analysis.contents[faraday.name][:4] == b"\x89PNG"

    bare = BurstSession.from_file(str(full_stokes_waterfall.path), dm=0.0)
    without = create_export_snapshot(bare, session_id="pol-plot-none", include=["plots"], plot_formats=["png"])
    omitted = next(item for item in without.manifest.artifacts if "faraday_spectrum" in item.name)
    assert omitted.status == "omitted"
    assert omitted.reason == "polarization_analysis_unavailable"


def test_replay_recomputes_the_rotation_measure_from_a_snapshot(
    measured_session: BurstSession,
    tmp_path: Path,
    capsys,
) -> None:
    snapshot_path = tmp_path / "session.json"
    snapshot_path.write_text(json.dumps(measured_session.snapshot_dict()))

    assert replay([str(snapshot_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert "polarization" in report["recomputed"]
    assert report["polarization"]["polarization_basis"] == "coherency_linear"
    assert report["polarization"]["calibration_status"] == "unknown"
    assert report["polarization"]["peak_rm_rad_m2"] == pytest.approx(
        measured_session.polarization.rm_synthesis["peak_rm_rad_m2"], rel=1e-9
    )


def test_replay_leaves_polarization_alone_when_the_snapshot_has_none(
    full_stokes_waterfall: FullStokesWaterfall,
    tmp_path: Path,
    capsys,
) -> None:
    session = BurstSession.from_file(str(full_stokes_waterfall.path), dm=0.0)
    snapshot_path = tmp_path / "bare.json"
    snapshot_path.write_text(json.dumps(session.snapshot_dict()))

    assert replay([str(snapshot_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "polarization" not in report["recomputed"]
    assert report["polarization"] is None


class TestWebApi:
    def teardown_method(self) -> None:
        SESSIONS.clear()

    def test_the_action_endpoint_measures_and_returns_the_result(self, measured_session: BurstSession) -> None:
        SESSIONS["pol-session"] = measured_session
        payload = session_action(
            "pol-session",
            ActionRequest(type="run_polarization_analysis", payload={"min_linear_snr": 3.0}),
        )
        view = payload["view"]
        assert view["polarization"]["rm_synthesis"]["status"] == "ok"
        assert view["polarization_capability"]["available"]
        assert view["polarization_settings"]["min_linear_snr"] == 3.0
        assert "_override" not in view["polarization_capability"]

    def test_an_unusable_file_is_a_client_error_with_the_reason_attached(self, synthetic_waterfall) -> None:
        from fastapi import HTTPException

        session = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0)
        session.add_offpulse_ms(session.bin_to_ms(0), session.bin_to_ms(64))
        SESSIONS["stokes-i-session"] = session

        with pytest.raises(HTTPException) as excinfo:
            session_action(
                "stokes-i-session",
                ActionRequest(type="run_polarization_analysis", payload={}),
            )
        assert excinfo.value.status_code == 400
        assert "insufficient_products" in excinfo.value.detail
