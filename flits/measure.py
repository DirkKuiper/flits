"""Measure bursts without the interface: from a file to numbers in one call.

:func:`measure_burst` runs the steps the interface offers, in the order a user
takes them: load at a DM, optionally crop, mask interference automatically,
localize the burst automatically, then measure. Any step can be pinned instead
of automated by giving it explicitly, such as an event window or a frequency
band. ``flits measure`` is the command-line form and handles many files.

The result is a plain, JSON-compatible record of what was selected and what was
measured. Saving the session alongside it (``snapshot_dir``) keeps every choice,
so a burst measured this way can be opened in the interface, checked with
``flits replay``, or corrected by hand.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flits import __version__

STATUSES = ("ok", "low_sn", "no_detection", "error")
"""Outcomes of a measurement: measured, measured with low S/N, nothing found to
measure, or the burst could not be processed."""

MASK_CHOICES = ("fast", "auto", "thorough", "none")
MEASUREMENT_SCHEMA_VERSION = "1.0"

_SNAPSHOT_SUFFIX = "_flits_session.json"


@dataclass(frozen=True)
class MeasureOptions:
    """How to measure a burst. Times are in ms from the start of the file, frequencies in MHz.

    Leaving `event_ms` unset localizes the burst automatically; `offpulse_ms`
    and `band_mhz` then replace the localized values when given. With
    `event_ms` set, unset off-pulse windows fall back to everything outside the
    event and an unset band to the full band.
    """

    dm: float
    telescope: str | None = None
    sefd_jy: float | None = None
    sefd_fractional_uncertainty: float | None = None
    crop_ms: tuple[float, float] | None = None
    event_ms: tuple[float, float] | None = None
    offpulse_ms: tuple[tuple[float, float], ...] = ()
    band_mhz: tuple[float, float] | None = None
    time_factor: int | None = None
    freq_factor: int | None = None
    mask: str = "auto"
    detection_snr: float = 6.0
    widths: bool = False
    include_diagnostics: bool = False


def _jsonable(value: Any) -> Any:
    """Plain JSON data: numpy scalars unwrapped, non-finite numbers as null."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item") and callable(value.item) and getattr(value, "ndim", None) == 0:
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def _snapshot_target(snapshot_dir: Path, burst_path: Path, *, overwrite: bool, taken: set[Path]) -> Path:
    """Name the snapshot like the interface does, without replacing an existing one."""
    target = snapshot_dir / f"{burst_path.stem}{_SNAPSHOT_SUFFIX}"
    if overwrite and target not in taken:
        return target
    if target.exists() or target in taken:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        candidate = snapshot_dir / f"{burst_path.stem}_{stamp}{_SNAPSHOT_SUFFIX}"
        counter = 2
        while candidate.exists() or candidate in taken:
            candidate = snapshot_dir / f"{burst_path.stem}_{stamp}-{counter}{_SNAPSHOT_SUFFIX}"
            counter += 1
        target = candidate
    return target


def measure_burst(
    path: str | Path,
    options: MeasureOptions,
    *,
    snapshot_dir: Path | None = None,
    overwrite: bool = False,
    _taken: set[Path] | None = None,
) -> dict[str, Any]:
    """Measure one burst and return a JSON-compatible record of the result.

    Problems with the input, such as a missing file or an unknown preset, raise;
    a burst that loads but cannot be found is reported as ``no_detection``.
    With `snapshot_dir`, the session is saved there, without replacing an
    existing snapshot unless `overwrite` is set.
    """
    from flits.session import BurstSession

    burst_path = Path(path)
    if not burst_path.is_file():
        raise FileNotFoundError(f"Burst file not found: {burst_path}")

    mask_profile = None if options.mask == "none" else options.mask
    session = BurstSession.from_file(
        str(burst_path),
        dm=float(options.dm),
        telescope=options.telescope,
        sefd_jy=options.sefd_jy,
        sefd_fractional_uncertainty=options.sefd_fractional_uncertainty,
        auto_mask_profile=mask_profile or "auto",
    )
    if options.time_factor is not None:
        session.set_time_factor(int(options.time_factor))
    if options.freq_factor is not None:
        session.set_freq_factor(int(options.freq_factor))
    if options.crop_ms is not None:
        session.set_crop_ms(*options.crop_ms)
    if mask_profile is not None:
        session.auto_mask(mask_profile)

    localization = None
    if options.event_ms is None:
        localization = session.auto_localize(detection_snr_threshold=float(options.detection_snr))
    else:
        session.set_event_ms(*options.event_ms)
        session.clear_offpulse()
    if options.offpulse_ms:
        session.clear_offpulse()
        for start_ms, end_ms in options.offpulse_ms:
            session.add_offpulse_ms(start_ms, end_ms)
    if options.band_mhz is not None:
        session.set_spectral_extent_freq(*options.band_mhz)

    detected = localization is None or localization.status != "no_detection"
    measurements: dict[str, Any] | None = None
    if detected:
        if options.widths:
            session.compute_widths()
        measurements = session.compute_properties().to_dict()
        if not options.include_diagnostics:
            measurements.pop("diagnostics", None)

    if not detected:
        status = "no_detection"
    elif (localization is not None and localization.status == "low_sn") or "low_sn" in (
        (measurements or {}).get("measurement_flags") or []
    ):
        status = "low_sn"
    else:
        status = "ok"

    snapshot_path: Path | None = None
    if snapshot_dir is not None:
        taken = set() if _taken is None else _taken
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = _snapshot_target(snapshot_dir, burst_path, overwrite=overwrite, taken=taken)
        taken.add(snapshot_path)
        snapshot_path.write_text(
            json.dumps(_jsonable(session.snapshot_dict()), indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    spec_lo, spec_hi = sorted((int(session.spec_ex_lo), int(session.spec_ex_hi)))
    band = sorted((float(session.freqs[spec_lo]), float(session.freqs[spec_hi])))
    channel_mask = session.channel_mask
    masked_channels = 0 if channel_mask is None else int(channel_mask.sum())
    record = {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "file": str(burst_path),
        "status": status,
        "error": None,
        "flits_version": __version__,
        "dm": float(session.dm),
        "telescope": session.config.preset_key,
        "selection": {
            "event_source": "auto_localized" if localization is not None else "manual",
            "crop_ms": [session.bin_to_ms(session.crop_start), session.bin_to_ms(session.crop_end)],
            "event_ms": [session.bin_to_ms(session.event_start), session.bin_to_ms(session.event_end)],
            "offpulse_ms": [[session.bin_to_ms(lo), session.bin_to_ms(hi)] for lo, hi in session.offpulse_regions],
            "band_mhz": band,
            "time_factor": int(session.time_factor),
            "freq_factor": int(session.freq_factor),
            "mask_profile": mask_profile,
            "masked_channels": masked_channels,
        },
        "localization": None if localization is None else localization.to_dict(),
        "measurements": measurements,
        "snapshot": None if snapshot_path is None else str(snapshot_path),
        "provenance_warnings": session.provenance_warnings(),
    }
    return _jsonable(record)


def error_record(path: str | Path, exc: BaseException) -> dict[str, Any]:
    """The record for a burst that could not be processed."""
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "file": str(path),
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
        "flits_version": __version__,
        "dm": None,
        "telescope": None,
        "selection": None,
        "localization": None,
        "measurements": None,
        "snapshot": None,
        "provenance_warnings": [],
    }


CSV_COLUMNS = (
    "file",
    "status",
    "dm",
    "telescope",
    "event_start_ms",
    "event_end_ms",
    "band_low_mhz",
    "band_high_mhz",
    "masked_channels",
    "snr_peak",
    "snr_integrated",
    "peak_flux_jy",
    "peak_flux_jy_err",
    "fluence_jyms",
    "fluence_jyms_err",
    "fluence_uncertainty_class",
    "width_ms_acf",
    "width_ms_acf_err",
    "event_duration_ms",
    "spectral_extent_mhz",
    "spectral_width_mhz_acf",
    "toa_topo_mjd",
    "measurement_flags",
    "snapshot",
    "error",
    "schema_version",
    "id",
)


def csv_row(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten a record into the columns of :data:`CSV_COLUMNS`."""
    selection = record.get("selection") or {}
    measurements = record.get("measurements") or {}
    details = measurements.get("uncertainty_details") or {}

    def uncertainty(field: str) -> Any:
        entry = details.get(field)
        return entry.get("value") if isinstance(entry, dict) else None

    event = selection.get("event_ms") or [None, None]
    band = selection.get("band_mhz") or [None, None]
    fluence_detail = details.get("fluence_jyms")
    return {
        "file": record.get("file"),
        "status": record.get("status"),
        "dm": record.get("dm"),
        "telescope": record.get("telescope"),
        "event_start_ms": event[0],
        "event_end_ms": event[1],
        "band_low_mhz": band[0],
        "band_high_mhz": band[1],
        "masked_channels": selection.get("masked_channels"),
        "snr_peak": measurements.get("snr_peak"),
        "snr_integrated": measurements.get("snr_integrated"),
        "peak_flux_jy": measurements.get("peak_flux_jy"),
        "peak_flux_jy_err": uncertainty("peak_flux_jy"),
        "fluence_jyms": measurements.get("fluence_jyms"),
        "fluence_jyms_err": uncertainty("fluence_jyms"),
        "fluence_uncertainty_class": fluence_detail.get("classification") if isinstance(fluence_detail, dict) else None,
        "width_ms_acf": measurements.get("width_ms_acf"),
        "width_ms_acf_err": uncertainty("width_ms_acf"),
        "event_duration_ms": measurements.get("event_duration_ms"),
        "spectral_extent_mhz": measurements.get("spectral_extent_mhz"),
        "spectral_width_mhz_acf": measurements.get("spectral_width_mhz_acf"),
        "toa_topo_mjd": measurements.get("toa_topo_mjd"),
        "measurement_flags": ";".join(measurements.get("measurement_flags") or []),
        "snapshot": record.get("snapshot"),
        "error": record.get("error"),
        "schema_version": record.get("schema_version"),
        "id": record.get("id"),
    }


def format_records(records: Iterable[dict[str, Any]], output_format: str) -> str:
    """Render records as ``json`` (an array), ``jsonl`` (one per line) or ``csv``."""
    records = list(records)
    if output_format == "json":
        return json.dumps(records, indent=2, allow_nan=False) + "\n"
    if output_format == "jsonl":
        return "".join(json.dumps(record, allow_nan=False) + "\n" for record in records)
    if output_format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(csv_row(record))
        return buffer.getvalue()
    raise ValueError(f"Unknown output format: {output_format}")


def measure_bursts(
    paths: Sequence[str | Path],
    options: MeasureOptions,
    *,
    snapshot_dir: Path | None = None,
    overwrite: bool = False,
) -> Iterable[dict[str, Any]]:
    """Measure each burst in turn, yielding a record per file.

    A burst that fails yields an ``error`` record instead of stopping the rest.
    """
    taken: set[Path] = set()
    for path in paths:
        try:
            yield measure_burst(path, options, snapshot_dir=snapshot_dir, overwrite=overwrite, _taken=taken)
        except Exception as exc:
            yield error_record(path, exc)
