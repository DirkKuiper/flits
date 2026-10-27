"""The multi-format documentation example must run and recover its known pulse."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_psrfits_and_chime_examples_recover_the_injected_pulse(tmp_path):
    run_examples = runpy.run_path(str(ROOT / "examples" / "format_workflows.py"))["run_examples"]
    destination = tmp_path / "examples"
    summary = run_examples(destination)
    records = {record["id"]: record for record in summary["records"]}
    assert set(records) == {"folded", "catalog", "power"}
    assert {key: record["dm"] for key, record in records.items()} == {"folded": 0, "catalog": 0, "power": 50}
    for record in records.values():
        assert record["status"] == "ok"
        assert record["selection"]["mask_profile"] == "auto"
        assert record["selection"]["event_ms"][0] <= 128 < record["selection"]["event_ms"][1]
        values = record["measurements"]
        assert values["width_ms_acf"] == pytest.approx(2.35482 * 2.5, abs=0.4)
        assert values["uncertainty_details"]["fluence_jyms"]["classification"] == "statistical_only"
        assert Path(record["snapshot"]).is_file()
    assert records["power"]["measurements"]["width_ms_acf"] == pytest.approx(
        records["catalog"]["measurements"]["width_ms_acf"], abs=0.1
    )
    assert json.loads((destination / "results.json").read_text())["input_sha256"] == summary["input_sha256"]
    before = (destination / "results.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_examples(destination)
    assert (destination / "results.json").read_bytes() == before
