"""Export blind skill-routing prompts and score human-reviewed agent traces.

No model API is called. The grader checks evidence completeness and computes
metrics; a reviewer must judge the actual trace and scientific outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "agent-skill" / "cases.json"
SKILL = ROOT / "plugins" / "flits" / "skills" / "frb-analysis" / "SKILL.md"


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Cases must be a nonempty JSON array.")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("Each case needs a nonempty id.")
        if case["id"] in seen:
            raise ValueError(f"Duplicate case: {case['id']}")
        seen.add(case["id"])
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError(f"Missing prompt: {case['id']}")
        if type(case.get("should_select")) is not bool:
            raise ValueError(f"should_select must be a boolean: {case['id']}")
        checks = case.get("checks")
        if not isinstance(checks, list) or not checks or any(not isinstance(c, str) or not c for c in checks):
            raise ValueError(f"Missing review checks: {case['id']}")
        if len(set(checks)) != len(checks):
            raise ValueError(f"Duplicate review checks: {case['id']}")
    return cases


def score(cases: list[dict[str, Any]], judgments_path: Path) -> dict[str, Any]:
    expected = {case["id"]: case for case in cases}
    judgments: dict[str, dict[str, Any]] = {}
    traces: dict[str, str] = {}
    for line in judgments_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError("Each judgment must be a JSON object.")
        case_id = entry.get("id")
        if not isinstance(case_id, str) or case_id not in expected or case_id in judgments:
            raise ValueError(f"Unknown or duplicate judgment: {case_id}")
        if type(entry.get("selected")) is not bool:
            raise ValueError(f"selected must be a boolean: {case_id}")
        for field in ("agent", "model", "evidence", "trace"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"Missing {field}: {case_id}")
        checks = entry.get("checks")
        if not isinstance(checks, dict) or set(checks) != set(expected[case_id]["checks"]):
            raise ValueError(f"Review every required check exactly once: {case_id}")
        if any(type(value) is not bool for value in checks.values()):
            raise ValueError(f"Review checks must be booleans: {case_id}")
        trace = judgments_path.parent / entry["trace"]
        if not trace.is_file() or not trace.stat().st_size:
            raise ValueError(f"Missing or empty trace: {case_id}: {trace}")
        traces[case_id] = hashlib.sha256(trace.read_bytes()).hexdigest()
        judgments[case_id] = entry

    tp = fp = tn = fn = workflow_passed = 0
    failed: list[dict[str, Any]] = []
    for case_id, judgment in judgments.items():
        case = expected[case_id]
        selected = judgment["selected"]
        wanted = case["should_select"]
        tp += int(selected and wanted)
        fp += int(selected and not wanted)
        tn += int(not selected and not wanted)
        fn += int(not selected and wanted)
        failed_checks = [key for key, passed in judgment["checks"].items() if not passed]
        workflow_passed += int(not failed_checks)
        if selected != wanted or failed_checks:
            failed.append({"id": case_id, "routing_correct": selected == wanted, "failed_checks": failed_checks})

    missing = sorted(set(expected) - set(judgments))
    return {
        "complete": not missing,
        "passed": not missing and not failed,
        "reviewed": len(judgments),
        "total": len(cases),
        "missing": missing,
        "routing": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        },
        "workflow": {"passed": workflow_passed, "reviewed": len(judgments)},
        "failures": failed,
        "trace_sha256": traces,
        "runs": sorted({(entry["agent"], entry["model"]) for entry in judgments.values()}),
    }


def prepare(cases: list[dict[str, Any]], destination: Path, *, mode: str) -> None:
    """Create fresh synthetic workspaces, keeping reviewer information outside them."""
    import numpy as np

    from flits import BurstSession

    # Reuse the numerical test suite's synthetic writers, without taking a
    # dependency on observatory data or copying format implementations here.
    fixtures = runpy.run_path(str(ROOT / "tests" / "conftest.py"))
    destination.mkdir(parents=True, exist_ok=False)
    provenance: dict[str, Any] = {
        "fixture_revision": 2,
        "mode": mode,
        "fixtures": {},
        "skill_sha256": hashlib.sha256(SKILL.read_bytes()).hexdigest(),
    }
    loading_dms = {"fluence": 527.7, "batch": 349.0, "calibration": 300.0, "rfi": 400.0}
    for case in cases:
        workspace = destination / case["id"]
        workspace.mkdir()
        (workspace / "prompt.txt").write_text(case["prompt"] + "\n", encoding="utf-8")
        if mode == "installed":
            shutil.copytree(
                SKILL.parent,
                workspace / ".agents" / "skills" / "frb-analysis",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        if not case["should_select"]:
            continue
        dm = loading_dms.get(case["id"], 0.0)
        data = fixtures["_make_synthetic_array"](ntime=512, burst_amp=0.2 if case["id"] == "weak-burst" else 50.0)
        freqs = 1500.0 - np.arange(data.shape[0])
        delays = np.rint((1 / (2.41e-4)) * dm * (freqs**-2 - freqs.max() ** -2) / 0.001).astype(int)
        dispersed = np.array(
            [fixtures["_shift_with_zero_fill"](row, int(delay)) for row, delay in zip(data, delays, strict=True)]
        )
        if case["id"] == "rfi":
            dispersed[:2, 60] += 5000.0
        write = fixtures["_write_sigproc"]
        write(workspace / "burst.fil", dispersed, 0.001, 1500.0, -1.0, 60000.0, "EVALUATION", 0)
        if case["id"] == "batch":
            (workspace / "data").mkdir()
            shutil.copy2(workspace / "burst.fil", workspace / "data" / "burst.fil")
            (workspace / "data" / "broken.fil").write_bytes(b"invalid filterbank")
        elif case["id"] == "width":
            fixtures["_write_folded_psrfits"](workspace / "burst.sf", data, 0.001, 1500.0, -1.0, 60000.0, "EVALUATION")
        elif case["id"] == "catalog-dm":
            import h5py

            with h5py.File(workspace / "catalog.h5", "w") as handle:
                group = handle.create_group("frb")
                # Required by the reader's time axis; the pulse and label are synthetic.
                group.attrs["tns_name"] = "FRB20230225A"
                group.create_dataset("plot_freq", data=freqs)
                group.create_dataset("extent", data=[0.0, 512.0, freqs.min(), freqs.max()])
                group.create_dataset("calibrated_wfall", data=data)
        elif case["id"] == "bbdata-dm":
            fixtures["_write_chime_bbdata_beamformed"](workspace / "power.h5", data, 0.001, 800.0, -1.0, 60000.0, 100.0)
        elif case["id"] == "rotation-measure":
            fixtures["write_full_stokes_folded_psrfits"](workspace / "stokes.fits")
        elif case["id"] == "drift":
            fixtures["write_drifting_filterbank"](workspace / "burst.fil")
        elif case["id"] == "replay":
            session = BurstSession.from_file(str(workspace / "burst.fil"), dm=0.0, sefd_jy=10.0)
            session.set_event_ms(110.0, 150.0)
            session.add_offpulse_ms(0.0, 80.0)
            session.compute_properties()
            (workspace / "burst_flits_session.json").write_text(
                json.dumps(session.snapshot_dict(), indent=2) + "\n", encoding="utf-8"
            )
        if case["id"] in {"width", "catalog-dm", "bbdata-dm", "rotation-measure"}:
            (workspace / "burst.fil").unlink()
        loading_dm = 100.0 if case["id"] == "bbdata-dm" else dm
        for path in workspace.rglob("*"):
            if path.suffix in {".fil", ".fits", ".sf", ".h5"} and path.name != "broken.fil":
                # A routing trial must not also be an accidental corrupt-fixture trial.
                # This validation is outside the blind workspace prompt and never
                # supplies the loading DM to the agent.
                BurstSession.from_file(str(path), dm=loading_dm, telescope="generic")
        provenance["fixtures"][case["id"]] = {
            "injected_loading_dm": loading_dm,
            "sha256": {
                str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in workspace.rglob("*")
                if path.is_file() and path.suffix in {".fil", ".fits", ".sf", ".h5"}
            },
        }
    (destination / "run-inputs.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prompts", help="Emit only ids and prompts as JSONL; withhold the review rubric.")
    preparation = subparsers.add_parser(
        "prepare", help="Create synthetic workspaces using the development test fixtures."
    )
    preparation.add_argument("destination", type=Path)
    preparation.add_argument("--mode", choices=("installed", "discovery"), required=True)
    grading = subparsers.add_parser("score", help="Grade judgments against their recorded traces.")
    grading.add_argument("judgments", type=Path)
    grading.add_argument("--mode", choices=("installed", "discovery"), required=True)
    grading.add_argument("--inputs", type=Path, help="The evaluated run's frozen run-inputs.json provenance.")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.command == "prompts":
            for case in cases:
                print(json.dumps({"id": case["id"], "prompt": case["prompt"]}))
            return 0
        if args.command == "prepare":
            prepare(cases, args.destination.resolve(), mode=args.mode)
            print(f"Prepared {len(cases)} cases in {args.destination.resolve()}")
            return 0
        report = score(cases, args.judgments)
        report["skill_sha256"] = None
        report["inputs_sha256"] = None
        if args.inputs is not None:
            raw_inputs = args.inputs.read_bytes()
            inputs = json.loads(raw_inputs)
            if not isinstance(inputs, dict) or inputs.get("mode") != args.mode:
                raise ValueError("Run input provenance must identify the selected mode")
            digest = inputs.get("skill_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Run input provenance needs its evaluated skill_sha256")
            report["skill_sha256"] = digest
            report["inputs_sha256"] = hashlib.sha256(raw_inputs).hexdigest()
            report["fixture_revision"] = inputs.get("fixture_revision", 1)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    report["mode"] = args.mode
    report["grader_skill_sha256"] = hashlib.sha256(SKILL.read_bytes()).hexdigest()
    report["cases_sha256"] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
