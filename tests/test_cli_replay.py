"""Tests for the headless `flits replay` command.

Replay is what makes the reproducibility claim checkable: a snapshot records
every interactive decision, and replaying it reopens the burst and recomputes
the measurements without a browser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flits.cli import main, replay
from flits.session import BurstSession


@pytest.fixture
def snapshot_path(tmp_path: Path, synthetic_waterfall) -> Path:
    """A saved session snapshot with selections and computed measurements."""
    session = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0)
    session.set_event_ms(100.0, 160.0)
    session.add_offpulse_ms(0.0, 50.0)
    session.compute_properties()

    path = tmp_path / "synthetic_flits_session.json"
    path.write_text(json.dumps(session.snapshot_dict(), indent=2), encoding="utf-8")
    return path


def test_replay_reopens_the_burst_and_recomputes(snapshot_path: Path, capsys) -> None:
    assert replay([str(snapshot_path)]) == 0

    out = capsys.readouterr().out
    assert "snapshot" in out
    assert "burst" in out
    assert "results" in out


def test_replay_json_report_carries_the_measurements(snapshot_path: Path, capsys) -> None:
    assert replay([str(snapshot_path), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["burst_file"].endswith(".fil")
    assert "results" in report["recomputed"]
    assert report["measurements"] is not None


def test_replay_check_passes_on_an_unmodified_snapshot(snapshot_path: Path, capsys) -> None:
    """Recomputing a snapshot must reproduce the numbers it was saved with."""
    assert replay([str(snapshot_path), "--check"]) == 0
    assert "OK" in capsys.readouterr().out


def test_replay_check_detects_altered_measurements(snapshot_path: Path, capsys) -> None:
    payload = json.loads(snapshot_path.read_text())
    assert payload["results"] is not None
    payload["results"]["snr"] = float(payload["results"].get("snr") or 1.0) * 2.0 + 1.0
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    assert replay([str(snapshot_path), "--check"]) == 1

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "snr" in out


def test_replay_writes_an_export_bundle(snapshot_path: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundle"
    assert replay([str(snapshot_path), "--export", str(destination), "--include", "json"]) == 0

    manifest_path = destination / "manifest.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text())
    ready = [a["name"] for a in manifest["artifacts"] if a["status"] == "ready"]
    assert ready, "expected at least one ready artifact"
    for name in ready:
        assert (destination / name).is_file()


def test_replay_reports_a_missing_snapshot_clearly(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        replay([str(tmp_path / "nope.json")])
    assert excinfo.value.code == 2
    assert "Snapshot not found" in capsys.readouterr().err


def test_replay_rejects_a_malformed_snapshot(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        replay([str(bad)])
    assert excinfo.value.code == 2
    assert "not valid JSON" in capsys.readouterr().err


class TestDispatch:
    def test_version_flag(self, capsys) -> None:
        assert main(["--version"]) == 0
        assert capsys.readouterr().out.startswith("flits ")

    def test_help_lists_the_commands(self, capsys) -> None:
        assert main(["--help"]) == 0
        out = capsys.readouterr().out
        assert "replay" in out
        assert "serve" in out

    def test_replay_dispatches_through_main(self, snapshot_path: Path, capsys) -> None:
        assert main(["replay", str(snapshot_path), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["burst_file"]

    def test_bare_server_options_still_reach_the_server(self, monkeypatch) -> None:
        """`flits --data-dir DIR --port N` must keep working without a subcommand."""
        captured: dict[str, object] = {}

        def fake_run(app: str, **kwargs: object) -> None:
            captured["app"] = app
            captured.update(kwargs)

        monkeypatch.setattr("uvicorn.run", fake_run)
        assert main(["--data-dir", ".", "--host", "0.0.0.0", "--port", "9999"]) == 0

        assert captured["app"] == "flits.web.app:app"
        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 9999

    def test_serve_subcommand_is_equivalent(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        def fake_run(app: str, **kwargs: object) -> None:
            captured.update(kwargs)

        monkeypatch.setattr("uvicorn.run", fake_run)
        assert main(["serve", "--port", "8123"]) == 0
        assert captured["port"] == 8123
