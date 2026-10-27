"""Create and measure synthetic PSRFITS and CHIME examples, without downloads.

Run from a FLITS source checkout with the development environment installed.
The example deliberately reuses the numerical test suite's fixture writers.
These are invented signals for learning format/DM conventions, not telescope
calibration references or new astronomical observations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
from dataclasses import asdict
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from flits import MeasureOptions, measure_burst
from flits.cli import main as flits_cli
from flits.io import inspect_filterbank

ROOT = Path(__file__).resolve().parents[1]


def run_examples(destination: Path) -> dict[str, Any]:
    """Write a new example directory, inspect the inputs, measure and replay."""
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    fixtures = runpy.run_path(str(ROOT / "tests" / "conftest.py"))
    data = fixtures["_make_synthetic_array"]()
    common = {
        "data": data,
        "tsamp_s": 0.001,
        "fch1_mhz": 800.0,
        "foff_mhz": -400.0 / (data.shape[0] - 1),
        "tstart_mjd": 60000.0,
    }
    folded = destination / "folded.sf"
    fixtures["_write_folded_psrfits"](folded, **common, source_name="SYNTHETIC")
    power = destination / "power.h5"
    fixtures["_write_chime_bbdata_beamformed"](power, **common, coherent_dm=50.0)
    catalog = destination / "catalog.h5"
    with h5py.File(catalog, "w") as handle:
        burst = handle.create_group("frb")
        # The catalog reader requires a date-shaped TNS field for its time axis.
        # This fixture label is synthetic; it identifies no observation.
        burst.attrs["tns_name"] = "FRB20230225A"
        burst.create_dataset("extent", data=[0.0, 256.0, 400.0, 800.0])
        burst.create_dataset("plot_freq", data=np.linspace(800.0, 400.0, data.shape[0]))
        burst.create_dataset("calibrated_wfall", data=data)

    records = []
    inspections = []
    for path in (folded, catalog, power):
        inspection = inspect_filterbank(path)
        inspections.append({**asdict(inspection), "source_path": str(path)})
        if inspection.schema_version == "chime_frb_catalog_v1":
            loading_dm = 0.0
        elif inspection.schema_version == "chime_bbdata_beamformed_v1":
            assert inspection.coherent_dm is not None
            loading_dm = inspection.coherent_dm
        else:
            loading_dm = 0.0  # The generated folded profile was not dispersed.
        record = measure_burst(
            path,
            MeasureOptions(
                dm=loading_dm,
                telescope="generic",
                sefd_jy=10.0,
                time_factor=1,
                freq_factor=1,
            ),
            snapshot_dir=destination / "snapshots",
        )
        assert record["status"] == "ok", record
        assert record["localization"]["warning_flags"] == [], record["localization"]
        assert record["measurements"]["snr_integrated"] > 6
        record["id"] = path.stem
        records.append(record)
        assert flits_cli(["replay", record["snapshot"], "--data-dir", str(destination), "--check"]) == 0

    summary = {
        "data_kind": "synthetic Gaussian pulse, sigma 2.5 ms, peak 128 ms, fixed random seed 42",
        "calibration": "Assumed SEFD 10 Jy for the example; not an instrument calibration. No SEFD uncertainty supplied.",
        "input_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (folded, catalog, power)},
        "inspections": inspections,
        "records": records,
    }
    (destination / "results.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; refuses to replace existing examples."
    )
    args = parser.parse_args()
    summary = run_examples(args.output)
    for record in summary["records"]:
        values = record["measurements"]
        print(
            f"{record['id']}: loading DM {record['dm']}, fluence {values['fluence_jyms']:.3f} Jy ms, ACF FWHM {values['width_ms_acf']:.3f} ms"
        )


if __name__ == "__main__":
    main()
