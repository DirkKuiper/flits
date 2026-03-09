from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from burst_analyzer.session import BurstSession
from burst_analyzer.settings import available_presets


ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT_DIR / "web_static"
SESSIONS: dict[str, BurstSession] = {}


class CreateSessionRequest(BaseModel):
    bfile: str
    dm: float
    telescope: str = "generic"
    sefd_jy: float | None = None
    read_start_sec: float | None = None
    initial_crop_sec: float | None = None
    distance_mpc: float | None = None
    redshift: float | None = None


class ActionRequest(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Burst Analyzer Web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def resolve_burst_path(path_str: str) -> Path:
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT_DIR / candidate).resolve()
    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"Filterbank file not found: {path_str}")
    return candidate


def get_session(session_id: str) -> BurstSession:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session id")
    return session


def list_filterbank_files() -> list[str]:
    return sorted(path.name for path in ROOT_DIR.glob("*.fil"))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/files")
def files() -> dict[str, list[str]]:
    return {"files": list_filterbank_files()}


@app.get("/api/presets")
def presets() -> dict[str, list[dict[str, Any]]]:
    return {"presets": [preset.to_dict() for preset in available_presets()]}


@app.post("/api/sessions")
def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    burst_path = resolve_burst_path(request.bfile)
    session = BurstSession.from_file(
        str(burst_path),
        dm=request.dm,
        telescope=request.telescope,
        sefd_jy=request.sefd_jy,
        read_start_sec=request.read_start_sec,
        initial_crop_sec=request.initial_crop_sec,
        distance_mpc=request.distance_mpc,
        redshift=request.redshift,
    )
    session_id = uuid4().hex
    SESSIONS[session_id] = session
    return {"session_id": session_id, "view": session.get_view()}


@app.get("/api/sessions/{session_id}")
def session_view(session_id: str) -> dict[str, Any]:
    session = get_session(session_id)
    return {"session_id": session_id, "view": session.get_view()}


@app.post("/api/sessions/{session_id}/actions")
def session_action(session_id: str, request: ActionRequest) -> dict[str, Any]:
    session = get_session(session_id)
    action = request.type
    payload = request.payload

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
            session.auto_mask_jess()
        elif action == "set_dm":
            session.set_dm(float(payload["dm"]))
        elif action == "compute_properties":
            session.compute_properties()
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"session_id": session_id, "view": session.get_view()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the web burst analyzer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("burst_analyzer.web.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
