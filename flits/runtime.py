"""Identify the loaded code, independently of cached scientific results.

The identity is captured once per process. Server responses omit local paths;
the local command can include them to diagnose source-checkout shadowing.
"""

from __future__ import annotations

import copy
import platform
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from flits import __version__
from flits.provenance import source_identity

RUNTIME_SCHEMA_VERSION = "1.0"


@lru_cache(maxsize=1)
def _identity() -> dict[str, Any]:
    from flits.analysis.fitting.model_fit import FITTER_NAME

    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "instance_id": uuid4().hex,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "flits_version": __version__,
        "python_version": platform.python_version(),
        "fitter": FITTER_NAME,
        "source": source_identity(Path(__file__).resolve().parent),
        "capabilities": ["measure", "inspect", "info", "replay", "serve", "native_fitting"],
    }


def runtime_info(*, include_paths: bool = False) -> dict[str, Any]:
    """Describe this process's code; local interpreter/package paths are opt-in."""
    report = copy.deepcopy(_identity())
    if include_paths:
        report["python_executable"] = sys.executable
        report["package_path"] = str(Path(__file__).resolve().parent)
    return report


def compare_server_runtime(local: dict[str, Any], server: dict[str, Any]) -> bool | None:
    """Compare FLITS code identities, leaving unidentified/legacy servers unknown.

    This checks FLITS code, not equality of all third-party dependencies. Each
    scientific product's software provenance describes its own environment.
    """
    if not all(server.get(key) for key in ("flits_version", "fitter")):
        return None
    if any(server[key] != local[key] for key in ("flits_version", "fitter")):
        return False
    local_source, server_source = local.get("source"), server.get("source")
    if not isinstance(local_source, dict) or not isinstance(server_source, dict):
        return None
    local_hash = local_source.get("python_source_sha256")
    server_hash = server_source.get("python_source_sha256")
    if not local_hash or not server_hash:
        return None
    return bool(local_hash == server_hash)
