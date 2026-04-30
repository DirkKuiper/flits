from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from flits.io import inspect_filterbank, list_readers
from flits.session import BurstSession
from flits.settings import available_auto_mask_profiles, available_presets, get_preset


PACKAGE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = PACKAGE_DIR / "web_static"
SESSIONS: dict[str, BurstSession] = {}
SESSION_SNAPSHOT_PATHS: dict[str, Path] = {}

_SKIP_DIRS: frozenset[str] = frozenset(
    {"site-packages", "node_modules", "__pycache__", "dist", "build"}
)
_TRUSTED_LISTING_SUFFIXES: frozenset[str] = frozenset({".fil"})
_SESSION_SNAPSHOT_SUFFIX = "_flits_session.json"
_SESSION_SNAPSHOT_INDEX_VERSION = 1
_SESSION_SNAPSHOT_INDEX_PATH = Path(".flits") / "session_snapshot_index.json"


class CreateSessionRequest(BaseModel):
    bfile: str
    dm: float
    telescope: str | None = None
    sefd_jy: float | None = None
    sefd_fractional_uncertainty: float | None = None
    npol_override: int | None = None
    read_start_sec: float | None = None
    read_end_sec: float | None = None
    auto_mask_profile: str | None = "auto"
    distance_mpc: float | None = None
    distance_fractional_uncertainty: float | None = None
    redshift: float | None = None
    source_ra_deg: float | None = None
    source_dec_deg: float | None = None
    time_scale: str | None = None
    observatory_longitude_deg: float | None = None
    observatory_latitude_deg: float | None = None
    observatory_height_m: float | None = None


class DetectFilterbankRequest(BaseModel):
    bfile: str


class ActionRequest(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ImportSessionRequest(BaseModel):
    snapshot: dict[str, Any]


class SaveSessionSnapshotRequest(BaseModel):
    save_as: bool = False


app = FastAPI(title="FLITS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def data_dir() -> Path:
    configured = os.environ.get("FLITS_DATA_DIR")
    base = Path.cwd() if configured is None else Path(configured).expanduser()
    return base.resolve()


def resolve_burst_path(path_str: str) -> Path:
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = (data_dir() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Filterbank file not found: {path_str}")
    return candidate


def get_session(session_id: str) -> BurstSession:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session id")
    return session


def drop_session(session_id: str) -> BurstSession:
    session = SESSIONS.pop(session_id, None)
    SESSION_SNAPSHOT_PATHS.pop(session_id, None)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session id")
    return session


def _inspection_suggested_dm(inspection: object) -> float | None:
    schema_version = getattr(inspection, "schema_version", None)
    coherent_dm = getattr(inspection, "coherent_dm", None)
    if schema_version == "chime_frb_catalog_v1":
        return 0.0
    if schema_version == "chime_bbdata_beamformed_v1" and coherent_dm is not None:
        return float(coherent_dm)
    return None


def _inspection_dm_guidance(inspection: object) -> str | None:
    schema_version = getattr(inspection, "schema_version", None)
    coherent_dm = getattr(inspection, "coherent_dm", None)
    if schema_version == "chime_frb_catalog_v1":
        return "already dedispersed; use DM 0"
    if schema_version == "chime_bbdata_beamformed_v1" and coherent_dm is not None:
        return (
            f"coherently dedispersed at {float(coherent_dm):.6f}; "
            "FLITS applies residual DM relative to that value"
        )
    return None


def list_filterbank_files() -> list[str]:
    base = data_dir()
    suffixes = {ext.lower() for reader in list_readers() for ext in reader.extensions}
    found: set[str] = set()
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        rel = path.relative_to(base)
        # Skip hidden directories (.venv, .vendor, .git, etc.) and caches
        # that happen to live under the data dir when it points at a repo root.
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in _TRUSTED_LISTING_SUFFIXES:
            try:
                inspect_filterbank(str(path))
            except Exception:
                continue
        found.add(rel.as_posix())
    return sorted(found)


def list_filterbank_directories() -> list[dict[str, Any]]:
    grouped_files: dict[str, list[str]] = {}
    for relative_path in list_filterbank_files():
        parent = str(Path(relative_path).parent)
        directory = "" if parent == "." else parent.replace("\\", "/")
        grouped_files.setdefault(directory, []).append(relative_path)

    directories: list[dict[str, Any]] = []
    for directory in sorted(grouped_files, key=lambda value: (value != "", value)):
        files = grouped_files[directory]
        directories.append(
            {
                "path": directory,
                "label": "Data root" if directory == "" else directory,
                "file_count": len(files),
                "files": [{"path": file_path, "name": Path(file_path).name} for file_path in files],
            }
        )
    return directories


def _resolve_existing_or_candidate(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except OSError:
        return expanded


def _relative_to_data_dir(path: Path) -> str | None:
    try:
        return _resolve_existing_or_candidate(path).relative_to(data_dir()).as_posix()
    except ValueError:
        return None


def _snapshot_index_path() -> Path:
    return data_dir() / _SESSION_SNAPSHOT_INDEX_PATH


def _snapshot_cache_key(path: Path) -> str:
    return str(_resolve_existing_or_candidate(path))


def _snapshot_id(path: Path) -> str:
    digest = hashlib.sha256(_snapshot_cache_key(path).encode("utf-8")).hexdigest()
    return digest[:20]


def _utc_mtime_iso(mtime_unix: float) -> str:
    return datetime.fromtimestamp(float(mtime_unix), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_excerpt(value: object, *, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _source_candidates_from_snapshot_source(source: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []

    def add(candidate: Path) -> None:
        resolved = _resolve_existing_or_candidate(candidate)
        if resolved not in candidates:
            candidates.append(resolved)

    source_path_value = source.get("source_path")
    if source_path_value:
        source_path = Path(str(source_path_value)).expanduser()
        add(source_path if source_path.is_absolute() else data_dir() / source_path)

    relative_path = source.get("data_dir_relative_path")
    if relative_path:
        add(data_dir() / str(relative_path))

    return candidates


def _source_exists_for_snapshot(source: dict[str, Any]) -> bool:
    return any(candidate.exists() for candidate in _source_candidates_from_snapshot_source(source))


def _default_snapshot_path_for_source(source_path: Path) -> Path:
    resolved_source = _resolve_existing_or_candidate(source_path)
    return resolved_source.parent / "snapshots" / f"{resolved_source.stem}{_SESSION_SNAPSHOT_SUFFIX}"


def _default_snapshot_path_for_session(session: BurstSession) -> Path:
    source_path = Path(session.burst_file).expanduser()
    if not source_path.is_absolute():
        source_path = data_dir() / source_path
    return _default_snapshot_path_for_source(source_path)


def _timestamped_snapshot_path(default_path: Path) -> Path:
    default_name = default_path.name
    if default_name.endswith(_SESSION_SNAPSHOT_SUFFIX):
        source_stem = default_name[: -len(_SESSION_SNAPSHOT_SUFFIX)]
    else:
        source_stem = default_path.stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = default_path.with_name(f"{source_stem}_{stamp}{_SESSION_SNAPSHOT_SUFFIX}")
    counter = 2
    while candidate.exists():
        candidate = default_path.with_name(f"{source_stem}_{stamp}-{counter}{_SESSION_SNAPSHOT_SUFFIX}")
        counter += 1
    return candidate


def _load_snapshot_payload(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("source"), dict):
        raise ValueError("Not a FLITS session snapshot.")
    return payload


def _snapshot_summary(path: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot_path = _resolve_existing_or_candidate(path)
    stat = snapshot_path.stat()
    snapshot = _load_snapshot_payload(snapshot_path) if payload is None else payload
    source = snapshot.get("source") or {}
    event_bins = snapshot.get("event_bins") if isinstance(snapshot.get("event_bins"), list) else []
    tsamp_ms = float(source.get("tsamp") or 0.0) * 1000.0
    read_start_ms = float(snapshot.get("read_start_sec") or 0.0) * 1000.0
    event_ms: list[float] = []
    if len(event_bins) == 2 and tsamp_ms > 0:
        event_ms = [
            float(event_bins[0]) * tsamp_ms + read_start_ms,
            float(event_bins[1]) * tsamp_ms + read_start_ms,
        ]

    preset_key = str(snapshot.get("preset_key") or "generic")
    try:
        preset_label = get_preset(preset_key).label
    except ValueError:
        preset_label = preset_key

    source_path_value = source.get("source_path")
    source_path = str(source_path_value) if source_path_value else ""
    source_file = str(source.get("file_name") or Path(source_path).name or "")
    source_name = source.get("source_name") or None
    notes_excerpt = _compact_excerpt(snapshot.get("notes"))
    masked_channels = snapshot.get("masked_channels") if isinstance(snapshot.get("masked_channels"), list) else []

    return {
        "id": _snapshot_id(snapshot_path),
        "path": str(snapshot_path),
        "relative_path": _relative_to_data_dir(snapshot_path),
        "file_name": snapshot_path.name,
        "saved_mtime_unix": float(stat.st_mtime),
        "saved_at_utc": _utc_mtime_iso(float(stat.st_mtime)),
        "size_bytes": int(stat.st_size),
        "schema_version": str(snapshot.get("schema_version", "")),
        "source_name": source_name,
        "source_file": source_file,
        "source_path": source_path,
        "source_exists": _source_exists_for_snapshot(source),
        "preset_key": preset_key,
        "preset_label": preset_label,
        "dm": float(snapshot.get("dm") or 0.0),
        "event_ms": event_ms,
        "masked_channel_count": len(masked_channels),
        "notes_excerpt": notes_excerpt,
        "has_results": snapshot.get("results") is not None,
        "has_dm_optimization": snapshot.get("dm_optimization") is not None,
        "has_temporal_structure": snapshot.get("temporal_structure") is not None,
    }


def _read_snapshot_index_cache() -> dict[str, dict[str, Any]]:
    index_path = _snapshot_index_path()
    if not index_path.exists():
        return {}
    try:
        with index_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("version") != _SESSION_SNAPSHOT_INDEX_VERSION:
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for entry in payload.get("entries", []):
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        entries[str(entry["path"])] = entry
    return entries


def _write_snapshot_index_cache(entries: dict[str, dict[str, Any]]) -> None:
    index_path = _snapshot_index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    ordered_entries = sorted(
        entries.values(),
        key=lambda entry: float(entry.get("summary", {}).get("saved_mtime_unix", 0.0)),
        reverse=True,
    )
    payload = {"version": _SESSION_SNAPSHOT_INDEX_VERSION, "entries": ordered_entries}
    tmp_path = index_path.with_name(f".{index_path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp_path.replace(index_path)


def _discover_session_snapshot_files() -> list[Path]:
    base = data_dir()
    if not base.exists():
        return []
    found: list[Path] = []
    for path in base.rglob(f"*{_SESSION_SNAPSHOT_SUFFIX}"):
        if not path.is_file() or path.parent.name != "snapshots":
            continue
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel.parts):
            continue
        found.append(path)
    return sorted(found)


def _refresh_session_snapshot_index(*, force: bool = False) -> dict[str, Any]:
    cached = _read_snapshot_index_cache()
    entries: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for path in _discover_session_snapshot_files():
        key = _snapshot_cache_key(path)
        try:
            stat = path.stat()
            cached_entry = cached.get(key)
            if (
                not force
                and cached_entry is not None
                and int(cached_entry.get("size_bytes", -1)) == int(stat.st_size)
                and float(cached_entry.get("mtime_unix", -1.0)) == float(stat.st_mtime)
            ):
                entries[key] = cached_entry
                continue
            entries[key] = {
                "path": key,
                "size_bytes": int(stat.st_size),
                "mtime_unix": float(stat.st_mtime),
                "summary": _snapshot_summary(path),
            }
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})

    _write_snapshot_index_cache(entries)
    summaries = [entry["summary"] for entry in entries.values()]
    summaries.sort(key=lambda item: float(item.get("saved_mtime_unix", 0.0)), reverse=True)
    return {
        "library_root": str(data_dir()),
        "index_path": str(_snapshot_index_path()),
        "sessions": summaries,
        "errors": errors,
    }


def _session_snapshot_path_by_id(snapshot_id: str) -> Path:
    library = _refresh_session_snapshot_index(force=False)
    for item in library["sessions"]:
        if item.get("id") == snapshot_id:
            return Path(str(item["path"]))
    raise HTTPException(status_code=404, detail="Unknown session snapshot id")


def _snapshot_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write_snapshot_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/files")
def files() -> dict[str, Any]:
    return {
        "files": list_filterbank_files(),
        "directories": list_filterbank_directories(),
    }


@app.get("/api/session-snapshots")
def session_snapshots() -> dict[str, Any]:
    return _refresh_session_snapshot_index(force=False)


@app.post("/api/session-snapshots/refresh")
def refresh_session_snapshots() -> dict[str, Any]:
    return _refresh_session_snapshot_index(force=True)


@app.post("/api/session-snapshots/{snapshot_id}/open")
def open_session_snapshot(snapshot_id: str) -> dict[str, Any]:
    snapshot_path = _session_snapshot_path_by_id(snapshot_id)
    try:
        snapshot = _load_snapshot_payload(snapshot_path)
        session = BurstSession.from_snapshot(snapshot)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = uuid4().hex
    SESSIONS[session_id] = session
    SESSION_SNAPSHOT_PATHS[session_id] = _resolve_existing_or_candidate(snapshot_path)
    return {
        "session_id": session_id,
        "view": session.get_view(),
        "snapshot": _snapshot_summary(snapshot_path, snapshot),
    }


@app.get("/api/presets")
def presets() -> dict[str, list[dict[str, Any]]]:
    return {"presets": [preset.to_dict() for preset in available_presets()]}


@app.get("/api/auto-mask-profiles")
def auto_mask_profiles() -> dict[str, list[dict[str, Any]]]:
    return {"profiles": [profile.to_dict() for profile in available_auto_mask_profiles()]}


@app.post("/api/detect")
def detect_filterbank(request: DetectFilterbankRequest) -> dict[str, Any]:
    burst_path = resolve_burst_path(request.bfile)
    try:
        inspection = inspect_filterbank(str(burst_path))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detected_preset = get_preset(inspection.detected_preset_key)
    suggested_dm = _inspection_suggested_dm(inspection)
    dm_guidance = _inspection_dm_guidance(inspection)
    return {
        "bfile": str(burst_path),
        "source_name": inspection.source_name,
        "telescope_id": inspection.telescope_id,
        "machine_id": inspection.machine_id,
        "telescope_name": inspection.telescope_name,
        "schema_version": inspection.schema_version,
        "coherent_dm": inspection.coherent_dm,
        "source_ra_deg": inspection.source_ra_deg,
        "source_dec_deg": inspection.source_dec_deg,
        "source_position_basis": inspection.source_position_basis,
        "time_scale": inspection.time_scale,
        "time_reference_frame": inspection.time_reference_frame,
        "barycentric_header_flag": inspection.barycentric_header_flag,
        "pulsarcentric_header_flag": inspection.pulsarcentric_header_flag,
        "suggested_dm": suggested_dm,
        "dm_guidance": dm_guidance,
        "detected_preset_key": inspection.detected_preset_key,
        "detected_preset_label": detected_preset.label,
        "detection_basis": inspection.detection_basis,
    }


@app.post("/api/sessions")
def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    burst_path = resolve_burst_path(request.bfile)
    session = BurstSession.from_file(
        str(burst_path),
        dm=request.dm,
        telescope=request.telescope,
        sefd_jy=request.sefd_jy,
        npol_override=request.npol_override,
        read_start_sec=request.read_start_sec,
        read_end_sec=request.read_end_sec,
        auto_mask_profile=request.auto_mask_profile,
        distance_mpc=request.distance_mpc,
        sefd_fractional_uncertainty=request.sefd_fractional_uncertainty,
        distance_fractional_uncertainty=request.distance_fractional_uncertainty,
        redshift=request.redshift,
        source_ra_deg=request.source_ra_deg,
        source_dec_deg=request.source_dec_deg,
        time_scale=request.time_scale,
        observatory_longitude_deg=request.observatory_longitude_deg,
        observatory_latitude_deg=request.observatory_latitude_deg,
        observatory_height_m=request.observatory_height_m,
    )
    session_id = uuid4().hex
    SESSIONS[session_id] = session
    return {"session_id": session_id, "view": session.get_view()}


@app.get("/api/sessions/{session_id}")
def session_view(session_id: str) -> dict[str, Any]:
    session = get_session(session_id)
    return {"session_id": session_id, "view": session.get_view()}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, str]:
    drop_session(session_id)
    return {"status": "deleted"}


@app.get("/api/sessions/{session_id}/snapshot")
def session_snapshot(session_id: str) -> Response:
    session = get_session(session_id)
    snapshot_bytes = (json.dumps(session.snapshot_dict(), indent=2, allow_nan=False) + "\n").encode("utf-8")
    snapshot_name = f"{Path(session.burst_file).stem}_flits_session.json"
    return Response(
        content=snapshot_bytes,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{snapshot_name}"'},
    )


@app.post("/api/sessions/{session_id}/snapshot/save")
def save_session_snapshot(session_id: str, request: SaveSessionSnapshotRequest) -> dict[str, Any]:
    session = get_session(session_id)
    snapshot_payload = session.snapshot_dict()
    snapshot_content = _snapshot_bytes(snapshot_payload)

    if request.save_as:
        target_path = _timestamped_snapshot_path(_default_snapshot_path_for_session(session))
    else:
        target_path = SESSION_SNAPSHOT_PATHS.get(session_id) or _default_snapshot_path_for_session(session)

    _write_snapshot_file(target_path, snapshot_content)
    target_path = _resolve_existing_or_candidate(target_path)
    SESSION_SNAPSHOT_PATHS[session_id] = target_path
    library = _refresh_session_snapshot_index(force=False)
    return {
        "status": "saved",
        "path": str(target_path),
        "snapshot": _snapshot_summary(target_path, snapshot_payload),
        "library": library,
    }


@app.post("/api/sessions/import")
def import_session(request: ImportSessionRequest) -> dict[str, Any]:
    try:
        session = BurstSession.from_snapshot(request.snapshot)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = uuid4().hex
    SESSIONS[session_id] = session
    return {"session_id": session_id, "view": session.get_view()}


@app.get("/api/sessions/{session_id}/exports/{export_id}")
def session_export_manifest(session_id: str, export_id: str) -> dict[str, Any]:
    session = get_session(session_id)
    try:
        manifest = session.get_export_manifest(export_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown export id") from exc
    return manifest.to_dict()


@app.get("/api/sessions/{session_id}/exports/{export_id}/{artifact_name}")
def session_export_artifact(session_id: str, export_id: str, artifact_name: str) -> Response:
    session = get_session(session_id)
    try:
        artifact, content = session.get_export_artifact(export_id, artifact_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown export artifact") from exc
    return Response(
        content=content,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.name}"'},
    )


@app.post("/api/sessions/{session_id}/actions")
def session_action(session_id: str, request: ActionRequest) -> dict[str, Any]:
    session = get_session(session_id)
    action = request.type
    payload = request.payload
    export_manifest: dict[str, Any] | None = None
    export_preview: dict[str, Any] | None = None

    try:
        if action == "time_factor":
            session.set_time_factor(int(payload["value"]))
        elif action == "freq_factor":
            session.set_freq_factor(int(payload["value"]))
        elif action == "reset_view":
            session.reset_view()
        elif action == "set_crop":
            session.set_crop_ms(float(payload["start_ms"]), float(payload["end_ms"]))
        elif action == "set_event":
            session.set_event_ms(float(payload["start_ms"]), float(payload["end_ms"]))
        elif action == "add_region":
            session.add_region_ms(float(payload["start_ms"]), float(payload["end_ms"]))
        elif action == "clear_regions":
            session.clear_regions()
        elif action == "add_offpulse":
            session.add_offpulse_ms(float(payload["start_ms"]), float(payload["end_ms"]))
        elif action == "clear_offpulse":
            session.clear_offpulse()
        elif action == "add_peak":
            session.add_peak_ms(float(payload["time_ms"]))
        elif action == "remove_peak":
            session.remove_peak_ms(float(payload["time_ms"]))
        elif action == "mask_channel":
            session.mask_channel_freq(float(payload["freq_mhz"]))
        elif action == "mask_range":
            session.mask_range_freq(float(payload["start_freq_mhz"]), float(payload["end_freq_mhz"]))
        elif action == "undo_mask":
            session.undo_mask()
        elif action == "reset_mask":
            session.reset_mask()
        elif action == "set_spectral_extent":
            session.set_spectral_extent_freq(float(payload["start_freq_mhz"]), float(payload["end_freq_mhz"]))
        elif action == "auto_mask_jess":
            session.auto_mask_jess(payload.get("profile"))
        elif action == "set_dm":
            session.set_dm(float(payload["dm"]))
        elif action == "optimize_dm":
            session.optimize_dm(
                float(payload["center_dm"]),
                float(payload["half_range"]),
                float(payload["step"]),
                metric=str(payload.get("metric", "integrated_event_snr")),
            )
        elif action == "compute_widths":
            session.compute_widths()
        elif action == "accept_width_result":
            session.accept_width_result(str(payload["method"]))
        elif action == "compute_properties":
            session.compute_properties()
        elif action == "fit_scattering":
            session.fit_scattering(payload)
        elif action == "run_temporal_structure_analysis":
            session.run_temporal_structure_analysis(
                segment_length_ms=float(payload["segment_length_ms"]),
            )
        elif action == "run_spectral_analysis":
            session.run_spectral_analysis(
                segment_length_ms=float(payload["segment_length_ms"]),
            )
        elif action == "set_notes":
            session.set_notes(payload.get("notes"))
        elif action == "set_timing_metadata":
            session.set_timing_metadata(
                source_ra_deg=payload.get("source_ra_deg", session.config.source_ra_deg),
                source_dec_deg=payload.get("source_dec_deg", session.config.source_dec_deg),
                time_scale=payload.get("time_scale", session.config.time_scale),
                observatory_longitude_deg=payload.get(
                    "observatory_longitude_deg",
                    session.config.observatory_longitude_deg,
                ),
                observatory_latitude_deg=payload.get(
                    "observatory_latitude_deg",
                    session.config.observatory_latitude_deg,
                ),
                observatory_height_m=payload.get(
                    "observatory_height_m",
                    session.config.observatory_height_m,
                ),
            )
        elif action == "preview_export_results":
            preview = session.preview_export_results(
                include=payload.get("include"),
                plot_formats=payload.get("plot_formats"),
                window_formats=payload.get("window_formats"),
                window_resolutions=payload.get("window_resolutions"),
            )
            export_preview = preview.to_dict()
        elif action == "export_results":
            manifest = session.export_results(
                session_id=session_id,
                include=payload.get("include"),
                plot_formats=payload.get("plot_formats"),
                window_formats=payload.get("window_formats"),
                window_resolutions=payload.get("window_resolutions"),
            )
            export_manifest = manifest.to_dict()
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "session_id": session_id,
        "view": session.get_view(),
        "export_manifest": export_manifest,
        "export_preview": export_preview,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FLITS.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory used for relative filterbank paths and known-file discovery.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.data_dir is not None:
        os.environ["FLITS_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    uvicorn.run("flits.web.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
