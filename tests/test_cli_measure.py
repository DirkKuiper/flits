"""Tests for headless measurement: `flits measure` and `flits.measure`.

The command is the scriptable front door to FLITS, for batch jobs and for tools
that drive it programmatically, so its output shape and exit codes are part of
its contract.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from flits.cli import main, replay
from flits.measure import CSV_COLUMNS, MeasureOptions, measure_burst
from flits.session import BurstSession


def _strict_json(text: str):
    """Parse JSON the way strict consumers do: NaN and Infinity are errors."""

    def reject(constant: str):
        raise ValueError(f"non-standard JSON constant {constant}")

    return json.loads(text, parse_constant=reject)


def test_measure_localizes_and_measures_automatically(synthetic_waterfall, capsys) -> None:
    assert main(["measure", str(synthetic_waterfall.path), "--dm", "0", "--sefd", "10", "--mask", "none"]) == 0

    records = _strict_json(capsys.readouterr().out)
    assert isinstance(records, list) and len(records) == 1
    record = records[0]
    assert record["status"] == "ok"
    assert record["error"] is None
    assert record["selection"]["event_source"] == "auto_localized"
    assert record["localization"]["status"] == "ok"

    # The synthetic burst sits at 128 ms; the event window must contain it.
    burst_ms = synthetic_waterfall.burst_time_idx * synthetic_waterfall.tsamp_s * 1e3
    start_ms, end_ms = record["selection"]["event_ms"]
    assert start_ms <= burst_ms <= end_ms
    assert record["measurements"]["snr_integrated"] > 6
    # Diagnostic arrays are left out unless asked for.
    assert "diagnostics" not in record["measurements"]


def test_pinned_selections_match_the_session_api(synthetic_waterfall, capsys) -> None:
    argv = [
        "measure",
        str(synthetic_waterfall.path),
        "--dm",
        "0",
        "--sefd",
        "10",
        "--mask",
        "none",
        "--event",
        "100",
        "160",
        "--offpulse",
        "0",
        "50",
    ]
    assert main(argv) == 0
    record = _strict_json(capsys.readouterr().out)[0]

    session = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0)
    session.set_event_ms(100.0, 160.0)
    session.add_offpulse_ms(0.0, 50.0)
    expected = session.compute_properties().to_dict()

    assert record["selection"]["event_source"] == "manual"
    assert record["localization"] is None
    assert record["selection"]["event_ms"] == [100.0, 160.0]
    assert record["selection"]["offpulse_ms"] == [[0.0, 50.0]]
    for field in ("snr_peak", "snr_integrated", "fluence_jyms", "peak_flux_jy", "event_duration_ms"):
        assert record["measurements"][field] == pytest.approx(expected[field], rel=1e-12), field


def test_a_band_overrides_the_localized_band(synthetic_waterfall) -> None:
    options = MeasureOptions(dm=0.0, sefd_jy=10.0, mask="none", band_mhz=(1450.0, 1480.0))
    record = measure_burst(synthetic_waterfall.path, options)

    assert record["selection"]["event_source"] == "auto_localized"
    assert record["selection"]["band_mhz"] == [1450.0, 1480.0]


def test_automatic_masking_is_recorded(synthetic_waterfall) -> None:
    record = measure_burst(synthetic_waterfall.path, MeasureOptions(dm=0.0, sefd_jy=10.0))

    assert record["selection"]["mask_profile"] == "auto"
    assert isinstance(record["selection"]["masked_channels"], int)


def test_nothing_to_find_is_reported_not_measured(synthetic_waterfall, capsys) -> None:
    # A crop well before the burst holds only noise.
    argv = ["measure", str(synthetic_waterfall.path), "--dm", "0", "--mask", "none", "--crop", "0", "60"]
    assert main(argv) == 1

    record = _strict_json(capsys.readouterr().out)[0]
    assert record["status"] == "no_detection"
    assert record["measurements"] is None
    assert record["localization"]["status"] == "no_detection"


def test_a_failed_burst_does_not_stop_the_batch(synthetic_waterfall, tmp_path: Path, capsys, caplog) -> None:
    missing = tmp_path / "missing.fil"
    argv = ["measure", str(missing), str(synthetic_waterfall.path), "--dm", "0", "--mask", "none"]
    assert main(argv) == 1

    first, second = _strict_json(capsys.readouterr().out)
    assert first["status"] == "error"
    assert "missing.fil" in first["error"]
    assert second["status"] == "ok"
    # The failure is reported as it happens, not only in the results.
    assert any(record.levelname == "ERROR" and "missing.fil" in record.getMessage() for record in caplog.records)


def test_jsonl_writes_one_record_per_line(synthetic_waterfall, capsys) -> None:
    path = str(synthetic_waterfall.path)
    assert main(["measure", path, path, "--dm", "0", "--mask", "none", "--format", "jsonl"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all(_strict_json(line)["status"] == "ok" for line in lines)


def test_csv_writes_a_table(synthetic_waterfall, tmp_path: Path, capsys) -> None:
    output = tmp_path / "results" / "table.csv"
    argv = ["measure", str(synthetic_waterfall.path), "--dm", "0", "--sefd", "10", "--mask", "none"]
    assert main([*argv, "--format", "csv", "--output", str(output)]) == 0

    assert capsys.readouterr().out == ""
    rows = list(csv.DictReader(io.StringIO(output.read_text(encoding="utf-8"))))
    assert len(rows) == 1
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[0]["status"] == "ok"
    assert float(rows[0]["fluence_jyms"]) > 0
    assert float(rows[0]["fluence_jyms_err"]) > 0
    assert rows[0]["fluence_uncertainty_class"]


def test_diagnostics_are_included_on_request(synthetic_waterfall) -> None:
    options = MeasureOptions(dm=0.0, mask="none", include_diagnostics=True)
    record = measure_burst(synthetic_waterfall.path, options)

    assert "time_profile_sn" in record["measurements"]["diagnostics"]


def test_widths_are_measured_on_request(synthetic_waterfall) -> None:
    record = measure_burst(synthetic_waterfall.path, MeasureOptions(dm=0.0, mask="none", widths=True))

    assert record["measurements"]["width_results"]


def test_saved_snapshots_replay_to_the_same_numbers(synthetic_waterfall, tmp_path: Path, capsys) -> None:
    snapshots = tmp_path / "snapshots"
    argv = ["measure", str(synthetic_waterfall.path), "--dm", "0", "--sefd", "10", "--mask", "none"]
    assert main([*argv, "--snapshot-dir", str(snapshots)]) == 0

    record = _strict_json(capsys.readouterr().out)[0]
    snapshot = Path(record["snapshot"])
    assert snapshot == snapshots / f"{synthetic_waterfall.path.stem}_flits_session.json"

    assert replay([str(snapshot), "--check"]) == 0
    assert "check      OK" in capsys.readouterr().out


def test_snapshots_are_not_replaced_without_overwrite(synthetic_waterfall, tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    existing = snapshots / f"{synthetic_waterfall.path.stem}_flits_session.json"
    existing.parent.mkdir()
    existing.write_text('{"edited": true}\n', encoding="utf-8")
    options = MeasureOptions(dm=0.0, mask="none")

    copy = Path(measure_burst(synthetic_waterfall.path, options, snapshot_dir=snapshots)["snapshot"])
    assert copy != existing
    assert copy.name.endswith("_flits_session.json")
    assert json.loads(existing.read_text(encoding="utf-8")) == {"edited": True}

    replaced = Path(
        measure_burst(synthetic_waterfall.path, options, snapshot_dir=snapshots, overwrite=True)["snapshot"]
    )
    assert replaced == existing
    assert "results" in json.loads(existing.read_text(encoding="utf-8"))


def test_the_same_file_twice_gets_two_snapshots(synthetic_waterfall, tmp_path: Path, capsys) -> None:
    path = str(synthetic_waterfall.path)
    snapshots = tmp_path / "snapshots"
    argv = ["measure", path, path, "--dm", "0", "--mask", "none", "--snapshot-dir", str(snapshots), "--overwrite"]
    assert main(argv) == 0

    first, second = _strict_json(capsys.readouterr().out)
    assert first["snapshot"] != second["snapshot"]
    assert len(list(snapshots.glob("*_flits_session.json"))) == 2


def test_dm_is_required(synthetic_waterfall, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["measure", str(synthetic_waterfall.path)])
    assert excinfo.value.code == 2
    assert "--dm" in capsys.readouterr().err


def test_help_lists_the_measure_command(capsys) -> None:
    assert main(["--help"]) == 0
    assert "measure" in capsys.readouterr().out
