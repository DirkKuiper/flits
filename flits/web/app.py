from __future__ import annotations

import argparse
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

_SKIP_DIRS: frozenset[str] = frozenset(
    {"site-packages", "node_modules", "__pycache__", "dist", "build"}
)
_TRUSTED_LISTING_SUFFIXES: frozenset[str] = frozenset({".fil"})


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


class DetectFilterbankRequest(BaseModel):
    bfile: str


class ActionRequest(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ImportSessionRequest(BaseModel):
    snapshot: dict[str, Any]


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
