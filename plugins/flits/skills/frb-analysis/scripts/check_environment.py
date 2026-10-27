"""Check the selected Python environment before following the FLITS skill.

This uses the standard library so a missing or older installation can report a
useful result. It never installs packages or changes the environment itself.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from typing import Any

MINIMUM_VERSION = (1, 6, 0)
INSTALL_COMMAND = 'python -m pip install --upgrade "flits>=1.6"'
OPTIONAL_CAPABILITIES = ("native_fitting", "component_comparison")


def check_environment(required: tuple[str, ...] = ()) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ready": False,
        "python": sys.executable,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "flits_version": None,
        "flits_path": None,
        "problems": [],
        "capabilities": dict.fromkeys(OPTIONAL_CAPABILITIES, False),
        "install_command": INSTALL_COMMAND,
    }
    problems: list[str] = report["problems"]
    if sys.version_info < (3, 11):  # noqa: UP036 - this helper also diagnoses unsupported interpreters
        problems.append("Select Python 3.11 or later before installing FLITS.")
        return report
    try:
        flits = importlib.import_module("flits")
        report["flits_version"] = version = str(getattr(flits, "__version__", "unknown"))
        report["flits_path"] = str(getattr(flits, "__file__", "unknown"))
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
        if match is None or tuple(map(int, match.groups())) < MINIMUM_VERSION:
            problems.append("This skill requires FLITS 1.6 or later.")
        for name in ("MeasureOptions", "measure_burst", "measure_bursts", "BurstSession"):
            if not callable(getattr(flits, name, None)):
                problems.append(f"Missing Python entry point: flits.{name}")
        session = getattr(flits, "BurstSession", None)
        for name in ("from_file", "auto_mask", "auto_localize", "compute_properties"):
            if not callable(getattr(session, name, None)):
                problems.append(f"Missing session method: BurstSession.{name}")
        report["capabilities"]["component_comparison"] = callable(getattr(session, "compare_model_components", None))
        if callable(getattr(session, "fit_model", None)):
            try:
                fitter = importlib.import_module("flits.analysis.fitting.model_fit")
                report["capabilities"]["native_fitting"] = getattr(fitter, "FITTER_NAME", None) == "flits"
            except Exception:
                # A fitting dependency must not block an otherwise usable measurement environment.
                pass
    except Exception as exc:
        problems.append(f"Cannot load FLITS: {type(exc).__name__}: {exc}")
    for name in required:
        if not report["capabilities"].get(name, False):
            problems.append(
                f"Installed FLITS lacks requested capability: {name}. "
                "Use a FLITS release or source checkout that provides it; basic measurement may still be available."
            )
    report["ready"] = not problems
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", action="append", choices=OPTIONAL_CAPABILITIES, default=[])
    args = parser.parse_args(argv)
    report = check_environment(tuple(args.require))
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
