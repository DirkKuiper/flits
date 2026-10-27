"""Read input metadata before choosing a loading DM or starting an analysis."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from flits.io import FilterbankInspection, detect_reader
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

    Raises on missing or unsupported input. The CLI reports such failures per
    file, so one invalid input does not prevent inspection of the others.
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Burst file not found: {resolved}")
    reader = detect_reader(resolved)
    inspection = reader.inspect(resolved)
    suggested_dm, guidance = loading_dm_hint(inspection)
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
        }
    )
