"""Reproduce the public GBT tutorial with explicit settings and checked outputs.

Download the public filterbank as described in docs/guided-workflow.md, then run
this script with --source. Normal runs verify the checked-in reference; only
--write-reference replaces it when deliberately updating the tutorial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from pathlib import Path

import flits
from flits.session import BurstSession

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "docs" / "examples"
SOURCE_NAME = "flits-tutorial-gbt-frb20240114a-v1.fil"
SOURCE_URL = f"https://github.com/DirkKuiper/flits/releases/download/tutorial-data-v1/{SOURCE_NAME}"
SOURCE_SHA256 = "e80c69842cdd5f41b5a1fc617b7b2cebd864c121e27830bbb5d2161af539a2cc"
SNAPSHOT_NAME = "gbt-reference-session.json"
SUMMARY_NAME = "gbt-reference-summary.json"
NUMERICAL_FIELDS = (
    "snr_peak",
    "snr_integrated",
    "fluence_jyms",
    "peak_flux_jy",
    "width_ms_acf",
    "event_duration_ms",
    "spectral_extent_mhz",
)


def prepare_session(source: Path) -> BurstSession:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Source checksum mismatch: expected {SOURCE_SHA256}, received {digest}")
    session = BurstSession.from_file(str(source.resolve()), dm=0.0, telescope="gbt", sefd_jy=10.0)
    session.set_time_factor(32)
    session.set_freq_factor(1)
    session.set_event_ms(235.0, 250.0)
    session.clear_offpulse()
    session.add_offpulse_ms(221.0, 233.0)
    session.add_offpulse_ms(260.0, 280.0)
    session.set_spectral_extent_freq(1300.0, 1750.0)
    # Intentionally no automatic masking or invented calibration systematic.
    session.notes = (
        "Public GBT tutorial, FLITS 1.2.0. Residual DM 0 on an already dedispersed cutout. "
        "Event 235-250 ms; off-pulse 221-233 and 260-280 ms; band 1300-1750 MHz; "
        "time factor 32, frequency factor 1; no masked channels; preset SEFD 10 Jy. "
        "No SEFD uncertainty supplied: flux uncertainties are statistical-only. "
        "Upstream absolute dedispersion time reference is not established by this example; "
        "absolute/infinite-frequency arrival times are not validated scientific results."
    )
    session.compute_properties()
    session.optimize_dm(center_dm=0.0, half_range=10.0, step=0.5, metric="dm_phase")
    return session


def summarize(session: BurstSession) -> dict:
    result = session.results.to_dict()
    dm = session.dm_optimization
    return {
        "flits_version": flits.__version__,
        "python_version": platform.python_version(),
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "relative_tolerance": 1e-6,
        "absolute_tolerance": 1e-8,
        "measurements": {key: result[key] for key in NUMERICAL_FIELDS},
        "peak_time_ms": result["peak_positions_ms"][0],
        "mask_count": result["mask_count"],
        "fluence_uncertainty_classification": result["uncertainty_details"]["fluence_jyms"]["classification"],
        "fluence_publishable": result["uncertainty_details"]["fluence_jyms"]["publishable"],
        "dm_sweep": {
            "best_residual_dm": dm.best_dm,
            "sampled_best_residual_dm": dm.sampled_best_dm,
            "fit_status": dm.fit_status,
        },
        "scope": (
            "Numerical regression example, not an observation-specific calibration or absolute-timing reference. "
            "The CLI replay recomputes core measurements; this script additionally reruns the residual DM sweep."
        ),
    }


def verify(actual: dict, expected: dict) -> None:
    if actual["source_sha256"] != expected["source_sha256"]:
        raise ValueError("Reference and source checksums differ")
    pairs = [(key, actual["measurements"][key], value) for key, value in expected["measurements"].items()]
    pairs.append(("peak_time_ms", actual["peak_time_ms"], expected["peak_time_ms"]))
    for key in ("best_residual_dm", "sampled_best_residual_dm"):
        pairs.append((key, actual["dm_sweep"][key], expected["dm_sweep"][key]))
    for key, value, target in pairs:
        if not math.isclose(
            value, target, rel_tol=expected["relative_tolerance"], abs_tol=expected["absolute_tolerance"]
        ):
            raise ValueError(f"{key}: expected {target}, received {value}")
    for key in ("mask_count", "fluence_uncertainty_classification", "fluence_publishable"):
        if actual[key] != expected[key]:
            raise ValueError(f"{key}: expected {expected[key]!r}, received {actual[key]!r}")
    if actual["dm_sweep"]["fit_status"] != expected["dm_sweep"]["fit_status"]:
        raise ValueError("DM fit status differs from the reference")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Downloaded, checksum-verified GBT filterbank")
    parser.add_argument("--reference-dir", type=Path, default=REFERENCE_DIR)
    parser.add_argument("--write-reference", action="store_true", help="Regenerate reference files deliberately")
    args = parser.parse_args()
    session = prepare_session(args.source)
    summary = summarize(session)
    summary_path = args.reference_dir / SUMMARY_NAME
    if args.write_reference:
        args.reference_dir.mkdir(parents=True, exist_ok=True)
        snapshot = session.snapshot_dict()
        # Publish portable names, never a developer's absolute local path.
        snapshot["source"].update(
            source_path=SOURCE_NAME,
            file_name=SOURCE_NAME,
            data_dir_relative_path=SOURCE_NAME,
            mtime_unix=0.0,
        )
        snapshot["results"]["burst_name"] = Path(SOURCE_NAME).stem
        (args.reference_dir / SNAPSHOT_NAME).write_text(
            json.dumps(snapshot, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Wrote {summary_path} and {SNAPSHOT_NAME}")
    else:
        verify(summary, json.loads(summary_path.read_text(encoding="utf-8")))
        print("PASS: GBT measurements, uncertainty labels, and residual DM sweep match the reference")
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
