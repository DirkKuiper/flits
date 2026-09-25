"""Verify attribution across software upgrades, old files, and independent exports."""

from __future__ import annotations

import copy
import csv
import io
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
import pytest
from PIL import Image

import flits.provenance as provenance
from flits.analysis.fitting.fitburst_adapter import ModelFitResult
from flits.cli import replay
from flits.exports import create_export_snapshot
from flits.models import ModelFitDiagnostics
from flits.session import BurstSession


@pytest.fixture
def session(synthetic_waterfall, monkeypatch):
    monkeypatch.setattr(
        provenance, "_process_environment", lambda: {"flits_version": "old", "packages": {"fitburst": {"version": "1"}}}
    )
    result = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0)
    result.set_event_ms(100, 160)
    result.add_offpulse_ms(0, 50)
    result.compute_properties()
    return result


def upgrade(monkeypatch):
    monkeypatch.setattr(
        provenance, "_process_environment", lambda: {"flits_version": "new", "packages": {"fitburst": {"version": "2"}}}
    )


def install_fake_fit(monkeypatch, status="ok"):
    result = ModelFitResult(
        status=status,
        message=None,
        width_ms_model=2.0,
        width_uncertainty_ms=0.1,
        tau_sc_ms=0.5,
        tau_uncertainty_ms=0.1,
        diagnostics=ModelFitDiagnostics(status=status, message=None, fitter="fitburst", component_count=1),
    )
    monkeypatch.setattr("flits.session.fit_model_selected_band", lambda **kwargs: result)


def test_reopening_and_exporting_never_relabel_old_results(session, monkeypatch):
    session.compute_widths()
    install_fake_fit(monkeypatch)
    session.fit_model()
    old = session.snapshot_dict()
    upgrade(monkeypatch)
    restored = BurstSession.from_snapshot(old)
    saved = restored.snapshot_dict()["software_provenance"]
    assert saved["products"]["results"] == old["software_provenance"]["products"]["results"]
    assert saved["products"]["model_fit"] == old["software_provenance"]["products"]["model_fit"]
    assert saved["saved_with"] != saved["products"]["results"]["environment_id"]
    assert restored.provenance_warnings()
    restored.compute_properties()
    fresh = restored.software_provenance()
    assert fresh["products"]["results"]["environment_id"] == fresh["saved_with"]
    assert fresh["products"]["model_fit"] == saved["products"]["model_fit"]
    assert fresh["products"]["model_fit_values"] == saved["products"]["model_fit_values"]
    assert restored.results.width_ms_model == 2.0
    # Exporting is also read-only with respect to stored computation origins.
    exported = create_export_snapshot(restored, session_id="test", include=["json"])
    assert exported.manifest.software_provenance["products"] == fresh["products"]
    restored.set_event_ms(110, 150)
    assert "results" not in restored.software_provenance()["products"]
    assert "model_fit" not in restored.software_provenance()["products"]


def test_failed_fit_does_not_relabel_preserved_fit_values(session, monkeypatch):
    install_fake_fit(monkeypatch)
    session.fit_model()
    old = session.snapshot_dict()
    upgrade(monkeypatch)
    restored = BurstSession.from_snapshot(old)
    install_fake_fit(monkeypatch, "failed")
    restored.fit_model()
    product = restored.software_provenance()["products"]
    assert product["model_fit"]["environment_id"] == restored.provenance.runtime_id
    assert product["model_fit_values"] == old["software_provenance"]["products"]["model_fit_values"]


def test_new_width_annotation_preserves_old_core_origin(session, monkeypatch):
    old = session.snapshot_dict()
    upgrade(monkeypatch)
    restored = BurstSession.from_snapshot(old)
    restored.compute_widths()
    products = restored.software_provenance()["products"]
    result = products["results"]
    assert result["environment_id"] == old["software_provenance"]["products"]["results"]["environment_id"]
    assert result["inputs"]["width_analysis"] == products["width_analysis"]
    assert products["width_analysis"]["environment_id"] == restored.provenance.runtime_id
    restored.clear_width_analysis()
    assert "width_analysis" not in restored.provenance.products["results"]["inputs"]


def test_new_fit_annotation_preserves_old_temporal_origin(session, monkeypatch):
    session.run_temporal_structure_analysis(8)
    old = session.snapshot_dict()
    upgrade(monkeypatch)
    restored = BurstSession.from_snapshot(old)
    install_fake_fit(monkeypatch)
    restored.fit_model()
    products = restored.software_provenance()["products"]
    assert (
        products["temporal_structure"]["environment_id"]
        == old["software_provenance"]["products"]["temporal_structure"]["environment_id"]
    )
    assert products["temporal_structure"]["inputs"]["model_fit_annotation"] == products["model_fit"]
    assert any("temporal_structure" in warning for warning in restored.provenance_warnings())


def test_legacy_provenance_remains_unknown_until_recomputed(session):
    old = session.snapshot_dict()
    old.pop("software_provenance")
    old["schema_version"] = "1.6"
    restored = BurstSession.from_snapshot(old)
    assert restored.software_provenance()["products"]["results"]["environment_id"] is None
    restored = BurstSession.from_snapshot(restored.snapshot_dict())
    assert restored.software_provenance()["products"]["results"]["environment_id"] is None
    restored.compute_properties()
    assert restored.software_provenance()["products"]["results"]["environment_id"] == restored.provenance.runtime_id


@pytest.mark.parametrize("version", ["2.0", "1.99", "bad", "0.9"])
def test_future_or_invalid_schema_rejected_before_loading(session, version):
    snapshot = session.snapshot_dict()
    snapshot["schema_version"] = version
    with pytest.raises(ValueError, match="Unsupported session schema"):
        BurstSession.from_snapshot(snapshot, loader=lambda *a, **kw: pytest.fail("opened unsupported data"))
    with pytest.raises(ValueError, match="Unsupported session schema"):
        BurstSession.from_snapshot(replace(session.to_snapshot(), schema_version=version))


def test_corrupt_provenance_rejected(session):
    snapshot = session.snapshot_dict()
    payload = snapshot["software_provenance"]
    payload["environments"][payload["saved_with"]]["flits_version"] = "altered"
    with pytest.raises(ValueError, match="identity"):
        BurstSession.from_snapshot(snapshot)


def test_all_exports_carry_readable_provenance(session):
    exported = create_export_snapshot(
        session,
        session_id="test",
        include=["json", "csv", "npz", "plots", "window"],
        window_formats=["npz", "fil"],
    )
    expected = session.software_provenance()
    assert exported.manifest.to_dict()["software_provenance"] == expected
    extensions = set()
    for name, content in exported.contents.items():
        suffix = Path(name).suffix
        extensions.add(suffix)
        if suffix == ".json":
            assert json.loads(content)["software_provenance"] == expected
        elif suffix == ".csv":
            row = next(csv.DictReader(io.StringIO(content.decode())))
            assert json.loads(row["software_provenance_json"]) == expected
        elif suffix == ".npz":
            with np.load(io.BytesIO(content), allow_pickle=False) as arrays:
                for key in arrays.files:
                    assert not arrays[key].dtype.hasobject
                if "software_provenance_json" in arrays:
                    actual = json.loads(str(arrays["software_provenance_json"]))
                else:
                    actual = json.loads(str(arrays["window_metadata_json"]))["software_provenance"]
                assert actual == expected
        elif suffix == ".png":
            with Image.open(io.BytesIO(content)) as image:
                assert json.loads(image.info["Description"]) == expected
        elif suffix == ".svg":
            root = ElementTree.fromstring(content)
            description = root.find(".//{http://purl.org/dc/elements/1.1/}description")
            assert json.loads(description.text) == expected
        elif suffix == ".fil":
            assert name.removesuffix(".fil") + ".meta.json" in exported.contents
    assert extensions == {".json", ".csv", ".npz", ".png", ".svg", ".fil"}
    # Returned dictionaries cannot mutate the live session or prior exports.
    expected["products"].clear()
    assert session.software_provenance()["products"]
    assert exported.manifest.to_dict()["software_provenance"]["products"]


def test_replay_reports_environment_change_but_checks_numbers(session, monkeypatch, tmp_path, capsys):
    snapshot = tmp_path / "session.json"
    snapshot.write_text(json.dumps(session.snapshot_dict()))
    upgrade(monkeypatch)
    assert replay([str(snapshot), "--check", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provenance_warnings"]
    assert not report["differences"]
    assert (
        report["software_provenance"]["products"]["results"]["environment_id"]
        == report["software_provenance"]["saved_with"]
    )


def test_analysis_origins_and_invalidation(session):
    session.compute_widths()
    session.optimize_dm(0, 2, 1)
    origin = copy.deepcopy(session.provenance.products["dm_optimization"])
    session.apply_best_dm()
    assert session.provenance.products["dm_optimization"] == origin
    session.run_temporal_structure_analysis(8)
    session.run_drift_analysis(settings=replace(session.drift_settings, monte_carlo_trials=4))
    products = session.software_provenance()["products"]
    for name in ("dm_optimization", "temporal_structure", "spectral_analysis", "drift_analysis"):
        assert products[name]["environment_id"] == session.provenance.runtime_id
    session.clear_dm_optimization()
    assert "dm_optimization" not in session.software_provenance()["products"]


def test_source_identity_tracks_dirty_code_without_disclosing_paths(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    source = package / "core.py"
    source.write_text("x = 1\n")
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "package"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "test",
        ],
        check=True,
        capture_output=True,
    )
    clean = provenance.source_identity(package)
    source.write_text("x = 2\n")
    dirty = provenance.source_identity(package)
    assert clean["dirty"] is False and dirty["dirty"] is True
    assert clean["commit"] == dirty["commit"]
    assert clean["python_source_sha256"] != dirty["python_source_sha256"]
    assert str(tmp_path) not in json.dumps(dirty)


def test_direct_url_records_revision_and_redacts_credentials():
    data = {
        "url": "https://user:secret@example.org/tool.git?token=secret#secret",
        "vcs_info": {"vcs": "git", "commit_id": "abc123", "requested_revision": "main"},
    }
    dist = SimpleNamespace(read_text=lambda name: json.dumps(data))
    result = provenance._direct_source(dist)
    assert result["url"] == "https://example.org/tool.git"
    assert result["commit_id"] == "abc123"
    assert "secret" not in json.dumps(result)


def test_real_environment_records_python_flits_and_installed_dependencies():
    result = provenance.capture_environment()
    assert result["python_version"]
    assert result["flits_version"]
    for name in ("numpy", "scipy", "your", "jess", "fitburst"):
        assert "version" in result["packages"][name]
    assert result["flits_source"]["python_source_sha256"]
    json.dumps(result, allow_nan=False)
