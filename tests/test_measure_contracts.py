"""Batch manifests must preserve each burst's settings and every outcome."""

from __future__ import annotations

import copy
import csv
import io
import json
from dataclasses import fields
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from flits.cli import main
from flits.manifest import load_measure_manifest
from flits.measure import MeasureOptions, error_record, measure_burst
from flits.schema import get_schema
from flits.session import BurstSession


def _write_manifest(tmp_path: Path, waterfall, *, overrides=None) -> Path:
    payload = {
        "schema_version": "1.0",
        "defaults": {"dm": 0, "sefd_jy": 10, "mask": "none", "event_ms": [100, 160], "offpulse_ms": [[0, 50]]},
        "bursts": [
            {"id": "first", "file": waterfall.path.name},
            {"id": "second", "file": waterfall.path.name, "options": {"sefd_jy": 20}},
        ],
    }
    if overrides:
        payload.update(overrides)
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_calibration_overrides_and_snapshots(synthetic_waterfall, tmp_path, monkeypatch, capsys):
    manifest = _write_manifest(tmp_path, synthetic_waterfall)
    snapshots = tmp_path / "snapshots"
    # Relative inputs belong to the manifest's directory, not the invoking shell.
    monkeypatch.chdir(tmp_path.parent)
    assert main(["measure", "--manifest", str(manifest), "--snapshot-dir", str(snapshots)]) == 0
    first, second = json.loads(capsys.readouterr().out)
    assert [first["id"], second["id"]] == ["first", "second"]
    assert first["file"] == second["file"] == str(synthetic_waterfall.path)
    for quantity in ("peak_flux_jy", "fluence_jyms"):
        assert second["measurements"][quantity] == pytest.approx(2 * first["measurements"][quantity], rel=1e-12)
    assert first["snapshot"] != second["snapshot"]
    for record, sefd in ((first, 10), (second, 20)):
        session = BurstSession.from_snapshot(json.loads(Path(record["snapshot"]).read_text()))
        assert session.config.sefd_jy == sefd
        assert session.compute_properties().fluence_jyms == pytest.approx(record["measurements"]["fluence_jyms"])


def test_manifest_honors_each_loading_dm(synthetic_waterfall, tmp_path, capsys):
    path = _write_manifest(
        tmp_path,
        synthetic_waterfall,
        overrides={
            "bursts": [
                {"file": synthetic_waterfall.path.name, "options": {"dm": 20}},
            ]
        },
    )
    assert main(["measure", "--manifest", str(path)]) == 0
    record = json.loads(capsys.readouterr().out)[0]
    expected = measure_burst(
        synthetic_waterfall.path,
        MeasureOptions(
            dm=20,
            sefd_jy=10,
            mask="none",
            event_ms=(100, 160),
            offpulse_ms=((0, 50),),
        ),
    )
    assert record["dm"] == 20
    for quantity in ("snr_integrated", "fluence_jyms", "toa_topo_mjd"):
        assert record["measurements"][quantity] == pytest.approx(expected["measurements"][quantity], rel=1e-12)


def test_utf8_manifest_with_bom_loads(synthetic_waterfall, tmp_path):
    path = _write_manifest(tmp_path, synthetic_waterfall)
    path.write_text(path.read_text(), encoding="utf-8-sig")
    assert len(load_measure_manifest(path)) == 2


@pytest.mark.parametrize("mode", ["files", "manifest", "manifest_input", "hardlink"])
def test_output_cannot_replace_an_input(synthetic_waterfall, tmp_path, mode):
    source = synthetic_waterfall.path
    original = source.read_bytes()
    manifest = _write_manifest(tmp_path, synthetic_waterfall)
    manifest_bytes = manifest.read_bytes()
    if mode == "files":
        arguments = ["measure", str(source), "--dm", "0"]
    else:
        arguments = ["measure", "--manifest", str(manifest)]
    output = manifest if mode == "manifest_input" else source
    if mode == "hardlink":
        output = tmp_path / "alias.fil"
        output.hardlink_to(source)
    with pytest.raises(SystemExit) as exc:
        main([*arguments, "--output", str(output)])
    assert exc.value.code == 2
    assert source.read_bytes() == original
    assert manifest.read_bytes() == manifest_bytes


@pytest.mark.parametrize("output_format", ["json", "jsonl", "csv"])
def test_manifest_failure_retains_id_and_does_not_stop_batch(synthetic_waterfall, tmp_path, capsys, output_format):
    path = _write_manifest(
        tmp_path,
        synthetic_waterfall,
        overrides={
            "bursts": [
                {"id": "missing", "file": "absent.fil"},
                {"id": "present", "file": synthetic_waterfall.path.name},
            ]
        },
    )
    assert main(["measure", "--manifest", str(path), "--format", output_format]) == 1
    raw = capsys.readouterr().out
    if output_format == "json":
        rows = json.loads(raw)
    elif output_format == "jsonl":
        rows = [json.loads(line) for line in raw.splitlines()]
    else:
        rows = list(csv.DictReader(io.StringIO(raw)))
    assert [row["id"] for row in rows] == ["missing", "present"]
    assert [row["status"] for row in rows] == ["error", "ok"]
    assert all(row["schema_version"] == "1.0" for row in rows)


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"dm": "0"},
        {"dm": True},
        {"dm": None},
        {"dm": 0, "time_factor": True},
        {"dm": 0, "freq_factor": 0},
        {"dm": 0, "sefd_jy": -1},
        {"dm": 0, "event_ms": [50, 10]},
        {"dm": 0, "offpulse_ms": [[0, 5, 10]]},
        {"dm": 0, "mask": "unknown"},
        {"dm": 0, "telescope": "typo"},
        {"dm": 0, "sefd_fractional_uncertainty": -0.1},
        {"dm": 0, "detection_snr": 0},
        {"dm": 0, "widths": "false"},
        {"dm": 0, "sefd": 10},
    ],
)
def test_invalid_manifest_settings_fail_before_analysis_or_output(synthetic_waterfall, tmp_path, monkeypatch, settings):
    import flits.manifest as module

    path = _write_manifest(tmp_path, synthetic_waterfall, overrides={"defaults": settings})
    output = tmp_path / "results.json"
    output.write_text("keep existing output", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("An invalid manifest must be rejected before measuring any input")

    monkeypatch.setattr(module, "measure_burst", forbidden)
    with pytest.raises(SystemExit) as exc:
        main(["measure", "--manifest", str(path), "--output", str(output)])
    assert exc.value.code == 2
    assert output.read_text() == "keep existing output"


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":"1.0","schema_version":"1.0","bursts":[]}',
        '{"schema_version":"1.0","defaults":{"dm":NaN},"bursts":[]}',
        '{"schema_version":"2.0","bursts":[{"file":"a.fil"}]}',
        '{"schema_version":"1.0","bursts":[]}',
        '{"schema_version":"1.0","defaults":{"dm":0},"bursts":[{"id":"a","file":"a.fil"},{"id":"a","file":"b.fil"}]}',
        '{"schema_version":"1.0","bursts":[{"file":"","options":{"dm":0}}]}',
        '{"schema_version":"1.0","bursts":[{"file":"a.fil","dmm":0}]}',
    ],
)
def test_ambiguous_or_unsupported_manifests_are_rejected(tmp_path, raw):
    path = tmp_path / "batch.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        load_measure_manifest(path)


@pytest.mark.parametrize("flags", [["--dm", "0"], ["burst.fil"], ["--mask", "auto"], ["--widths"]])
def test_manifest_and_shared_per_burst_flags_cannot_be_mixed(tmp_path, flags):
    with pytest.raises(SystemExit) as exc:
        main(["measure", "--manifest", str(tmp_path / "batch.json"), *flags])
    assert exc.value.code == 2


def test_packaged_schemas_are_valid_and_available_offline(capsys):
    for name in ("measurement", "batch-manifest"):
        assert main(["schema", name]) == 0
        schema = json.loads(capsys.readouterr().out)
        Draft202012Validator.check_schema(schema)
    one = get_schema("measurement")
    one["title"] = "changed"
    assert get_schema("measurement")["title"] != "changed"
    assert set(get_schema("batch-manifest")["$defs"]["options"]["properties"]) == {
        field.name for field in fields(MeasureOptions)
    }


def test_real_measurement_outcomes_match_the_schema(synthetic_waterfall, tmp_path):
    validator = Draft202012Validator(get_schema("measurement"))
    records = [
        measure_burst(
            synthetic_waterfall.path, MeasureOptions(dm=0, mask="none", widths=True, include_diagnostics=True)
        ),
        measure_burst(synthetic_waterfall.path, MeasureOptions(dm=0, mask="none", crop_ms=(0, 60))),
        error_record(tmp_path / "missing.fil", FileNotFoundError("missing")),
    ]
    assert [record["status"] for record in records] == ["ok", "no_detection", "error"]
    for record in records:
        validator.validate(json.loads(json.dumps(record, allow_nan=False)))
    low_sn = copy.deepcopy(records[0])
    low_sn["status"] = "low_sn"
    validator.validate(low_sn)


@pytest.mark.parametrize(
    "mutation", ["missing_flags", "numeric_string", "unclassified", "false_detection", "future_version"]
)
def test_schema_rejects_malformed_or_misclassified_results(synthetic_waterfall, mutation):
    record = measure_burst(synthetic_waterfall.path, MeasureOptions(dm=0, mask="none", sefd_jy=10))
    if mutation == "missing_flags":
        del record["localization"]["warning_flags"]
    elif mutation == "numeric_string":
        record["measurements"]["fluence_jyms"] = "12"
    elif mutation == "unclassified":
        del record["measurements"]["uncertainty_details"]["fluence_jyms"]["classification"]
    elif mutation == "false_detection":
        record["status"] = "no_detection"
    else:
        record["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        Draft202012Validator(get_schema("measurement")).validate(record)
