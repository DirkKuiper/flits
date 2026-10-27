"""Exercise an installed wheel outside its source checkout, using synthetic inputs.

Run with the target environment's Python and a working directory outside the
checkout. pytest is test tooling for the shared fixture writers, not a FLITS
runtime dependency. No observatory files or external data downloads are used.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import runpy
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def _cli(*arguments: str, expected_code: int = 0) -> Any:
    result = subprocess.run(
        [sys.executable, "-m", "flits", *arguments], capture_output=True, text=True, timeout=90, check=False
    )
    assert result.returncode == expected_code, result.stderr
    return json.loads(result.stdout)


def _server(data_dir: Path, log_path: Path) -> dict[str, Any]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "flits", "serve", "--data-dir", str(data_dir), "--port", str(port)],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 60
            while True:
                assert process.poll() is None, log_path.read_text(errors="replace")
                try:
                    with urlopen(base + "/api/health", timeout=2) as response:
                        health = json.load(response)
                    break
                except (URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Server startup exceeded 60 s; see {log_path}") from None
                    time.sleep(0.1)
            assert health["status"] == "ok", health
            for endpoint, marker in (("/", b"FLITS"), ("/static/app.js", b"DOMContentLoaded")):
                with urlopen(base + endpoint, timeout=5) as response:
                    assert response.status == 200
                    assert marker in response.read(), f"Missing interface asset: {endpoint}"
            return {"health": health, "index": "ok", "javascript": "ok"}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _exercise(source: Path, workspace: Path, report: dict[str, Any], log_path: Path) -> None:
    import numpy as np

    import flits
    from flits import BurstSession, MeasureOptions, measure_burst
    from flits.io import inspect_filterbank

    package = Path(flits.__file__).resolve()
    assert not package.is_relative_to(source), f"Imported the source checkout instead of the wheel: {package}"
    assert not Path.cwd().resolve().is_relative_to(source), "Run this check outside the source checkout"
    report.update(flits_version=flits.__version__, package_path=str(package))
    print(f"Imported installed FLITS {flits.__version__} on {platform.system()} {platform.machine()}", flush=True)

    fixtures = runpy.run_path(str(source / "tests" / "conftest.py"))
    data = fixtures["_make_synthetic_array"](nchan=16)
    common = {
        "data": data,
        "tsamp_s": 0.001,
        "fch1_mhz": 1500.0,
        "foff_mhz": -1.0,
        "tstart_mjd": 60000.0,
        "source_name": "WHEEL_SMOKE",
    }
    paths = [workspace / "burst.fil", workspace / "burst.sf", workspace / "burst.h5"]
    fixtures["_write_sigproc"](paths[0], **common, telescope_id=0)
    fixtures["_write_folded_psrfits"](paths[1], **common)
    fixtures["_write_chime_hdf5"](paths[2], **common, telescope_id=0)

    measurements = []
    options = MeasureOptions(
        dm=0,
        telescope="generic",
        sefd_jy=10,
        mask="none",
        time_factor=1,
        freq_factor=1,
        event_ms=(100, 160),
        offpulse_ms=((0, 50),),
    )
    for path in paths:
        inspection = inspect_filterbank(path)
        assert inspection.freq_hi_mhz is not None and inspection.freq_lo_mhz is not None
        assert inspection.freq_hi_mhz > inspection.freq_lo_mhz
        record = measure_burst(path, options)
        assert record["status"] == "ok", record
        values = record["measurements"]
        for key in ("fluence_jyms", "peak_flux_jy", "snr_integrated"):
            assert values[key] > 0 and math.isfinite(values[key]), (path.suffix, key, values[key])
        # The injected Gaussian has sigma=2.5 ms, hence FWHM=5.887 ms.
        assert abs(values["width_ms_acf"] - 2.35482 * 2.5) < 0.6, values["width_ms_acf"]
        assert values["uncertainty_details"]["fluence_jyms"]["classification"] == "statistical_only"
        measurements.append(
            {"format": path.suffix, "fluence_jyms": values["fluence_jyms"], "acf_fwhm_ms": values["width_ms_acf"]}
        )
    assert np.isclose(measurements[0]["fluence_jyms"], measurements[2]["fluence_jyms"], rtol=1e-5)
    # Folded PSRFITS stores rounded int16 samples; the other fixtures keep float32.
    assert np.isclose(measurements[0]["fluence_jyms"], measurements[1]["fluence_jyms"], rtol=0.1)
    report["measurements"] = measurements
    print("SIGPROC, folded PSRFITS and HDF5 measurement checks passed", flush=True)

    session = BurstSession.from_file(str(paths[0]), dm=0, telescope="generic", sefd_jy=10)
    session.set_event_ms(100, 160)
    session.add_offpulse_ms(0, 50)
    fitted = session.fit_model(
        {
            "free_parameters": ["amplitude", "arrival_time", "burst_width"],
            "initial_parameters": {"scattering_timescale": [0.0]},
            "factor_time_upsample": 1,
        }
    )
    fit = fitted.diagnostics.model_fit
    assert fit is not None and fit.status == "ok", fit
    assert fit.fitter == "flits", fit.fitter
    assert fitted.width_ms_model is not None and abs(fitted.width_ms_model - 2.5) < 0.2, fitted.width_ms_model
    arrivals = fit.bestfit_parameters["arrival_time"]
    assert arrivals is not None and len(arrivals) == 1
    arrival_ms = arrivals[0] * 1e3
    assert abs(arrival_ms - 128) < 0.5, arrival_ms
    assert fitted.uncertainty_details["width_ms_model"].classification == "model_hessian"
    report["native_fit"] = {
        "sigma_ms": fitted.width_ms_model,
        "arrival_ms": arrival_ms,
        "evaluations": fit.function_evaluations,
    }
    print(f"Native fit: sigma={fitted.width_ms_model:.6f} ms, arrival={arrival_ms:.6f} ms", flush=True)

    manifest = session.export_results(session_id="wheel-smoke", include=["json", "csv", "plots"], plot_formats=["png"])
    plots = []
    ready_kinds: set[str] = set()
    for artifact in manifest.artifacts:
        if artifact.status != "ready":
            assert artifact.reason in {
                "polarization_analysis_unavailable",
                "drift_analysis_unavailable",
                "temporal_structure_unavailable",
                "dm_optimization_unavailable",
            }, (artifact.name, artifact.status, artifact.reason)
            continue
        ready_kinds.add(artifact.kind)
        _, content = session.get_export_artifact(manifest.export_id, artifact.name)
        assert content, artifact.name
        if artifact.name.endswith(".png"):
            assert content.startswith(b"\x89PNG\r\n\x1a\n"), artifact.name
            plots.append(artifact.name)
    assert plots, "No PNG plots were exported"
    assert {"structured", "catalog", "plot"} <= ready_kinds, ready_kinds
    for key in ("dynamic_spectrum", "profile_diagnostics", "acf_panel"):
        assert any(key in name for name in plots), f"Missing {key} plot"
    report["exports"] = {"artifacts": len(manifest.artifacts), "pngs": plots}
    print(f"Exported {len(plots)} PNG plots and structured results", flush=True)

    records = _cli(
        "measure",
        str(paths[0]),
        str(workspace / "missing.fil"),
        "--dm",
        "0",
        "--sefd",
        "10",
        "--mask",
        "none",
        "--snapshot-dir",
        str(workspace / "snapshots"),
        "--data-dir",
        str(workspace),
        expected_code=1,
    )
    assert [record["status"] for record in records] == ["ok", "error"], records
    replay = _cli("replay", records[0]["snapshot"], "--data-dir", str(workspace), "--check", "--json")
    assert replay["checked"] is True and replay["differences"] == [], replay
    report["cli"] = {"statuses": [record["status"] for record in records], "snapshot_replay": "ok"}
    print("CLI mixed-success batch and snapshot replay passed", flush=True)
    report["server"] = _server(workspace, log_path)
    print("HTTP server and interface assets passed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "running",
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python_executable": sys.executable,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="flits-wheel-") as temp:
            workspace = Path(temp)
            os.environ["MPLBACKEND"] = "Agg"
            os.environ["MPLCONFIGDIR"] = str(workspace / "matplotlib")
            _exercise(args.source_root.resolve(), workspace, report, output.with_name("wheel-smoke-server.log"))
        report["status"] = "ok"
    except BaseException as exc:
        report.update(status="error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
