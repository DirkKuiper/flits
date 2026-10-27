"""Validated per-burst settings for heterogeneous measurement batches."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from flits.measure import MASK_CHOICES, MeasureOptions, error_record, measure_burst
from flits.settings import PRESETS

MANIFEST_SCHEMA_VERSION = "1.0"
_OPTION_NAMES = frozenset(item.name for item in fields(MeasureOptions))


@dataclass(frozen=True)
class ManifestEntry:
    file: Path
    options: MeasureOptions
    id: str | None = None


def _object(value: Any, allowed: set[str] | frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"Unknown {label} field(s): {', '.join(sorted(unknown))}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError(f"Non-finite JSON number: {value}")


def _options(payload: dict[str, Any]) -> MeasureOptions:
    # JSON mode accepts arrays for tuple-valued windows while strict mode
    # rejects numeric strings and booleans in numeric settings.
    options = TypeAdapter(MeasureOptions).validate_json(json.dumps(payload, allow_nan=False), strict=True)
    for name in ("dm", "sefd_jy", "sefd_fractional_uncertainty", "detection_snr"):
        value = getattr(options, name)
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if options.mask not in MASK_CHOICES:
        raise ValueError(f"Unknown mask profile: {options.mask}")
    if options.telescope is not None and options.telescope not in PRESETS:
        raise ValueError(f"Unknown telescope preset: {options.telescope}")
    for name in ("time_factor", "freq_factor", "sefd_jy", "detection_snr"):
        value = getattr(options, name)
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be positive")
    if options.sefd_fractional_uncertainty is not None and options.sefd_fractional_uncertainty < 0:
        raise ValueError("sefd_fractional_uncertainty must be non-negative")
    for name in ("crop_ms", "event_ms", "band_mhz", "offpulse_ms"):
        value = getattr(options, name)
        ranges = value if name == "offpulse_ms" else (() if value is None else (value,))
        for start, end in ranges:
            if not all(math.isfinite(number) for number in (start, end)) or start < 0 or end <= start:
                raise ValueError(f"{name} must contain finite, non-negative, increasing ranges")
    return options


def load_measure_manifest(path: str | Path) -> list[ManifestEntry]:
    """Validate the whole manifest before analysis; resolve files relative to it.

    File existence and readability are checked during measurement so a missing
    burst gets an error record without stopping valid inputs.
    """
    source = Path(path).expanduser().resolve()
    payload = json.loads(
        source.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )
    manifest = _object(payload, {"schema_version", "defaults", "bursts"}, "manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"Unsupported manifest schema_version; expected {MANIFEST_SCHEMA_VERSION!r}")
    defaults = _object(manifest.get("defaults", {}), _OPTION_NAMES, "defaults")
    try:
        _options({"dm": 0.0, **defaults})
    except ValueError as exc:
        raise ValueError(f"defaults: {exc}") from exc
    bursts = manifest.get("bursts")
    if not isinstance(bursts, list) or not bursts:
        raise ValueError("bursts must be a non-empty array")
    identifiers: set[str] = set()
    entries = []
    for index, value in enumerate(bursts):
        label = f"bursts[{index}]"
        row = _object(value, {"id", "file", "options"}, label)
        filename = row.get("file")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError(f"{label}.file must be a non-empty string")
        identifier = row.get("id")
        if "id" in row:
            if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
                raise ValueError(f"{label}.id must be a unique, non-empty string")
            identifiers.add(identifier)
        overrides = _object(row.get("options", {}), _OPTION_NAMES, f"{label}.options")
        try:
            options = _options({**defaults, **overrides})
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc
        file = Path(filename).expanduser()
        if not file.is_absolute():
            file = source.parent / file
        entries.append(ManifestEntry(file.resolve(), options, identifier))
    return entries


def measure_manifest_entries(
    entries: list[ManifestEntry], *, snapshot_dir: Path | None = None, overwrite: bool = False
) -> Iterable[dict[str, Any]]:
    """Measure validated entries in order, including per-file failures."""
    taken: set[Path] = set()
    for entry in entries:
        try:
            record = measure_burst(
                entry.file, entry.options, snapshot_dir=snapshot_dir, overwrite=overwrite, _taken=taken
            )
        except Exception as exc:
            record = error_record(entry.file, exc)
        if entry.id is not None:
            record["id"] = entry.id
        yield record
