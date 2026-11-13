"""Read input metadata before choosing a loading DM or starting an analysis."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from flits.io import FilterbankInspection, detect_reader
from flits.io.load_budget import estimate_load, load_budget_bytes, max_window_sec
from flits.measure import _jsonable

INSPECTION_SCHEMA_VERSION = "1.0"


def loading_dm_hint(inspection: FilterbankInspection) -> tuple[float | None, str | None]:
    """Return a loading DM only when the input format establishes its meaning."""
    if inspection.schema_version == "chime_frb_catalog_v1":
        return 0.0, "already dedispersed; use DM 0"
    if inspection.schema_version == "chime_bbdata_beamformed_v1" and inspection.coherent_dm is not None:
        dm = float(inspection.coherent_dm)
        return dm, f"coherently dedispersed at {dm:.6f}; FLITS applies residual DM relative to that value"
    return None, None


def inspect_burst(path: str | Path) -> dict[str, Any]:
    """Inspect a file without loading its waterfall or measuring a burst.

    Besides the header metadata, the record says how long the file is, its size
    on disk, the memory FLITS estimates loading all of it would take
    (`estimated_load_bytes`, Stokes I), the current load budget
    (`load_budget_bytes`, None for no limit) and the longest read window that
    fits it (`max_window_s`, None when unlimited or unknown).

    Raises on missing or unsupported input. The CLI reports such failures per
    file, so one invalid input does not prevent inspection of the others.
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Burst file not found: {resolved}")
    reader = detect_reader(resolved)
    inspection = reader.inspect(resolved)
    suggested_dm, guidance = loading_dm_hint(inspection)
    estimate = estimate_load(inspection)
    budget = load_budget_bytes()
    return _jsonable(
        {
            "schema_version": INSPECTION_SCHEMA_VERSION,
            "file": str(resolved),
            "status": "ok",
            "error": None,
            "error_type": None,
            "format_id": reader.format_id,
            "metadata": asdict(inspection),
            "suggested_dm": suggested_dm,
            "dm_guidance": guidance,
            "stokes_available": inspection.stokes_available,
            "duration_s": inspection.duration_s,
            "nsamples": inspection.nsamples,
            "bytes_on_disk": resolved.stat().st_size,
            "estimated_load_bytes": None if estimate is None else estimate.estimated_load_bytes,
            "load_budget_bytes": budget,
            "max_window_s": None if budget is None else max_window_sec(inspection, budget),
        }
    )
