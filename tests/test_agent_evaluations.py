"""The evaluation must distinguish selection, scientific use and missing evidence."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = runpy.run_path(str(ROOT / "tools" / "evaluate_agent_skill.py"))


def _judgment(tmp_path, *, selected=True, checks=None):
    (tmp_path / "trace.txt").write_text("Recorded tool use and outputs for reviewer inspection.\n", encoding="utf-8")
    entry = {
        "id": "measurement",
        "selected": selected,
        "checks": {"uncertainty": True} if checks is None else checks,
        "evidence": "trace.txt:1 and the saved result artifact",
        "trace": "trace.txt",
        "agent": "test-harness",
        "model": "test-model",
    }
    path = tmp_path / "judgments.jsonl"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return path, entry


def test_routing_suite_covers_positive_negative_and_explicit_choice() -> None:
    cases = EVALUATOR["load_cases"](EVALUATOR["DEFAULT_CASES"])
    assert len(cases) >= 20
    assert {case["should_select"] for case in cases} == {True, False}
    assert not next(case for case in cases if case["id"] == "explicit-alternative")["should_select"]


def test_prompt_export_withholds_expected_answers(capsys) -> None:
    assert EVALUATOR["main"](["prompts"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows and all(set(row) == {"id", "prompt"} for row in rows)


def test_selected_tool_can_still_fail_scientific_use(tmp_path) -> None:
    path, _ = _judgment(tmp_path, checks={"uncertainty": False})
    cases = [{"id": "measurement", "should_select": True, "checks": ["uncertainty"]}]
    result = EVALUATOR["score"](cases, path)
    assert result["complete"] and not result["passed"]
    assert result["routing"]["recall"] == 1
    assert result["workflow"]["passed"] == 0
    assert result["failures"][0]["failed_checks"] == ["uncertainty"]


def test_missing_cases_are_not_counted_as_passes(tmp_path) -> None:
    path, _ = _judgment(tmp_path)
    cases = [
        {"id": "measurement", "should_select": True, "checks": ["uncertainty"]},
        {"id": "search", "should_select": False, "checks": ["scope"]},
    ]
    result = EVALUATOR["score"](cases, path)
    assert not result["complete"] and not result["passed"]
    assert result["missing"] == ["search"]
    assert result["reviewed"] == 1 and result["total"] == 2


def test_false_positive_and_false_negative_are_separate(tmp_path) -> None:
    path, entry = _judgment(tmp_path, selected=False)
    other = {**entry, "id": "search", "selected": True, "checks": {"scope": True}}
    path.write_text(json.dumps(entry) + "\n" + json.dumps(other) + "\n", encoding="utf-8")
    cases = [
        {"id": "measurement", "should_select": True, "checks": ["uncertainty"]},
        {"id": "search", "should_select": False, "checks": ["scope"]},
    ]
    result = EVALUATOR["score"](cases, path)
    assert result["routing"]["false_positive"] == result["routing"]["false_negative"] == 1
    assert result["routing"]["precision"] == result["routing"]["recall"] == 0


def test_scoring_keeps_the_evaluated_skill_separate_from_later_edits(tmp_path, capsys) -> None:
    path, _ = _judgment(tmp_path)
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [{"id": "measurement", "prompt": "Measure the burst.", "should_select": True, "checks": ["uncertainty"]}]
        )
    )
    inputs = tmp_path / "run-inputs.json"
    inputs.write_text(json.dumps({"mode": "installed", "skill_sha256": "a" * 64, "fixture_revision": 1}))
    arguments = ["--cases", str(cases), "score", str(path), "--mode", "installed"]
    assert EVALUATOR["main"]([*arguments, "--inputs", str(inputs)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skill_sha256"] == "a" * 64
    assert report["grader_skill_sha256"] != report["skill_sha256"]
    assert report["fixture_revision"] == 1
    assert EVALUATOR["main"](arguments) == 0
    assert json.loads(capsys.readouterr().out)["skill_sha256"] is None
    with pytest.raises(SystemExit) as exc:
        EVALUATOR["main"]([*arguments[:-1], "discovery", "--inputs", str(inputs)])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "field,value", [("trace", "missing.txt"), ("evidence", ""), ("selected", "true"), ("checks", {})]
)
def test_grading_requires_real_traces_and_complete_boolean_reviews(tmp_path, field, value) -> None:
    path, entry = _judgment(tmp_path)
    entry[field] = value
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        EVALUATOR["score"]([{"id": "measurement", "should_select": True, "checks": ["uncertainty"]}], path)


@pytest.mark.parametrize("mode", ["installed", "discovery"])
def test_prepared_workspaces_preserve_known_signals_and_dm_metadata(tmp_path, mode) -> None:
    from flits import MeasureOptions, measure_burst
    from flits.io import inspect_filterbank

    cases = [
        case
        for case in EVALUATOR["load_cases"](EVALUATOR["DEFAULT_CASES"])
        if case["id"]
        in {"fluence", "width", "catalog-dm", "bbdata-dm", "rotation-measure", "drift", "replay", "new-burst-search"}
    ]
    target = tmp_path / "evaluation"
    EVALUATOR["prepare"](cases, target, mode=mode)
    assert (target / "fluence" / ".agents" / "skills" / "frb-analysis" / "SKILL.md").exists() == (mode == "installed")
    record = measure_burst(target / "fluence" / "burst.fil", MeasureOptions(dm=527.7, mask="none", sefd_jy=10.0))
    assert record["status"] == "ok"
    event = record["selection"]["event_ms"]
    assert event[0] <= 128.0 <= event[1]
    assert record["measurements"]["snr_integrated"] > 50
    assert inspect_filterbank(target / "catalog-dm" / "catalog.h5").schema_version == "chime_frb_catalog_v1"
    catalog = measure_burst(
        target / "catalog-dm" / "catalog.h5",
        MeasureOptions(
            dm=0,
            telescope="generic",
            time_factor=1,
            freq_factor=1,
            mask="auto",
            sefd_jy=10,
        ),
    )
    assert catalog["status"] == "ok"
    assert catalog["selection"]["event_ms"][0] <= 128 <= catalog["selection"]["event_ms"][1]
    assert inspect_filterbank(target / "bbdata-dm" / "power.h5").coherent_dm == 100.0
    assert inspect_filterbank(target / "rotation-measure" / "stokes.fits").stokes_available
    assert (target / "replay" / "burst_flits_session.json").is_file()
    inputs = json.loads((target / "run-inputs.json").read_text(encoding="utf-8"))
    assert inputs["fixtures"]["fluence"]["injected_loading_dm"] == 527.7
    assert inputs["fixture_revision"] == 2
    assert inputs["fixtures"]["bbdata-dm"]["injected_loading_dm"] == 100.0
    assert not (target / "fluence" / "cases.json").exists()
    with pytest.raises(FileExistsError):
        EVALUATOR["prepare"](cases, target, mode=mode)
