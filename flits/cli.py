"""Command line entry point for FLITS.

``flits`` starts the browser interface. ``flits replay`` re-runs a saved session
snapshot without a browser, which is what makes the reproducibility claim
checkable: the snapshot records every interactive decision, and replaying it
recomputes the measurements from the original data file.

Invoking ``flits`` with server options and no subcommand still starts the
server, so existing commands and container entrypoints keep working.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from flits import __version__

logger = logging.getLogger("flits")

LOG_LEVELS = ("debug", "info", "warning", "error")

# Analyses that a snapshot can record, in the order they must be recomputed.
# Each entry maps the snapshot key to the method that regenerates it.
_REPLAYABLE_ANALYSES: tuple[tuple[str, str], ...] = (
    ("width_analysis", "compute_widths"),
    ("results", "compute_properties"),
)


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
    parser = argparse.ArgumentParser(prog=prog, description="Run the FLITS interface.")
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

    recomputed: list[str] = []
    for key, method_name in _REPLAYABLE_ANALYSES:
        if snapshot.get(key) is None and key != "results":
            continue
        getattr(session, method_name)()
        recomputed.append(key)
        logger.info("Recomputed %s", key)

    results = session.results.to_dict() if session.results is not None else None
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
        "exported": [str(path) for path in exported],
        "checked": bool(args.check),
        "differences": differences,
    }

    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)

    if args.check and differences:
        return 1
    return 0


def _print_report(report: dict[str, Any]) -> None:
    print(f"snapshot   {report['snapshot']}")
    print(f"burst      {report['burst_file']}")
    print(f"dm         {report['dm']}")
    print(f"recomputed {', '.join(report['recomputed']) or 'nothing'}")

    measurements = report.get("measurements") or {}
    for field in ("width_ms", "fluence_jyms", "peak_flux_jy", "snr"):
        if field in measurements and measurements[field] is not None:
            print(f"{field:<10} {measurements[field]}")

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


def serve(argv: Sequence[str]) -> int:
    """Run the ``flits serve`` command (also the default). Returns the exit code."""
    import uvicorn

    args = build_serve_parser().parse_args(list(argv))
    _configure_logging(args.log_level)

    if args.data_dir is not None:
        os.environ["FLITS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    if args.allow_outside_data_dir:
        os.environ["FLITS_ALLOW_OUTSIDE_DATA_DIR"] = "1"
    if args.cors_origin:
        os.environ["FLITS_CORS_ORIGINS"] = ",".join(args.cors_origin)

    logger.info("FLITS %s serving on http://%s:%d", __version__, args.host, args.port)
    uvicorn.run("flits.web.app:app", host=args.host, port=args.port, reload=False)
    return 0


COMMANDS = {
    "serve": serve,
    "replay": replay,
}


def _print_usage() -> None:
    print(
        "usage: flits [--help] [--version] <command> [options]\n"
        "\n"
        "commands:\n"
        "  serve    Start the browser interface (default when no command is given)\n"
        "  replay   Re-run a saved session snapshot without a browser\n"
        "\n"
        "Run `flits <command> --help` for command options.\n"
        "Server options may also be passed directly: `flits --data-dir DIR`."
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
