"""Command line entry point for FLITS.

``flits`` starts the browser interface. ``flits demo`` opens it on a prepared
example burst. ``flits measure`` measures bursts without a browser.
``flits replay`` re-runs a saved session snapshot without a browser, which is
what makes the reproducibility claim checkable: the snapshot records the
selected analysis state, and replaying it recomputes the measurements from the
original data file.

Invoking ``flits`` with server options and no subcommand still starts the
server, so existing commands and container entrypoints keep working.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from flits import __version__

logger = logging.getLogger("flits")

LOG_LEVELS = ("debug", "info", "warning", "error")

DOCS_URL = "https://dirkkuiper.github.io/flits/"
# Plain-Markdown index of the docs for language-model tools (https://llmstxt.org).
LLMS_TXT_URL = f"{DOCS_URL}llms.txt"

# Analyses that a snapshot can record, in the order they must be recomputed.
# Each entry maps the snapshot key to the method that regenerates it.
_REPLAYABLE_ANALYSES: tuple[tuple[str, str], ...] = (
    ("width_analysis", "compute_widths"),
    ("results", "compute_properties"),
    ("drift_analysis", "run_drift_analysis"),
    ("polarization", "run_polarization_analysis"),
)


class _HelpFormatter(argparse.HelpFormatter):
    """Wrap help text at spaces only, so options and URLs are never split."""

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        return textwrap.fill(
            " ".join(text.split()),
            width,
            initial_indent=indent,
            subsequent_indent=indent,
            break_on_hyphens=False,
            break_long_words=False,
        )

    def _split_lines(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False, break_long_words=False)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _add_log_level(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-level",
        default="info",
        choices=LOG_LEVELS,
        help="Logging verbosity (default: info).",
    )


def build_serve_parser(prog: str = "flits") -> argparse.ArgumentParser:
    """Return the parser for the server command."""
    parser = argparse.ArgumentParser(prog=prog, description="Run the FLITS interface.", formatter_class=_HelpFormatter)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory used for relative burst paths and known-file discovery.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000).")
    parser.add_argument(
        "--allow-outside-data-dir",
        action="store_true",
        help=(
            "Allow opening files outside --data-dir. Off by default: the data "
            "directory is a containment boundary, not only a browsing root."
        ),
    )
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=None,
        metavar="ORIGIN",
        help=(
            "Allow cross-origin browser requests from ORIGIN (repeatable). The "
            "bundled interface is same-origin and needs none; only add an origin "
            "when serving the interface from somewhere else."
        ),
    )
    _add_log_level(parser)
    return parser


def build_replay_parser(prog: str = "flits replay") -> argparse.ArgumentParser:
    """Return the parser for the replay command."""
    parser = argparse.ArgumentParser(
        prog=prog,
        formatter_class=_HelpFormatter,
        description=(
            "Re-run a saved session snapshot without a browser: reopen the burst "
            "it names, restore every selection it records, recompute the "
            "measurements, and optionally write an export bundle."
        ),
    )
    parser.add_argument("snapshot", type=Path, help="Path to a *_flits_session.json snapshot.")
    parser.add_argument(
        "--export",
        type=Path,
        default=None,
        metavar="DIR",
        help="Write the export bundle into DIR (created if needed).",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="KIND",
        help="Export artifact kind to include (repeatable): json, csv, npz, plots, window.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory to resolve the snapshot's burst path against.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Compare the recomputed measurements against the values stored in "
            "the snapshot and exit non-zero if they differ."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-9,
        help="Relative tolerance used by --check (default: 1e-9).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the replay report as JSON instead of text.",
    )
    _add_log_level(parser)
    return parser


def build_demo_parser(prog: str = "flits demo") -> argparse.ArgumentParser:
    """Return the parser for the demo command."""
    parser = argparse.ArgumentParser(
        prog=prog,
        formatter_class=_HelpFormatter,
        description=(
            "Open a prepared example burst in the interface. The first run downloads "
            "the public GBT burst from the guided workflow (42 MB, checked against its "
            "published SHA-256); later runs reuse it and work offline."
        ),
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Where to keep the demo burst and its session (default: $FLITS_DEMO_DIR, "
            "else ~/.cache/flits/demo). A copy of the burst placed here is reused."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000).")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser; print the link instead (useful over SSH).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Rebuild the demo session, discarding changes saved to it from the interface.",
    )
    _add_log_level(parser)
    return parser


def build_measure_parser(prog: str = "flits measure") -> argparse.ArgumentParser:
    """Return the parser for the measure command."""
    from flits.measure import MASK_CHOICES
    from flits.settings import PRESETS

    parser = argparse.ArgumentParser(
        prog=prog,
        formatter_class=_HelpFormatter,
        description=(
            "Measure bursts without a browser. Each file is loaded at --dm, masked "
            "automatically, localized automatically, and measured; give --event, "
            "--offpulse, --band or --crop to pin any of those choices instead. "
            "Times are in ms from the start of the file, frequencies in MHz. "
            "Results go to stdout (or --output) and progress to stderr. Exit code "
            "0 means every burst was measured, 1 that at least one was not found "
            "or failed, 2 an invalid command line."
        ),
        epilog=(
            "Example: flits measure burst.fil --dm 527.7 --snapshot-dir snapshots. "
            "Check each record's status and localization.warning_flags before "
            "using its numbers. From Python: "
            "flits.measure_burst(path, flits.MeasureOptions(dm=...)) returns the "
            f"same record. Docs: {DOCS_URL}user-guide/headless-measurement/"
        ),
    )
    parser.add_argument("files", nargs="*", type=Path, metavar="FILE", help="Burst files to measure.")
    parser.add_argument(
        "--manifest", type=Path, help="JSON manifest with per-burst settings; replaces FILE and selection options."
    )
    parser.add_argument(
        "--dm", type=float, help="Dispersion measure to load each burst at (pc cm^-3); required with FILE."
    )
    parser.add_argument(
        "--telescope",
        choices=sorted(PRESETS),
        default=None,
        help="Telescope preset (default: detected from each file).",
    )
    parser.add_argument("--sefd", type=float, default=None, metavar="JY", help="SEFD in Jy (default: the preset's).")
    parser.add_argument(
        "--sefd-uncertainty",
        type=float,
        default=None,
        metavar="FRACTION",
        help="Fractional SEFD uncertainty, carried into the flux and fluence uncertainties.",
    )
    parser.add_argument("--crop", nargs=2, type=float, metavar=("START", "END"), help="Restrict to this time range.")
    parser.add_argument(
        "--event",
        nargs=2,
        type=float,
        metavar=("START", "END"),
        help="Event window; skips automatic localization.",
    )
    parser.add_argument(
        "--offpulse",
        nargs=2,
        type=float,
        action="append",
        default=None,
        metavar=("START", "END"),
        help="Off-pulse noise window (repeatable).",
    )
    parser.add_argument("--band", nargs=2, type=float, metavar=("LOW", "HIGH"), help="Frequency band to measure over.")
    parser.add_argument("--time-factor", type=int, default=None, metavar="N", help="Time downsampling factor.")
    parser.add_argument("--freq-factor", type=int, default=None, metavar="N", help="Frequency downsampling factor.")
    parser.add_argument(
        "--mask",
        choices=MASK_CHOICES,
        default=None,
        help="Automatic interference-masking profile, or none (default: auto).",
    )
    parser.add_argument(
        "--detection-snr",
        type=float,
        default=None,
        metavar="SNR",
        help="Detection threshold for automatic localization (default: 6).",
    )
    parser.add_argument("--widths", action="store_true", default=None, help="Also run the width analysis.")
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        default=None,
        help="Include diagnostic profiles and autocorrelations in JSON output.",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Save each burst's session here, to open in the interface or check with "
            "`flits replay`. The interface lists sessions saved in a folder named "
            "'snapshots' under its data directory."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing snapshots instead of saving timestamped copies.",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "jsonl", "csv"),
        default="json",
        help="json: one array; jsonl: one record per line as each burst finishes; csv: a table (default: json).",
    )
    parser.add_argument("--output", type=Path, default=None, metavar="PATH", help="Write results here, not stdout.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Data directory that saved snapshots record their burst paths against.",
    )
    _add_log_level(parser)
    return parser


EXIT_INPUT_ERROR = 2


def _fail(message: str) -> NoReturn:
    """Report an input problem on stderr and exit with the documented code."""
    print(message, file=sys.stderr)
    raise SystemExit(EXIT_INPUT_ERROR)


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"Snapshot not found: {path}")
    except json.JSONDecodeError as exc:
        _fail(f"Snapshot is not valid JSON ({path}): {exc}")
    if not isinstance(payload, dict):
        _fail(f"Snapshot must be a JSON object: {path}")
    return payload


def _numeric_items(payload: Any, prefix: str = "") -> dict[str, float]:
    """Flatten a nested result payload into comparable numeric leaves."""
    flat: dict[str, float] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            flat.update(_numeric_items(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            flat.update(_numeric_items(value, f"{prefix}[{index}]"))
    elif isinstance(payload, bool):
        pass
    elif isinstance(payload, (int, float)):
        flat[prefix] = float(payload)
    return flat


def _compare_measurements(
    recomputed: dict[str, Any] | None,
    stored: dict[str, Any] | None,
    tolerance: float,
) -> list[dict[str, Any]]:
    """Return the numeric fields that changed between stored and recomputed results."""
    if not stored or not recomputed:
        return []

    stored_flat = _numeric_items(stored)
    fresh_flat = _numeric_items(recomputed)

    differences: list[dict[str, Any]] = []
    for key, stored_value in stored_flat.items():
        if key not in fresh_flat:
            differences.append({"field": key, "stored": stored_value, "recomputed": None})
            continue
        fresh_value = fresh_flat[key]
        if stored_value == fresh_value:
            continue
        scale = max(abs(stored_value), abs(fresh_value), 1e-30)
        if abs(stored_value - fresh_value) / scale > tolerance:
            differences.append({"field": key, "stored": stored_value, "recomputed": fresh_value})
    return differences


def _write_export(session: Any, destination: Path, include: Sequence[str] | None) -> list[Path]:
    """Build an export bundle and write every ready artifact into `destination`."""
    destination.mkdir(parents=True, exist_ok=True)
    manifest = session.export_results(
        session_id="replay",
        include=list(include) if include else None,
    )

    written: list[Path] = []
    for artifact in manifest.artifacts:
        if artifact.status != "ready":
            logger.warning("Skipping %s: %s", artifact.name, artifact.reason or artifact.status)
            continue
        _, content = session.get_export_artifact(manifest.export_id, artifact.name)
        target = destination / artifact.name
        target.write_bytes(content)
        written.append(target)
        logger.info("Wrote %s (%d bytes)", target, len(content))

    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
    written.append(manifest_path)
    return written


def replay(argv: Sequence[str]) -> int:
    """Run the ``flits replay`` command. Returns the process exit code."""
    from flits.session import BurstSession

    args = build_replay_parser().parse_args(list(argv))
    _configure_logging(args.log_level)

    if args.data_dir is not None:
        os.environ["FLITS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    snapshot = _load_snapshot(args.snapshot)
    stored_results = snapshot.get("results")

    logger.info("Replaying %s", args.snapshot)
    try:
        session = BurstSession.from_snapshot(snapshot)
    except FileNotFoundError as exc:
        _fail(f"Could not reopen the burst named by the snapshot: {exc}")
    except ValueError as exc:
        _fail(f"Snapshot could not be replayed: {exc}")

    logger.info("Reopened %s at DM %.6f", session.burst_file, session.dm)
    provenance_warnings = session.provenance_warnings()
    for message in provenance_warnings:
        logger.warning(message)

    recomputed: list[str] = []
    for key, method_name in _REPLAYABLE_ANALYSES:
        if snapshot.get(key) is None and key != "results":
            continue
        getattr(session, method_name)()
        recomputed.append(key)
        logger.info("Recomputed %s", key)

    results = session.results.to_dict() if session.results is not None else None
    drift = session.drift_analysis
    drift_report = (
        None
        if drift is None
        else {
            "drift_rate_mhz_per_ms": drift.drift_rate_mhz_per_ms,
            "drift_rate_uncertainty_mhz_per_ms": drift.drift_rate_uncertainty_mhz_per_ms,
            "drift_rate_status": drift.drift_rate_status,
            "dm_equivalent_pc_cm3": drift.dm_equivalent_pc_cm3,
            "component_drift_rate_mhz_per_ms": drift.component_drift_rate_mhz_per_ms,
            "component_drift_status": drift.component_drift_status,
            "warning_flags": list(drift.warning_flags),
        }
    )
    polarization = session.polarization
    polarization_report = (
        None
        if polarization is None
        else {
            "peak_rm_rad_m2": polarization.peak_rm_rad_m2,
            "peak_rm_uncertainty_rad_m2": polarization.peak_rm_uncertainty_rad_m2,
            "calibration_status": polarization.calibration_status,
            "polarization_basis": polarization.polarization_basis,
            "channel_count": int(len(polarization.freqs_mhz)),
        }
    )
    differences = _compare_measurements(results, stored_results, args.tolerance) if args.check else []

    exported: list[Path] = []
    if args.export is not None:
        exported = _write_export(session, args.export, args.include)

    report = {
        "snapshot": str(args.snapshot),
        "burst_file": session.burst_file,
        "dm": session.dm,
        "recomputed": recomputed,
        "measurements": results,
        "drift": drift_report,
        "polarization": polarization_report,
        "exported": [str(path) for path in exported],
        "checked": bool(args.check),
        "differences": differences,
        "provenance_warnings": provenance_warnings,
        "software_provenance": session.software_provenance(),
    }

    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)

    if args.check and differences:
        return 1
    return 0


def measure(argv: Sequence[str]) -> int:
    """Run the ``flits measure`` command. Returns the process exit code."""
    import contextlib

    from flits.measure import CSV_COLUMNS, MeasureOptions, csv_row, format_records, measure_bursts

    parser = build_measure_parser()
    args = parser.parse_args(list(argv))
    _configure_logging(args.log_level)

    if args.data_dir is not None:
        os.environ["FLITS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    if args.manifest is not None:
        from flits.manifest import load_measure_manifest, measure_manifest_entries

        per_burst_flags = (
            "dm",
            "telescope",
            "sefd",
            "sefd_uncertainty",
            "crop",
            "event",
            "offpulse",
            "band",
            "time_factor",
            "freq_factor",
            "mask",
            "detection_snr",
            "widths",
            "diagnostics",
        )
        if args.files or any(getattr(args, name) is not None for name in per_burst_flags):
            parser.error("--manifest replaces FILE and per-burst options; put those settings in the manifest")
        try:
            entries = load_measure_manifest(args.manifest)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        total = len(entries)
        iterator = measure_manifest_entries(entries, snapshot_dir=args.snapshot_dir, overwrite=args.overwrite)
        input_paths = [args.manifest, *(entry.file for entry in entries)]
    else:
        if not args.files or args.dm is None:
            parser.error("provide FILE... and --dm, or use --manifest PATH")
        options = MeasureOptions(
            dm=args.dm,
            telescope=args.telescope,
            sefd_jy=args.sefd,
            sefd_fractional_uncertainty=args.sefd_uncertainty,
            crop_ms=None if args.crop is None else (args.crop[0], args.crop[1]),
            event_ms=None if args.event is None else (args.event[0], args.event[1]),
            offpulse_ms=tuple((start, end) for start, end in (args.offpulse or [])),
            band_mhz=None if args.band is None else (args.band[0], args.band[1]),
            time_factor=args.time_factor,
            freq_factor=args.freq_factor,
            mask=args.mask or "auto",
            detection_snr=6.0 if args.detection_snr is None else args.detection_snr,
            widths=bool(args.widths),
            include_diagnostics=bool(args.diagnostics),
        )
        total = len(args.files)
        iterator = measure_bursts(args.files, options, snapshot_dir=args.snapshot_dir, overwrite=args.overwrite)
        input_paths = args.files

    if args.output is not None:
        for input_path in input_paths:
            same_file = args.output.resolve() == input_path.resolve()
            if not same_file:
                with contextlib.suppress(OSError):
                    same_file = args.output.samefile(input_path)
            if same_file:
                parser.error(f"--output would replace an input file: {input_path}")

    with contextlib.ExitStack() as stack:
        if args.output is None:
            out = sys.stdout
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            out = stack.enter_context(args.output.open("w", encoding="utf-8", newline=""))

        if args.output_format == "csv":
            import csv

            writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
            writer.writeheader()

        records: list[dict[str, Any]] = []
        for index, record in enumerate(iterator, start=1):
            records.append(record)
            if record["status"] == "error":
                logger.error("[%d/%d] %s: %s", index, total, record["file"], record["error"])
            else:
                logger.info("[%d/%d] %s: %s", index, total, record["file"], record["status"])
            # Stream line-based formats, so a long batch can be followed and
            # its finished bursts survive an interruption.
            if args.output_format == "jsonl":
                out.write(format_records([record], "jsonl"))
                out.flush()
            elif args.output_format == "csv":
                writer.writerow(csv_row(record))
                out.flush()

        if args.output_format == "json":
            out.write(format_records(records, "json"))

    if args.output is not None:
        logger.info("Wrote %s", args.output)
    return 0 if all(record["status"] in {"ok", "low_sn"} for record in records) else 1


def inspect(argv: Sequence[str]) -> int:
    """Read metadata only, retaining a record for every input."""
    from flits.inspection import INSPECTION_SCHEMA_VERSION, inspect_burst

    parser = argparse.ArgumentParser(
        prog="flits inspect",
        formatter_class=_HelpFormatter,
        description="Inspect burst file metadata without loading a waterfall or measuring. Prints a JSON array.",
    )
    parser.add_argument("files", nargs="+", type=Path, metavar="FILE")
    args = parser.parse_args(list(argv))
    records = []
    for path in args.files:
        try:
            records.append(inspect_burst(path))
        except Exception as exc:
            records.append(
                {
                    "schema_version": INSPECTION_SCHEMA_VERSION,
                    "file": str(path.expanduser().resolve()),
                    "status": "error",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
    print(json.dumps(records, indent=2, allow_nan=False))
    return 0 if all(record["status"] == "ok" for record in records) else 1


def info(argv: Sequence[str]) -> int:
    """Identify local code, optionally comparing it with a running server."""
    from urllib.error import URLError
    from urllib.parse import urlsplit
    from urllib.request import urlopen

    from flits.runtime import compare_server_runtime, runtime_info

    parser = argparse.ArgumentParser(
        prog="flits info",
        formatter_class=_HelpFormatter,
        description=(
            "Print runtime identity and capabilities as JSON. Local paths identify source-checkout shadowing. "
            "With --server, compare the running server's FLITS code against this interpreter. "
            "Exit 1 on mismatch, unidentified legacy server, or connection failure."
        ),
    )
    parser.add_argument("--server", metavar="URL", help="Server base URL, for example http://127.0.0.1:8000.")
    args = parser.parse_args(list(argv))
    local = runtime_info(include_paths=True)
    if args.server is None:
        print(json.dumps(local, indent=2, allow_nan=False))
        return 0
    if urlsplit(args.server).scheme not in {"http", "https"}:
        parser.error("--server must be an http or https base URL")
    report: dict[str, Any] = {"local": local, "server": None, "same_flits_code": None, "error": None}
    try:
        with urlopen(args.server.rstrip("/") + "/api/health", timeout=10) as response:
            server = json.load(response)
        if not isinstance(server, dict) or server.get("status") != "ok":
            raise ValueError("Server did not return a FLITS health object with status ok")
        json.dumps(server, allow_nan=False)
        report["server"] = server
        report["same_flits_code"] = compare_server_runtime(local, server)
    except (OSError, URLError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["same_flits_code"] is True else 1


def _print_report(report: dict[str, Any]) -> None:
    print(f"snapshot   {report['snapshot']}")
    print(f"burst      {report['burst_file']}")
    print(f"dm         {report['dm']}")
    print(f"recomputed {', '.join(report['recomputed']) or 'nothing'}")

    measurements = report.get("measurements") or {}
    for field in ("width_ms", "fluence_jyms", "peak_flux_jy", "snr"):
        if field in measurements and measurements[field] is not None:
            print(f"{field:<10} {measurements[field]}")

    polarization = report.get("polarization")
    if polarization:
        rm = polarization.get("peak_rm_rad_m2")
        sigma = polarization.get("peak_rm_uncertainty_rad_m2")
        if rm is not None:
            uncertainty = "" if sigma is None else f" +/- {sigma}"
            print(f"{'rm_rad_m2':<10} {rm}{uncertainty} ({polarization.get('calibration_status')})")

    if report["exported"]:
        print(f"exported   {len(report['exported'])} files")
        for path in report["exported"]:
            print(f"           {path}")

    if report["checked"]:
        differences = report["differences"]
        if not differences:
            print("check      OK - recomputed measurements match the snapshot")
        else:
            print(f"check      FAILED - {len(differences)} field(s) differ")
            for difference in differences[:20]:
                print(
                    f"           {difference['field']}: "
                    f"stored={difference['stored']} recomputed={difference['recomputed']}"
                )
            if len(differences) > 20:
                print(f"           ... and {len(differences) - 20} more")


def _run_server(host: str, port: int) -> None:
    import uvicorn

    logger.info("FLITS %s serving on http://%s:%d", __version__, host, port)
    uvicorn.run("flits.web.app:app", host=host, port=port, reload=False)


def serve(argv: Sequence[str]) -> int:
    """Run the ``flits serve`` command (also the default). Returns the exit code."""
    args = build_serve_parser().parse_args(list(argv))
    _configure_logging(args.log_level)

    if args.data_dir is not None:
        os.environ["FLITS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    if args.allow_outside_data_dir:
        os.environ["FLITS_ALLOW_OUTSIDE_DATA_DIR"] = "1"
    if args.cors_origin:
        os.environ["FLITS_CORS_ORIGINS"] = ",".join(args.cors_origin)

    _run_server(args.host, args.port)
    return 0


def _browser_host(host: str) -> str:
    """A host a local browser can reach, for servers bound to every interface."""
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


def _open_browser_when_ready(health_url: str, url: str, timeout_s: float = 60.0) -> None:
    """Open `url` once the server answers `health_url`; give up quietly after `timeout_s`."""
    import time
    import webbrowser
    from urllib.request import urlopen

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urlopen(health_url, timeout=2):
                break
        except OSError:
            time.sleep(0.25)
    else:
        return
    webbrowser.open(url)


def demo(argv: Sequence[str]) -> int:
    """Run the ``flits demo`` command. Returns the process exit code."""
    import threading
    from urllib.parse import quote

    from flits.demo import GUIDE_URL, DemoError, default_demo_dir, fetch_demo_burst, write_demo_snapshot

    args = build_demo_parser().parse_args(list(argv))
    _configure_logging(args.log_level)

    directory = (args.dir or default_demo_dir()).expanduser().resolve()
    # The demo directory is the data directory, so the snapshot records its burst
    # relative to it and the interface lists both.
    os.environ["FLITS_DATA_DIR"] = str(directory)
    try:
        burst = fetch_demo_burst(directory)
        snapshot = write_demo_snapshot(directory, burst, reset=args.reset)
    except DemoError as exc:
        print(exc, file=sys.stderr)
        return 1

    base_url = f"http://{_browser_host(args.host)}:{args.port}"
    url = f"{base_url}/?open-snapshot={quote(snapshot.relative_to(directory).as_posix())}"
    print(f"Demo burst  {burst}")
    print(f"Session     {snapshot}")
    print(f"Open        {url}")
    print(f"Guide       {GUIDE_URL}", flush=True)
    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(f"{base_url}/api/health", url),
            daemon=True,
        ).start()

    _run_server(args.host, args.port)
    return 0


def schema(argv: Sequence[str]) -> int:
    """Print a packaged JSON schema without performing an analysis."""
    from flits.schema import SCHEMAS, get_schema

    parser = argparse.ArgumentParser(prog="flits schema", description="Print an offline JSON schema.")
    parser.add_argument("name", choices=SCHEMAS)
    args = parser.parse_args(list(argv))
    print(json.dumps(get_schema(args.name), indent=2))
    return 0


COMMANDS = {
    "serve": serve,
    "replay": replay,
    "demo": demo,
    "measure": measure,
    "inspect": inspect,
    "info": info,
    "schema": schema,
}


def _print_usage() -> None:
    print(
        "usage: flits [--help] [--version] <command> [options]\n"
        "\n"
        "commands:\n"
        "  serve    Start the browser interface (default when no command is given)\n"
        "  demo     Open a prepared example burst in the interface\n"
        "  measure  Measure bursts without a browser (JSON, JSONL or CSV)\n"
        "  inspect  Read burst metadata without starting an analysis (JSON)\n"
        "  info     Identify loaded FLITS code and compare a running server (JSON)\n"
        "  schema   Print a versioned measurement or batch-manifest JSON schema\n"
        "  replay   Re-run a saved session snapshot without a browser\n"
        "\n"
        "examples:\n"
        "  flits measure burst.fil --dm 527.7\n"
        "  flits measure data/*.fil --dm 527.7 --format csv --output results.csv\n"
        "  flits --data-dir data\n"
        "  flits demo\n"
        "\n"
        "Run `flits <command> --help` for command options.\n"
        "Server options may also be passed directly: `flits --data-dir DIR`.\n"
        f"Docs: {DOCS_URL}\n"
        f"Docs for language models: {LLMS_TXT_URL}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch a FLITS subcommand, defaulting to the server."""
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "--version":
        print(f"flits {__version__}")
        return 0

    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:])

    if argv and argv[0] in {"-h", "--help"}:
        _print_usage()
        print()
        build_serve_parser().print_help()
        return 0

    # No subcommand: treat everything as server options so that existing
    # invocations such as `flits --data-dir DIR --port 8123` keep working.
    return serve(argv)


if __name__ == "__main__":
    raise SystemExit(main())
