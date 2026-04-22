from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence
from uuid import uuid4

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from flits import __version__
from flits.analysis.dm_optimization import dm_metric_definition
from flits.io.sigproc import SigprocFilterbankHeader, build_sigproc_filterbank_bytes
from flits.measurements import _acf_width
from flits.models import (
    ExportArtifact,
    ExportManifest,
    ExportPlotPreview,
    ExportPreview,
    ExportPreviewArtifact,
)


if TYPE_CHECKING:
    from flits.session import BurstSession


EXPORT_SCHEMA_VERSION = "1.5"
DEFAULT_EXPORT_INCLUDE = ("json", "csv", "npz", "plots")
DEFAULT_PLOT_FORMATS = ("png", "svg")
DEFAULT_WINDOW_FORMATS = ("npz",)
DEFAULT_WINDOW_RESOLUTIONS = ("native",)
MAX_EXPORT_SNAPSHOTS = 3

_VALID_INCLUDE = frozenset((*DEFAULT_EXPORT_INCLUDE, "window"))
_VALID_PLOT_FORMATS = frozenset(DEFAULT_PLOT_FORMATS)
_VALID_WINDOW_FORMATS = frozenset(("fil", "npz"))
_VALID_WINDOW_RESOLUTIONS = frozenset(("native", "view"))

ASTROFLASH_COLORS = {
    "ink": "#323232",
    "muted": "#6A6A6A",
    "border": "#d9d9d9",
    "accent": "#7235a2",
    "accent_strong": "#5f2b88",
    "accent_alt": "#327cbc",
    "warning": "#8e6ebd",
    "alert": "#a23b61",
    "noise": "#88d8dd",
    "crossover": "#e2a144",
    "neutral": "#7d8290",
    "grid": "#e9e9ee",
}
ASTROFLASH_HEATMAP_CMAP = LinearSegmentedColormap.from_list(
    "astroflash_heatmap",
    ["#191919", "#2e2e2e", "#4d326e", "#7235a2", "#b287d0", "#f3eafb"],
)


@dataclass(frozen=True)
class StoredExportSnapshot:
    manifest: ExportManifest
    contents: dict[str, bytes]


@dataclass(frozen=True)
class ExportSnapshotData:
    export_id: str
    bundle_name: str
    created_at_utc: str
    meta: dict[str, Any]
    state: dict[str, Any]
    results: dict[str, Any] | None
    width_analysis: dict[str, Any] | None
    dm_optimization: dict[str, Any] | None
    temporal_structure: dict[str, Any] | None
    dynamic_spectrum: np.ndarray
    time_axis_ms: np.ndarray
    freq_axis_mhz: np.ndarray
    burst_only_profile_sn: np.ndarray
    time_profile_sn: np.ndarray
    event_profile_sn: np.ndarray
    spectrum_sn: np.ndarray
    temporal_acf: np.ndarray
    temporal_acf_lags_ms: np.ndarray
    spectral_acf: np.ndarray
    spectral_acf_lags_mhz: np.ndarray
    spectral_axis_mhz: np.ndarray
    event_window_ms: tuple[float, float]
    spectral_extent_mhz: tuple[float, float]
    crop_start_bin: int
    crop_end_bin: int
    event_start_rel_bin: int
    event_end_rel_bin: int
    selected_channel_start: int
    selected_channel_end: int
    masked_channels: np.ndarray
    peak_positions_ms: np.ndarray


@dataclass(frozen=True)
class ExportSelection:
    include: tuple[str, ...]
    plot_formats: tuple[str, ...]
    window_formats: tuple[str, ...]
    window_resolutions: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "include": list(self.include),
            "plot_formats": list(self.plot_formats),
            "window_formats": list(self.window_formats),
            "window_resolutions": list(self.window_resolutions),
        }


@dataclass(frozen=True)
class PlannedArtifact:
    key: str
    label: str
    kind: str
    content_type: str
    status: str
    reason: str | None
    filename_suffix: str
    build_target: str
    format: str | None = None
    plot_key: str | None = None
    window_mode: str | None = None

    def materialized_name(self, bundle_name: str) -> str:
        return f"{bundle_name}_{self.filename_suffix}"


@dataclass(frozen=True)
class PlotPlan:
    key: str
    title: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class WindowExportData:
    mode: str
    dynamic_spectrum: np.ndarray
    time_axis_ms: np.ndarray
    freq_axis_mhz: np.ndarray
    time_start_bin: int
    time_end_bin: int
    crop_start_bin: int
    crop_end_bin: int
    event_start_rel_bin: int
    event_end_rel_bin: int
    spectral_extent_channels: tuple[int, int]
    spectral_extent_mhz: tuple[float, float]
    masked_channels: np.ndarray
    time_factor: int
    freq_factor: int
    effective_tsamp_sec: float
    effective_freqres_mhz: float
    frequency_step_mhz: float


def preview_export(
    session: "BurstSession",
    *,
    include: Sequence[str] | None = None,
    plot_formats: Sequence[str] | None = None,
    window_formats: Sequence[str] | None = None,
    window_resolutions: Sequence[str] | None = None,
) -> ExportPreview:
    selection = _normalize_selection(
        include=include,
        plot_formats=plot_formats,
        window_formats=window_formats,
        window_resolutions=window_resolutions,
    )
    snapshot = _build_snapshot_data(session)
    plan = _plan_export(snapshot, selection)
    plot_previews = _build_plot_previews(snapshot, plan)
    return ExportPreview(
        selection=selection.to_dict(),
        artifacts=[
            ExportPreviewArtifact(
                label=artifact.label,
                kind=artifact.kind,
                content_type=artifact.content_type,
                format=artifact.format,
                status=artifact.status,
                reason=artifact.reason,
            )
            for artifact in plan
        ],
        plot_previews=plot_previews,
        generated_at_utc=snapshot.created_at_utc,
    )


def create_export_snapshot(
    session: "BurstSession",
    *,
    session_id: str,
    include: Sequence[str] | None = None,
    plot_formats: Sequence[str] | None = None,
    window_formats: Sequence[str] | None = None,
    window_resolutions: Sequence[str] | None = None,
) -> StoredExportSnapshot:
    selection = _normalize_selection(
        include=include,
        plot_formats=plot_formats,
        window_formats=window_formats,
        window_resolutions=window_resolutions,
    )
    snapshot = _build_snapshot_data(session)
    window_exports = _build_window_exports(session, selection)
    window_metadata = {
        mode: _build_window_metadata_payload(snapshot, window)
        for mode, window in window_exports.items()
    }
    plan = _plan_export(snapshot, selection)
    plot_figure_cache = _build_plot_figure_cache(snapshot, plan)

    specs: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}

    for artifact in plan:
        if artifact.build_target == "json":
            continue

        artifact_name = artifact.materialized_name(snapshot.bundle_name)
        if artifact.status != "ready":
            specs.append(
                _omitted_artifact_spec(
                    name=artifact_name,
                    kind=artifact.kind,
                    content_type=artifact.content_type,
                    reason=artifact.reason or "plot_unavailable",
                )
            )
            continue

        content = _materialize_artifact(
            snapshot,
            artifact,
            plot_figure_cache,
            window_exports=window_exports,
            window_metadata=window_metadata,
        )
        specs.append(
            _ready_artifact_spec(
                session_id=session_id,
                export_id=snapshot.export_id,
                name=artifact_name,
                kind=artifact.kind,
                content_type=artifact.content_type,
                content=content,
            )
        )
        contents[artifact_name] = content

    json_plan = next((artifact for artifact in plan if artifact.build_target == "json"), None)
    json_bytes = b""
    json_size: int | None = None
    if json_plan is not None:
        json_name = json_plan.materialized_name(snapshot.bundle_name)
        for _ in range(3):
            manifest = ExportManifest(
                export_id=snapshot.export_id,
                bundle_name=snapshot.bundle_name,
                schema_version=EXPORT_SCHEMA_VERSION,
                created_at_utc=snapshot.created_at_utc,
                artifacts=_artifact_objects(
                    session_id=session_id,
                    export_id=snapshot.export_id,
                    specs=specs,
                    json_name=json_name,
                    json_size=json_size,
                ),
            )
            json_bytes = _build_science_json(snapshot, manifest)
            new_size = len(json_bytes)
            if new_size == json_size:
                break
            json_size = new_size

        specs.insert(
            0,
            _ready_artifact_spec(
                session_id=session_id,
                export_id=snapshot.export_id,
                name=json_name,
                kind=json_plan.kind,
                content_type=json_plan.content_type,
                content=json_bytes,
                size_override=json_size,
            ),
        )
        contents[json_name] = json_bytes

    for figure in plot_figure_cache.values():
        plt.close(figure)

    manifest = ExportManifest(
        export_id=snapshot.export_id,
        bundle_name=snapshot.bundle_name,
        schema_version=EXPORT_SCHEMA_VERSION,
        created_at_utc=snapshot.created_at_utc,
        artifacts=_artifact_objects(
            session_id=session_id,
            export_id=snapshot.export_id,
            specs=specs,
        ),
    )
    return StoredExportSnapshot(manifest=manifest, contents=contents)


def _build_snapshot_data(session: "BurstSession") -> ExportSnapshotData:
    export_id = uuid4().hex
    created = datetime.now(timezone.utc)
    created_at_utc = created.isoformat(timespec="seconds").replace("+00:00", "Z")
    bundle_name = f"{_slugify(Path(session.burst_file).stem)}_{created.strftime('%Y%m%dT%H%M%SZ')}_{export_id[:8]}"

    view = session.get_view()
    export_meta = dict(view["meta"])
    export_meta["start_mjd"] = float(session.start_mjd)
    export_meta["read_start_sec"] = float(session.plus_mjd_sec)
    grid, context = session._build_measurement_context_for_data()
    event_start_ms, event_end_ms = _event_window_ms(
        context.time_axis_ms,
        grid.effective_tsamp_ms,
        context.event_rel_start,
        context.event_rel_end,
    )
    spectral_extent = (
        tuple(sorted((float(np.min(context.spectral_axis_mhz)), float(np.max(context.spectral_axis_mhz)))))
        if context.spectral_axis_mhz.size
        else (0.0, 0.0)
    )
    peak_positions = np.asarray(
        [
            float(grid.time_axis_ms[peak])
            for peak in grid.peak_bins
            if 0 <= int(peak) < grid.time_axis_ms.size
        ],
        dtype=float,
    )
    temporal_acf_lags_ms, temporal_acf = _acf_width(context.event_profile_sn, grid.effective_tsamp_ms)[1:]
    spectral_acf_lags_mhz, spectral_acf = _acf_width(context.spectrum_sn, grid.effective_freqres_mhz)[1:]

    return ExportSnapshotData(
        export_id=export_id,
        bundle_name=bundle_name,
        created_at_utc=created_at_utc,
        meta=export_meta,
        state=view["state"],
        results=session.results.to_dict() if session.results is not None else None,
        width_analysis=session.width_analysis.to_dict() if session.width_analysis is not None else None,
        dm_optimization=session.dm_optimization.to_dict() if session.dm_optimization is not None else None,
        temporal_structure=(
            session.temporal_structure.to_dict() if session.temporal_structure is not None else None
        ),
        dynamic_spectrum=np.asarray(grid.masked, dtype=float),
        time_axis_ms=np.asarray(context.time_axis_ms, dtype=float),
        freq_axis_mhz=np.asarray(grid.freqs_mhz, dtype=float),
        burst_only_profile_sn=np.asarray(context.selected_profile_sn, dtype=float),
        time_profile_sn=np.asarray(context.time_profile_sn, dtype=float),
        event_profile_sn=np.asarray(context.event_profile_sn, dtype=float),
        spectrum_sn=np.asarray(context.spectrum_sn, dtype=float),
        temporal_acf=np.asarray(temporal_acf, dtype=float),
        temporal_acf_lags_ms=np.asarray(temporal_acf_lags_ms, dtype=float),
        spectral_acf=np.asarray(spectral_acf, dtype=float),
        spectral_acf_lags_mhz=np.asarray(spectral_acf_lags_mhz, dtype=float),
        spectral_axis_mhz=np.asarray(context.spectral_axis_mhz, dtype=float),
        event_window_ms=(event_start_ms, event_end_ms),
        spectral_extent_mhz=spectral_extent,
        crop_start_bin=0,
        crop_end_bin=int(grid.masked.shape[1]),
        event_start_rel_bin=int(context.event_rel_start),
        event_end_rel_bin=int(context.event_rel_end),
        selected_channel_start=int(context.spec_lo),
        selected_channel_end=int(context.spec_hi),
        masked_channels=np.asarray(view["state"]["masked_channels"], dtype=int),
        peak_positions_ms=peak_positions,
    )


def _window_time_bounds(session: "BurstSession") -> tuple[int, int]:
    event_width = max(1, int(session.event_end) - int(session.event_start))
    start = max(int(session.crop_start), int(session.event_start) - event_width)
    end = min(int(session.crop_end), int(session.event_end) + event_width)
    if end <= start:
        end = min(int(session.crop_end), start + max(1, event_width))
    return int(start), int(end)


def _build_window_exports(
    session: "BurstSession",
    selection: ExportSelection,
) -> dict[str, WindowExportData]:
    if "window" not in selection.include or not selection.window_resolutions or not selection.window_formats:
        return {}

    spec_lo_abs, spec_hi_abs = session._selected_channel_bounds()
    selected_masked_channels = np.flatnonzero(session.channel_mask[spec_lo_abs : spec_hi_abs + 1]).astype(int) + spec_lo_abs
    time_start_abs, time_end_abs = _window_time_bounds(session)
    exports: dict[str, WindowExportData] = {}
    native_freq_sign = -1.0 if session.freqs.size > 1 and float(session.freqs[1] - session.freqs[0]) < 0 else 1.0

    for mode in selection.window_resolutions:
        if mode == "native":
            masked_crop = session.get_masked_crop()
            time_lo_rel = max(0, int(time_start_abs) - int(session.crop_start))
            time_hi_rel = min(masked_crop.shape[1], int(time_end_abs) - int(session.crop_start))
            dynamic = np.asarray(
                masked_crop[spec_lo_abs : spec_hi_abs + 1, time_lo_rel:time_hi_rel],
                dtype=float,
            )
            time_axis_ms = session._bins_to_ms_array(np.arange(time_start_abs, time_end_abs, dtype=float))
            freq_axis_mhz = np.asarray(session.freqs[spec_lo_abs : spec_hi_abs + 1], dtype=float)
            event_rel_start = max(0, min(dynamic.shape[1], int(session.event_start) - int(time_start_abs)))
            event_rel_end = max(event_rel_start + 1, min(dynamic.shape[1], int(session.event_end) - int(time_start_abs)))
            exports[mode] = WindowExportData(
                mode=mode,
                dynamic_spectrum=dynamic,
                time_axis_ms=np.asarray(time_axis_ms, dtype=float),
                freq_axis_mhz=freq_axis_mhz,
                time_start_bin=int(time_start_abs),
                time_end_bin=int(time_end_abs),
                crop_start_bin=int(session.crop_start),
                crop_end_bin=int(session.crop_end),
                event_start_rel_bin=int(event_rel_start),
                event_end_rel_bin=int(event_rel_end),
                spectral_extent_channels=(int(spec_lo_abs), int(spec_hi_abs)),
                spectral_extent_mhz=tuple(sorted((float(freq_axis_mhz.min()), float(freq_axis_mhz.max())))),
                masked_channels=np.asarray(selected_masked_channels, dtype=int),
                time_factor=1,
                freq_factor=1,
                effective_tsamp_sec=float(session.tsamp),
                effective_freqres_mhz=float(abs(session.freqres)),
                frequency_step_mhz=(
                    float(np.nanmedian(np.diff(freq_axis_mhz)))
                    if freq_axis_mhz.size > 1
                    else float(native_freq_sign * abs(session.freqres))
                ),
            )
            continue

        grid = session._reduced_analysis_grid()
        time_bounds = session._reduce_interval(
            time_start_abs,
            time_end_abs,
            base=int(session.crop_start),
            factor=int(session.time_factor),
            max_bins=int(grid.masked.shape[1]),
            require_nonempty=True,
        )
        spec_bounds = session._reduce_interval(
            spec_lo_abs,
            spec_hi_abs + 1,
            base=0,
            factor=int(session.freq_factor),
            max_bins=int(grid.masked.shape[0]),
            require_nonempty=True,
            )
        event_bounds = session._reduce_interval(
            int(session.event_start),
            int(session.event_end),
            base=int(session.crop_start),
            factor=int(session.time_factor),
            max_bins=int(grid.masked.shape[1]),
            require_nonempty=True,
        )
        assert time_bounds is not None
        assert spec_bounds is not None
        assert event_bounds is not None
        time_lo_red, time_hi_red = time_bounds
        spec_lo_red, spec_hi_red_exclusive = spec_bounds
        event_lo_red, event_hi_red = event_bounds
        dynamic = np.asarray(
            grid.masked[spec_lo_red:spec_hi_red_exclusive, time_lo_red:time_hi_red],
            dtype=float,
        )
        time_axis_ms = np.asarray(grid.time_axis_ms[time_lo_red:time_hi_red], dtype=float)
        freq_axis_mhz = np.asarray(grid.freqs_mhz[spec_lo_red:spec_hi_red_exclusive], dtype=float)
        exports[mode] = WindowExportData(
            mode=mode,
            dynamic_spectrum=dynamic,
            time_axis_ms=time_axis_ms,
            freq_axis_mhz=freq_axis_mhz,
            time_start_bin=int(session.crop_start + (time_lo_red * session.time_factor)),
            time_end_bin=int(min(session.crop_end, session.crop_start + (time_hi_red * session.time_factor))),
            crop_start_bin=int(session.crop_start),
            crop_end_bin=int(session.crop_end),
            event_start_rel_bin=max(0, int(event_lo_red - time_lo_red)),
            event_end_rel_bin=max(1, int(event_hi_red - time_lo_red)),
            spectral_extent_channels=(int(spec_lo_abs), int(spec_hi_abs)),
            spectral_extent_mhz=tuple(sorted((float(freq_axis_mhz.min()), float(freq_axis_mhz.max())))),
            masked_channels=np.asarray(selected_masked_channels, dtype=int),
            time_factor=int(session.time_factor),
            freq_factor=int(session.freq_factor),
            effective_tsamp_sec=float(grid.effective_tsamp_ms / 1000.0),
            effective_freqres_mhz=float(grid.effective_freqres_mhz),
            frequency_step_mhz=(
                float(np.nanmedian(np.diff(freq_axis_mhz)))
                if freq_axis_mhz.size > 1
                else float(native_freq_sign * grid.effective_freqres_mhz)
            ),
        )

    return exports


def _build_window_metadata_payload(snapshot: ExportSnapshotData, window: WindowExportData) -> dict[str, Any]:
    event_start_ms, event_end_ms = _event_window_ms(
        window.time_axis_ms,
        float(window.effective_tsamp_sec * 1000.0),
        int(window.event_start_rel_bin),
        int(window.event_end_rel_bin),
    )
    window_end_ms = (
        float(window.time_axis_ms[-1] + (window.effective_tsamp_sec * 1000.0))
        if window.time_axis_ms.size
        else float(snapshot.state.get("event_ms", [0.0, 0.0])[1])
    )
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "flits_version": __version__,
        "export_id": snapshot.export_id,
        "bundle_name": snapshot.bundle_name,
        "exported_at_utc": snapshot.created_at_utc,
        "type": "window_metadata",
        "data_semantics": "dedispersed_normalized_stokes_i",
        "resolution_mode": window.mode,
        "time_factor": int(window.time_factor),
        "freq_factor": int(window.freq_factor),
        "effective_tsamp_sec": float(window.effective_tsamp_sec),
        "effective_freqres_mhz": float(window.effective_freqres_mhz),
        "frequency_step_mhz": float(window.frequency_step_mhz),
        "meta": snapshot.meta,
        "state": snapshot.state,
        "window": {
            "policy": "event_plus_one_event_width_clipped_to_crop",
            "time_bins": [int(window.time_start_bin), int(window.time_end_bin)],
            "time_ms": [
                float(window.time_axis_ms[0]) if window.time_axis_ms.size else event_start_ms,
                window_end_ms,
            ],
            "event_window_bins": [int(window.event_start_rel_bin), int(window.event_end_rel_bin)],
            "event_window_ms": [float(event_start_ms), float(event_end_ms)],
            "crop_bins": [int(window.crop_start_bin), int(window.crop_end_bin)],
            "spectral_extent_channels": [int(window.spectral_extent_channels[0]), int(window.spectral_extent_channels[1])],
            "spectral_extent_mhz": [float(window.spectral_extent_mhz[0]), float(window.spectral_extent_mhz[1])],
            "masked_channels": [int(value) for value in np.asarray(window.masked_channels, dtype=int)],
            "shape": [int(window.dynamic_spectrum.shape[0]), int(window.dynamic_spectrum.shape[1])],
        },
    }


def _normalize_selection(
    *,
    include: Sequence[str] | None,
    plot_formats: Sequence[str] | None,
    window_formats: Sequence[str] | None,
    window_resolutions: Sequence[str] | None,
) -> ExportSelection:
    include_types = _normalize_requested(
        include,
        _VALID_INCLUDE,
        DEFAULT_EXPORT_INCLUDE,
        "export include",
        allow_empty=True,
    )
    formats = (
        _normalize_requested(
            plot_formats,
            _VALID_PLOT_FORMATS,
            DEFAULT_PLOT_FORMATS,
            "plot format",
            allow_empty=True,
        )
        if "plots" in include_types
        else ()
    )
    selected_window_formats = (
        _normalize_requested(
            window_formats,
            _VALID_WINDOW_FORMATS,
            DEFAULT_WINDOW_FORMATS,
            "window format",
            allow_empty=True,
        )
        if "window" in include_types
        else ()
    )
    selected_window_resolutions = (
        _normalize_requested(
            window_resolutions,
            _VALID_WINDOW_RESOLUTIONS,
            DEFAULT_WINDOW_RESOLUTIONS,
            "window resolution",
            allow_empty=True,
        )
        if "window" in include_types
        else ()
    )
    return ExportSelection(
        include=include_types,
        plot_formats=formats,
        window_formats=selected_window_formats,
        window_resolutions=selected_window_resolutions,
    )


def _normalize_requested(
    values: Sequence[str] | None,
    allowed: frozenset[str],
    defaults: Sequence[str],
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if values is None:
        return tuple(defaults)

    normalized: list[str] = []
    for value in values:
        lowered = str(value).strip().lower()
        if lowered not in allowed:
            raise ValueError(f"Unsupported {label}: {value}")
        if lowered not in normalized:
            normalized.append(lowered)
    if not normalized and not allow_empty:
        raise ValueError(f"At least one {label} must be requested.")
    return tuple(normalized)


def _plan_export(snapshot: ExportSnapshotData, selection: ExportSelection) -> list[PlannedArtifact]:
    artifacts: list[PlannedArtifact] = []

    for include_key in selection.include:
        if include_key == "json":
            artifacts.append(
                PlannedArtifact(
                    key="science_json",
                    label="Science JSON",
                    kind="structured",
                    content_type="application/json; charset=utf-8",
                    status="ready",
                    reason=None,
                    filename_suffix="science.json",
                    build_target="json",
                    format="json",
                )
            )
        elif include_key == "csv":
            artifacts.append(
                PlannedArtifact(
                    key="catalog_csv",
                    label="Catalog CSV",
                    kind="catalog",
                    content_type="text/csv; charset=utf-8",
                    status="ready",
                    reason=None,
                    filename_suffix="catalog.csv",
                    build_target="csv",
                    format="csv",
                )
            )
        elif include_key == "npz":
            artifacts.append(
                PlannedArtifact(
                    key="diagnostics_npz",
                    label="Diagnostics NPZ",
                    kind="arrays",
                    content_type="application/x-npz",
                    status="ready",
                    reason=None,
                    filename_suffix="diagnostics.npz",
                    build_target="npz",
                    format="npz",
                )
            )
        elif include_key == "plots":
            for plot_plan in _plot_plan(snapshot):
                for fmt in selection.plot_formats:
                    artifacts.append(
                        PlannedArtifact(
                            key=f"{plot_plan.key}_{fmt}",
                            label=f"{plot_plan.title} ({fmt.upper()})",
                            kind="plot",
                            content_type=_plot_content_type(fmt),
                            status=plot_plan.status,
                            reason=plot_plan.reason,
                            filename_suffix=f"{plot_plan.key}.{fmt}",
                            build_target="plot",
                            format=fmt,
                            plot_key=plot_plan.key,
                        )
                    )
        elif include_key == "window":
            for mode in selection.window_resolutions:
                mode_label = _window_mode_label(mode)
                artifacts.append(
                    PlannedArtifact(
                        key=f"window_{mode}_meta",
                        label=f"Window Metadata ({mode_label})",
                        kind="window",
                        content_type="application/json; charset=utf-8",
                        status="ready",
                        reason=None,
                        filename_suffix=f"window_{mode}.meta.json",
                        build_target="window_meta",
                        format="json",
                        window_mode=mode,
                    )
                )
                for fmt in selection.window_formats:
                    artifacts.append(
                        PlannedArtifact(
                            key=f"window_{mode}_{fmt}",
                            label=_window_artifact_label(fmt, mode),
                            kind="window",
                            content_type=_window_content_type(fmt),
                            status="ready",
                            reason=None,
                            filename_suffix=f"window_{mode}.{fmt}",
                            build_target=f"window_{fmt}",
                            format=fmt,
                            window_mode=mode,
                        )
                    )
    return artifacts


def _plot_plan(snapshot: ExportSnapshotData) -> list[PlotPlan]:
    plots = [
        PlotPlan(key="dynamic_spectrum", title="Dynamic Spectrum", status="ready", reason=None),
        PlotPlan(key="profile_diagnostics", title="Profile Diagnostics", status="ready", reason=None),
    ]

    if snapshot.temporal_acf.size and snapshot.spectral_acf.size:
        plots.append(PlotPlan(key="acf_panel", title="ACF Panel", status="ready", reason=None))
    else:
        plots.append(
            PlotPlan(
                key="acf_panel",
                title="ACF Panel",
                status="omitted",
                reason="acf_diagnostics_unavailable",
            )
        )

    temporal = snapshot.temporal_structure or {}
    if (
        snapshot.temporal_structure is not None
        and temporal.get("status") == "ok"
        and len(temporal.get("averaged_psd_freq_hz", []) or []) > 0
    ):
        plots.append(PlotPlan(key="power_spectrum", title="Power Spectrum", status="ready", reason=None))
    else:
        plots.append(
            PlotPlan(
                key="power_spectrum",
                title="Power Spectrum",
                status="omitted",
                reason="temporal_structure_unavailable",
            )
        )

    if snapshot.dm_optimization is None:
        plots.append(
            PlotPlan(
                key="dm_curve",
                title="DM Curve",
                status="omitted",
                reason="dm_optimization_unavailable",
            )
        )
        plots.append(
            PlotPlan(
                key="dm_residuals",
                title="DM Residuals",
                status="omitted",
                reason="dm_optimization_unavailable",
            )
        )
    else:
        plots.append(PlotPlan(key="dm_curve", title="DM Curve", status="ready", reason=None))
        if snapshot.dm_optimization.get("residual_status") == "ok":
            plots.append(PlotPlan(key="dm_residuals", title="DM Residuals", status="ready", reason=None))
        else:
            plots.append(
                PlotPlan(
                    key="dm_residuals",
                    title="DM Residuals",
                    status="omitted",
                    reason="residual_diagnostics_unavailable",
                )
            )
    return plots


def _build_plot_previews(snapshot: ExportSnapshotData, plan: Sequence[PlannedArtifact]) -> list[ExportPlotPreview]:
    selected_keys: list[str] = []
    for artifact in plan:
        if artifact.kind == "plot" and artifact.plot_key and artifact.plot_key not in selected_keys:
            selected_keys.append(artifact.plot_key)

    plot_status = {item.key: item for item in _plot_plan(snapshot)}
    figure_cache = _build_plot_figure_cache(snapshot, plan)
    previews: list[ExportPlotPreview] = []
    for plot_key in selected_keys:
        plot_plan = plot_status[plot_key]
        figure = figure_cache.get(plot_key)
        svg = _figure_string(figure, "svg") if figure is not None else None
        previews.append(
            ExportPlotPreview(
                plot_key=plot_key,
                title=plot_plan.title,
                status=plot_plan.status,
                reason=plot_plan.reason,
                svg=svg,
            )
        )

    for figure in figure_cache.values():
        plt.close(figure)
    return previews


def _build_plot_figure_cache(
    snapshot: ExportSnapshotData,
    plan: Sequence[PlannedArtifact],
) -> dict[str, plt.Figure]:
    plot_keys = {
        artifact.plot_key
        for artifact in plan
        if artifact.kind == "plot" and artifact.status == "ready" and artifact.plot_key is not None
    }
    return {
        str(plot_key): _plot_figure(snapshot, str(plot_key))
        for plot_key in plot_keys
    }


def _materialize_artifact(
    snapshot: ExportSnapshotData,
    artifact: PlannedArtifact,
    plot_figure_cache: dict[str, plt.Figure],
    *,
    window_exports: dict[str, WindowExportData],
    window_metadata: dict[str, dict[str, Any]],
) -> bytes:
    if artifact.build_target == "csv":
        return _build_catalog_csv(snapshot)
    if artifact.build_target == "npz":
        return _build_diagnostics_npz(snapshot)
    if artifact.build_target == "window_meta":
        if artifact.window_mode is None:
            raise ValueError(f"Missing window mode for artifact: {artifact.key}")
        return _build_window_metadata_json(window_metadata[artifact.window_mode])
    if artifact.build_target == "window_npz":
        if artifact.window_mode is None:
            raise ValueError(f"Missing window mode for artifact: {artifact.key}")
        return _build_window_npz(window_exports[artifact.window_mode], window_metadata[artifact.window_mode])
    if artifact.build_target == "window_fil":
        if artifact.window_mode is None:
            raise ValueError(f"Missing window mode for artifact: {artifact.key}")
        return _build_window_fil(snapshot, window_exports[artifact.window_mode])
    if artifact.build_target == "plot":
        if artifact.plot_key is None or artifact.format is None:
            raise ValueError(f"Incomplete plot artifact plan: {artifact.key}")
        figure = plot_figure_cache.get(artifact.plot_key)
        if figure is None:
            raise ValueError(f"Missing plot figure for artifact: {artifact.key}")
        return _figure_bytes(figure, artifact.format)
    raise ValueError(f"Unsupported artifact build target: {artifact.build_target}")


def _plot_content_type(fmt: str) -> str:
    return "image/png" if fmt == "png" else "image/svg+xml"


def _window_content_type(fmt: str) -> str:
    if fmt == "npz":
        return "application/x-npz"
    if fmt == "fil":
        return "application/octet-stream"
    raise ValueError(f"Unsupported window format: {fmt}")


def _window_mode_label(mode: str) -> str:
    return "Native" if mode == "native" else "View"


def _window_artifact_label(fmt: str, mode: str) -> str:
    mode_label = _window_mode_label(mode)
    if fmt == "fil":
        return f"Window Filterbank (FIL, {mode_label})"
    if fmt == "npz":
        return f"Window Data (NPZ, {mode_label})"
    raise ValueError(f"Unsupported window format: {fmt}")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "flits_export"


def _artifact_url(session_id: str, export_id: str, name: str) -> str:
    return f"/api/sessions/{session_id}/exports/{export_id}/{name}"


def _ready_artifact_spec(
    *,
    session_id: str,
    export_id: str,
    name: str,
    kind: str,
    content_type: str,
    content: bytes,
    size_override: int | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "content_type": content_type,
        "size_bytes": len(content) if size_override is None else size_override,
        "url": _artifact_url(session_id, export_id, name),
        "status": "ready",
        "reason": None,
    }


def _omitted_artifact_spec(
    *,
    name: str,
    kind: str,
    content_type: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "content_type": content_type,
        "size_bytes": None,
        "url": None,
        "status": "omitted",
        "reason": reason,
    }


def _artifact_objects(
    *,
    session_id: str,
    export_id: str,
    specs: Sequence[dict[str, Any]],
    json_name: str | None = None,
    json_size: int | None = None,
) -> list[ExportArtifact]:
    artifacts = [
        ExportArtifact(
            name=str(spec["name"]),
            kind=str(spec["kind"]),
            content_type=str(spec["content_type"]),
            size_bytes=spec["size_bytes"],
            url=spec["url"],
            status=str(spec["status"]),
            reason=spec["reason"],
        )
        for spec in specs
    ]
    if json_name is not None:
        artifacts.insert(
            0,
            ExportArtifact(
                name=json_name,
                kind="structured",
                content_type="application/json; charset=utf-8",
                size_bytes=json_size,
                url=_artifact_url(session_id, export_id, json_name),
                status="ready",
                reason=None,
            ),
        )
    return artifacts


def _build_science_json(snapshot: ExportSnapshotData, manifest: ExportManifest) -> bytes:
    payload = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "flits_version": __version__,
        "export_id": snapshot.export_id,
        "bundle_name": snapshot.bundle_name,
        "exported_at_utc": snapshot.created_at_utc,
        "meta": snapshot.meta,
        "state": snapshot.state,
        "results": snapshot.results,
        "width_analysis": snapshot.width_analysis,
        "dm_optimization": snapshot.dm_optimization,
        "temporal_structure": snapshot.temporal_structure,
        "artifacts": manifest.to_dict()["artifacts"],
    }
    return (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _build_catalog_csv(snapshot: ExportSnapshotData) -> bytes:
    results = snapshot.results or {}
    width_analysis = snapshot.width_analysis or {}
    accepted_width = width_analysis.get("accepted_width") or results.get("accepted_width") or {}
    dm = snapshot.dm_optimization or {}
    temporal = snapshot.temporal_structure or {}
    result_uncertainty_details = results.get("uncertainty_details") or {}
    dm_uncertainty_details = dm.get("uncertainty_details") or {}
    temporal_uncertainty_details = temporal.get("uncertainty_details") or {}

    def uncertainty_columns(prefix: str, detail: dict[str, Any] | None) -> dict[str, str]:
        payload = detail or {}
        return {
            f"{prefix}_uncertainty_class": str(payload.get("classification", "") or ""),
            f"{prefix}_uncertainty_basis": str(payload.get("basis", "") or ""),
        }

    row = {
        "bundle_name": snapshot.bundle_name,
        "exported_at_utc": snapshot.created_at_utc,
        "flits_version": __version__,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "burst_name": snapshot.meta.get("burst_name", ""),
        "burst_file": snapshot.meta.get("burst_file", ""),
        "source_name": snapshot.meta.get("source_name", ""),
        "telescope": snapshot.meta.get("telescope", ""),
        "detected_telescope": snapshot.meta.get("detected_telescope", ""),
        "preset_key": snapshot.meta.get("preset_key", ""),
        "detected_preset_key": snapshot.meta.get("detected_preset_key", ""),
        "dm_applied": snapshot.meta.get("dm", ""),
        "accepted_width_method": accepted_width.get("method", ""),
        "accepted_width_value_ms": accepted_width.get("value", ""),
        "accepted_width_uncertainty_ms": accepted_width.get("uncertainty", ""),
        "toa_topo_mjd": results.get("toa_topo_mjd", ""),
        "snr_peak": results.get("snr_peak", ""),
        "snr_integrated": results.get("snr_integrated", ""),
        "width_ms_acf": results.get("width_ms_acf", ""),
        "width_ms_model": results.get("width_ms_model", ""),
        "spectral_width_mhz_acf": results.get("spectral_width_mhz_acf", ""),
        "tau_sc_ms": results.get("tau_sc_ms", ""),
        "peak_flux_jy": results.get("peak_flux_jy", ""),
        "fluence_jyms": results.get("fluence_jyms", ""),
        "iso_e_erg": results.get("iso_e", ""),
        "min_structure_ms_primary": temporal.get("min_structure_ms_primary", ""),
        "min_structure_ms_wavelet": temporal.get("min_structure_ms_wavelet", ""),
        "fitburst_min_component_ms": temporal.get("fitburst_min_component_ms", ""),
        "psd_alpha": temporal.get("power_law_alpha", ""),
        "psd_alpha_err": temporal.get("power_law_alpha_err", ""),
        "psd_fit_status": temporal.get("power_law_fit_status", ""),
        "psd_crossover_frequency_hz": temporal.get("crossover_frequency_hz", ""),
        "psd_crossover_frequency_status": temporal.get("crossover_frequency_status", ""),
        "psd_crossover_frequency_hz_3sigma_low": temporal.get("crossover_frequency_hz_3sigma_low", ""),
        "psd_crossover_frequency_hz_3sigma_high": temporal.get("crossover_frequency_hz_3sigma_high", ""),
        "noise_psd_segment_count": temporal.get("noise_psd_segment_count", ""),
        "event_window_start_ms": snapshot.state.get("event_ms", ["", ""])[0],
        "event_window_end_ms": snapshot.state.get("event_ms", ["", ""])[1],
        "spectral_extent_start_mhz": snapshot.state.get("spectral_extent_mhz", ["", ""])[0],
        "spectral_extent_end_mhz": snapshot.state.get("spectral_extent_mhz", ["", ""])[1],
        "peak_positions_ms": _join_list(results.get("peak_positions_ms", [])),
        "measurement_flags": _join_list(results.get("measurement_flags", [])),
        "mask_count": results.get("mask_count", len(snapshot.state.get("masked_channels", []))),
        "masked_channels": _join_list(snapshot.state.get("masked_channels", [])),
        "sefd_jy": snapshot.meta.get("sefd_jy", ""),
        "npol": snapshot.meta.get("npol", ""),
        "header_npol": snapshot.meta.get("header_npol", ""),
        "npol_override": snapshot.meta.get("npol_override", ""),
        "distance_mpc": snapshot.meta.get("distance_mpc", ""),
        "redshift": snapshot.meta.get("redshift", ""),
        "dm_sweep_center": dm.get("center_dm", ""),
        "dm_best": dm.get("best_dm", ""),
        "dm_uncertainty": dm.get("best_dm_uncertainty", ""),
        "dm_fit_status": dm.get("fit_status", ""),
        "dm_best_snr": dm.get("best_sn", ""),
        "dm_sampled_best_dm": dm.get("sampled_best_dm", ""),
        "dm_sampled_best_snr": dm.get("sampled_best_sn", ""),
        "dm_snr_metric": dm.get("snr_metric", ""),
        "residual_status": dm.get("residual_status", ""),
    }
    row.update(uncertainty_columns("accepted_width", accepted_width.get("uncertainty_detail")))
    row.update(uncertainty_columns("toa_topo_mjd", result_uncertainty_details.get("toa_topo_mjd")))
    row.update(uncertainty_columns("width_ms_acf", result_uncertainty_details.get("width_ms_acf")))
    row.update(uncertainty_columns("width_ms_model", result_uncertainty_details.get("width_ms_model")))
    row.update(uncertainty_columns("spectral_width_mhz_acf", result_uncertainty_details.get("spectral_width_mhz_acf")))
    row.update(uncertainty_columns("tau_sc_ms", result_uncertainty_details.get("tau_sc_ms")))
    row.update(uncertainty_columns("peak_flux_jy", result_uncertainty_details.get("peak_flux_jy")))
    row.update(uncertainty_columns("fluence_jyms", result_uncertainty_details.get("fluence_jyms")))
    row.update(uncertainty_columns("iso_e_erg", result_uncertainty_details.get("iso_e")))
    row.update(uncertainty_columns("dm", dm_uncertainty_details.get("best_dm")))
    row.update(uncertainty_columns("psd_alpha", temporal_uncertainty_details.get("power_law_alpha")))
    row.update(uncertainty_columns("psd_crossover_frequency_hz", temporal_uncertainty_details.get("crossover_frequency_hz")))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _build_diagnostics_npz(snapshot: ExportSnapshotData) -> bytes:
    payload: dict[str, Any] = {
        "dynamic_spectrum": np.asarray(snapshot.dynamic_spectrum, dtype=float),
        "time_axis_ms": np.asarray(snapshot.time_axis_ms, dtype=float),
        "freq_axis_mhz": np.asarray(snapshot.freq_axis_mhz, dtype=float),
        "spectral_axis_mhz": np.asarray(snapshot.spectral_axis_mhz, dtype=float),
        "crop_bins": np.asarray([snapshot.crop_start_bin, snapshot.crop_end_bin], dtype=int),
        "event_window_bins": np.asarray([snapshot.event_start_rel_bin, snapshot.event_end_rel_bin], dtype=int),
        "event_window_ms": np.asarray(snapshot.event_window_ms, dtype=float),
        "spectral_extent_channels": np.asarray([snapshot.selected_channel_start, snapshot.selected_channel_end], dtype=int),
        "spectral_extent_mhz": np.asarray(snapshot.spectral_extent_mhz, dtype=float),
        "masked_channels": np.asarray(snapshot.masked_channels, dtype=int),
        "peak_positions_ms": np.asarray(snapshot.peak_positions_ms, dtype=float),
        "time_profile_sn": np.asarray(snapshot.time_profile_sn, dtype=float),
        "burst_only_profile_sn": np.asarray(snapshot.burst_only_profile_sn, dtype=float),
        "event_profile_sn": np.asarray(snapshot.event_profile_sn, dtype=float),
        "spectrum_sn": np.asarray(snapshot.spectrum_sn, dtype=float),
        "temporal_acf": np.asarray(snapshot.temporal_acf, dtype=float),
        "temporal_acf_lags_ms": np.asarray(snapshot.temporal_acf_lags_ms, dtype=float),
        "spectral_acf": np.asarray(snapshot.spectral_acf, dtype=float),
        "spectral_acf_lags_mhz": np.asarray(snapshot.spectral_acf_lags_mhz, dtype=float),
    }

    if snapshot.results is not None:
        diagnostics = snapshot.results.get("diagnostics", {})
        gaussian_fits = diagnostics.get("gaussian_fits", [])
        payload["gaussian_amp"] = np.asarray([fit.get("amp", np.nan) for fit in gaussian_fits], dtype=float)
        payload["gaussian_mu_ms"] = np.asarray([fit.get("mu_ms", np.nan) for fit in gaussian_fits], dtype=float)
        payload["gaussian_sigma_ms"] = np.asarray([fit.get("sigma_ms", np.nan) for fit in gaussian_fits], dtype=float)
        payload["gaussian_offset"] = np.asarray([fit.get("offset", np.nan) for fit in gaussian_fits], dtype=float)
        scattering_fit = diagnostics.get("scattering_fit") or {}
        for key in (
            "freq_axis_mhz",
            "time_axis_ms",
            "data_dynamic_spectrum_sn",
            "model_dynamic_spectrum_sn",
            "residual_dynamic_spectrum_sn",
            "data_profile_sn",
            "model_profile_sn",
            "residual_profile_sn",
        ):
            if key in scattering_fit:
                payload[f"scattering_{key}"] = np.asarray(scattering_fit.get(key, []), dtype=float)

    if snapshot.dm_optimization is not None:
        for key in (
            "trial_dms",
            "snr",
            "subband_freqs_mhz",
            "arrival_times_applied_ms",
            "arrival_times_best_ms",
            "residuals_applied_ms",
            "residuals_best_ms",
        ):
            payload[key] = np.asarray(snapshot.dm_optimization.get(key, []), dtype=float)

    if snapshot.temporal_structure is not None:
        for key in (
            "raw_periodogram_freq_hz",
            "raw_periodogram_power",
            "averaged_psd_freq_hz",
            "averaged_psd_power",
            "noise_psd_freq_hz",
            "noise_psd_power",
            "matched_filter_scales_ms",
            "matched_filter_boxcar_sigma",
            "matched_filter_gaussian_sigma",
            "wavelet_scales_ms",
            "wavelet_sigma",
        ):
            payload[key] = np.asarray(snapshot.temporal_structure.get(key, []), dtype=float)
        for key in (
            "crossover_frequency_hz",
            "crossover_frequency_hz_3sigma_low",
            "crossover_frequency_hz_3sigma_high",
            "noise_psd_segment_count",
        ):
            payload[key] = np.asarray([snapshot.temporal_structure.get(key, np.nan)], dtype=float)
        payload["crossover_frequency_status"] = np.asarray(
            [snapshot.temporal_structure.get("crossover_frequency_status", "unavailable")],
            dtype=str,
        )

    buffer = io.BytesIO()
    np.savez_compressed(buffer, **payload)
    return buffer.getvalue()


def _build_window_metadata_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _build_window_npz(window: WindowExportData, metadata_payload: dict[str, Any]) -> bytes:
    payload: dict[str, Any] = {
        "dynamic_spectrum": np.asarray(window.dynamic_spectrum, dtype=float),
        "time_axis_ms": np.asarray(window.time_axis_ms, dtype=float),
        "freq_axis_mhz": np.asarray(window.freq_axis_mhz, dtype=float),
        "time_bins": np.asarray([window.time_start_bin, window.time_end_bin], dtype=int),
        "crop_bins": np.asarray([window.crop_start_bin, window.crop_end_bin], dtype=int),
        "event_window_bins": np.asarray([window.event_start_rel_bin, window.event_end_rel_bin], dtype=int),
        "event_window_ms": np.asarray(metadata_payload["window"]["event_window_ms"], dtype=float),
        "spectral_extent_channels": np.asarray(window.spectral_extent_channels, dtype=int),
        "spectral_extent_mhz": np.asarray(window.spectral_extent_mhz, dtype=float),
        "masked_channels": np.asarray(window.masked_channels, dtype=int),
        "time_factor": np.asarray([window.time_factor], dtype=int),
        "freq_factor": np.asarray([window.freq_factor], dtype=int),
        "effective_tsamp_sec": np.asarray([window.effective_tsamp_sec], dtype=float),
        "effective_freqres_mhz": np.asarray([window.effective_freqres_mhz], dtype=float),
        "window_metadata_json": np.asarray(json.dumps(metadata_payload, sort_keys=True), dtype=np.str_),
    }
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **payload)
    return buffer.getvalue()


def _build_window_fil(snapshot: ExportSnapshotData, window: WindowExportData) -> bytes:
    source_name = str(snapshot.meta.get("source_name") or snapshot.meta.get("burst_name") or snapshot.bundle_name)
    file_label = f"{snapshot.bundle_name}_window_{window.mode}.fil"
    tstart = float(
        float(snapshot.meta.get("start_mjd", 0.0))
        + ((float(snapshot.meta.get("read_start_sec", 0.0)) + (float(window.time_start_bin) * float(snapshot.meta.get("tsamp_us", 0.0)) / 1e6)) / 86400.0)
    )
    header = SigprocFilterbankHeader(
        rawdatafile=file_label,
        source_name=source_name,
        nchans=int(window.dynamic_spectrum.shape[0]),
        foff=float(window.frequency_step_mhz),
        fch1=float(window.freq_axis_mhz[0]) if window.freq_axis_mhz.size else 0.0,
        tsamp=float(window.effective_tsamp_sec),
        tstart=tstart,
        machine_id=int(snapshot.meta.get("machine_id") or 0),
        telescope_id=int(snapshot.meta.get("telescope_id") or 0),
        nbits=32,
        nifs=1,
    )
    content = np.nan_to_num(np.asarray(window.dynamic_spectrum, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return build_sigproc_filterbank_bytes(content, header)


def _plot_figure(snapshot: ExportSnapshotData, plot_key: str) -> plt.Figure:
    if plot_key == "dynamic_spectrum":
        return _dynamic_spectrum_figure(snapshot)
    if plot_key == "profile_diagnostics":
        return _profile_diagnostics_figure(snapshot)
    if plot_key == "acf_panel":
        return _acf_panel_figure(snapshot)
    if plot_key == "power_spectrum":
        return _power_spectrum_figure(snapshot)
    if plot_key == "dm_curve":
        return _dm_curve_figure(snapshot)
    if plot_key == "dm_residuals":
        return _dm_residuals_figure(snapshot)
    raise ValueError(f"Unsupported export plot: {plot_key}")


def _dynamic_spectrum_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9.0, 5.5), constrained_layout=True)
    freqs, dynamic = _sorted_frequency_data(snapshot.freq_axis_mhz, snapshot.dynamic_spectrum)
    if snapshot.time_axis_ms.size:
        time_start = float(snapshot.time_axis_ms[0])
        time_end = float(snapshot.time_axis_ms[-1] + max(snapshot.meta.get("tsamp_us", 0.0) / 1000.0, 1e-6))
    else:
        time_start, time_end = 0.0, 1.0
    freq_start = float(freqs[0]) if freqs.size else 0.0
    freq_end = float(freqs[-1]) if freqs.size else 1.0

    image = ax.imshow(
        dynamic,
        aspect="auto",
        origin="lower",
        extent=[time_start, time_end, freq_start, freq_end],
        cmap=ASTROFLASH_HEATMAP_CMAP,
        vmin=_robust_limits(dynamic)[0],
        vmax=_robust_limits(dynamic)[1],
    )
    ax.axvline(snapshot.event_window_ms[0], color=ASTROFLASH_COLORS["accent_alt"], linewidth=1.5)
    ax.axvline(snapshot.event_window_ms[1], color=ASTROFLASH_COLORS["accent_alt"], linewidth=1.5)
    ax.axhline(snapshot.spectral_extent_mhz[0], color=ASTROFLASH_COLORS["accent"], linewidth=1.4, linestyle="--")
    ax.axhline(snapshot.spectral_extent_mhz[1], color=ASTROFLASH_COLORS["accent"], linewidth=1.4, linestyle="--")
    for peak in snapshot.peak_positions_ms:
        ax.axvline(float(peak), color=ASTROFLASH_COLORS["alert"], linewidth=1.2, linestyle=":")
    ax.set_title("Dynamic Spectrum")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Frequency (MHz)")
    colorbar = fig.colorbar(image, ax=ax, pad=0.01, label="Intensity (arb.)")
    _style_export_axis(ax, grid=False)
    _style_colorbar(colorbar)
    return fig


def _profile_diagnostics_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    fig, (ax_time, ax_spec) = plt.subplots(2, 1, figsize=(9.0, 6.4), constrained_layout=True)
    ax_time.plot(snapshot.time_axis_ms, snapshot.time_profile_sn, color=ASTROFLASH_COLORS["neutral"], linewidth=1.5, label="Full-band profile")
    ax_time.plot(snapshot.time_axis_ms, snapshot.burst_only_profile_sn, color=ASTROFLASH_COLORS["accent"], linewidth=1.5, label="Selected-band profile")
    ax_time.axvspan(snapshot.event_window_ms[0], snapshot.event_window_ms[1], color=ASTROFLASH_COLORS["accent_alt"], alpha=0.14)
    for peak in snapshot.peak_positions_ms:
        ax_time.axvline(float(peak), color=ASTROFLASH_COLORS["alert"], linewidth=1.2, linestyle=":")
    ax_time.set_title("Profile Diagnostics")
    ax_time.set_ylabel("S/N")
    ax_time.legend(loc="upper right", frameon=False)

    freqs = snapshot.spectral_axis_mhz
    spectrum = snapshot.spectrum_sn
    if freqs.size and freqs[0] > freqs[-1]:
        freqs = freqs[::-1]
        spectrum = spectrum[::-1]
    ax_spec.plot(freqs, spectrum, color=ASTROFLASH_COLORS["accent_alt"], linewidth=1.5)
    ax_spec.set_xlabel("Frequency (MHz)")
    ax_spec.set_ylabel("S/N")
    _style_export_axis(ax_time)
    _style_export_axis(ax_spec)
    return fig


def _acf_panel_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    fig, (ax_time, ax_spec) = plt.subplots(1, 2, figsize=(9.0, 4.2), constrained_layout=True)
    ax_time.plot(snapshot.temporal_acf_lags_ms, snapshot.temporal_acf, color=ASTROFLASH_COLORS["accent"], linewidth=1.5)
    ax_time.axhline(0.5, color=ASTROFLASH_COLORS["warning"], linewidth=1.0, linestyle="--")
    ax_time.set_title("Temporal ACF")
    ax_time.set_xlabel("Lag (ms)")
    ax_time.set_ylabel("Normalized ACF")
    ax_time.set_ylim(-0.05, 1.05)

    ax_spec.plot(snapshot.spectral_acf_lags_mhz, snapshot.spectral_acf, color=ASTROFLASH_COLORS["accent_alt"], linewidth=1.5)
    ax_spec.axhline(0.5, color=ASTROFLASH_COLORS["warning"], linewidth=1.0, linestyle="--")
    ax_spec.set_title("Spectral ACF")
    ax_spec.set_xlabel("Lag (MHz)")
    ax_spec.set_ylabel("Normalized ACF")
    ax_spec.set_ylim(-0.05, 1.05)
    _style_export_axis(ax_time)
    _style_export_axis(ax_spec)
    return fig


def _positive_xy(x_values: Any, y_values: Any) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
    return np.asarray(x[mask], dtype=float), np.asarray(y[mask], dtype=float)


def _power_spectrum_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    temporal = snapshot.temporal_structure or {}
    freq_hz, power = _positive_xy(
        temporal.get("averaged_psd_freq_hz", []),
        temporal.get("averaged_psd_power", []),
    )
    noise_freq_hz, noise_power = _positive_xy(
        temporal.get("noise_psd_freq_hz", []),
        temporal.get("noise_psd_power", []),
    )

    fig, (ax_power, ax_residual) = plt.subplots(
        2,
        1,
        figsize=(9.0, 6.6),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    ax_power.loglog(freq_hz, power, color=ASTROFLASH_COLORS["neutral"], linewidth=1.2, label="Burst power spectrum")
    if noise_freq_hz.size and noise_power.size:
        ax_power.loglog(
            noise_freq_hz,
            noise_power,
            color=ASTROFLASH_COLORS["noise"],
            linewidth=1.0,
            alpha=0.8,
            label="Noise power spectrum",
        )

    a = temporal.get("power_law_a")
    alpha = temporal.get("power_law_alpha")
    c = temporal.get("power_law_c")
    has_model = all(value is not None and np.isfinite(float(value)) and float(value) > 0 for value in (a, alpha, c))
    model_freq = freq_hz
    if has_model and model_freq.size:
        a_value = float(a)
        alpha_value = float(alpha)
        c_value = float(c)
        power_law = a_value * np.power(model_freq, -alpha_value)
        white_noise = np.full_like(model_freq, c_value, dtype=float)
        model = power_law + white_noise
        ax_power.loglog(
            model_freq,
            power_law,
            color=ASTROFLASH_COLORS["accent"],
            linewidth=1.7,
            linestyle="--",
            label="Power law",
        )
        ax_power.loglog(
            model_freq,
            white_noise,
            color=ASTROFLASH_COLORS["accent"],
            linewidth=1.4,
            linestyle=":",
            label="White noise constant",
        )

        residual_mask = np.isfinite(model) & (model > 0)
        residual_freq = model_freq[residual_mask]
        residual_ratio = power[residual_mask] / model[residual_mask]
        residual_mask = np.isfinite(residual_ratio) & (residual_ratio > 0)
        ax_residual.semilogx(
            residual_freq[residual_mask],
            residual_ratio[residual_mask],
            color=ASTROFLASH_COLORS["neutral"],
            linewidth=1.0,
        )

    crossover = temporal.get("crossover_frequency_hz")
    if crossover is not None and np.isfinite(float(crossover)) and float(crossover) > 0 and freq_hz.size:
        crossover_hz = float(crossover)
        visible_min = float(np.min(freq_hz))
        visible_max = float(np.max(freq_hz))
        low = temporal.get("crossover_frequency_hz_3sigma_low")
        high = temporal.get("crossover_frequency_hz_3sigma_high")
        if low is not None and high is not None and np.isfinite(float(low)) and np.isfinite(float(high)):
            span_low = max(float(low), visible_min)
            span_high = min(float(high), visible_max)
            if span_high > span_low:
                ax_power.axvspan(span_low, span_high, color=ASTROFLASH_COLORS["crossover"], alpha=0.22, linewidth=0)
                ax_residual.axvspan(span_low, span_high, color=ASTROFLASH_COLORS["crossover"], alpha=0.22, linewidth=0)
        if visible_min <= crossover_hz <= visible_max:
            ax_power.axvline(crossover_hz, color=ASTROFLASH_COLORS["crossover"], linewidth=1.4, label="Crossover frequency")
            ax_residual.axvline(crossover_hz, color=ASTROFLASH_COLORS["crossover"], linewidth=1.4)

    ax_residual.axhline(1.0, color=ASTROFLASH_COLORS["accent"], linewidth=1.2)
    ax_residual.set_yscale("log")
    ax_power.set_title("Power Spectrum")
    ax_power.set_ylabel("Power")
    ax_residual.set_xlabel("Frequency (Hz)")
    ax_residual.set_ylabel("Residuals")
    ax_power.legend(frameon=False, loc="best")
    _style_export_axis(ax_power)
    _style_export_axis(ax_residual)
    return fig


def _dm_curve_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    optimization = snapshot.dm_optimization or {}
    fig, ax = plt.subplots(figsize=(8.6, 4.8), constrained_layout=True)
    trial_dms = np.asarray(optimization.get("trial_dms", []), dtype=float)
    scores = np.asarray(optimization.get("snr", []), dtype=float)
    metric_key = str(optimization.get("snr_metric", "integrated_event_snr"))
    metric_definition = dm_metric_definition(metric_key)
    metric_label = metric_definition.label if metric_definition is not None else "DM Metric"
    ax.plot(trial_dms, scores, color=ASTROFLASH_COLORS["accent"], linewidth=1.5, marker="o", markersize=3.5)
    ax.axvline(float(optimization.get("center_dm", np.nan)), color=ASTROFLASH_COLORS["neutral"], linewidth=1.0, linestyle=":")
    ax.axvline(float(optimization.get("sampled_best_dm", np.nan)), color=ASTROFLASH_COLORS["accent_alt"], linewidth=1.2, linestyle="--")
    ax.axvline(float(optimization.get("best_dm", np.nan)), color=ASTROFLASH_COLORS["accent_strong"], linewidth=1.4)
    ax.axvline(float(optimization.get("applied_dm", np.nan)), color=ASTROFLASH_COLORS["alert"], linewidth=1.0, linestyle=":")
    ax.set_title("DM Curve")
    ax.set_xlabel("Dispersion Measure")
    ax.set_ylabel(metric_label)
    _style_export_axis(ax)
    return fig


def _dm_residuals_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    optimization = snapshot.dm_optimization or {}
    freqs = np.asarray(optimization.get("subband_freqs_mhz", []), dtype=float)
    applied = np.asarray(optimization.get("residuals_applied_ms", []), dtype=float)
    best = np.asarray(optimization.get("residuals_best_ms", []), dtype=float)
    fig, ax = plt.subplots(figsize=(8.6, 4.8), constrained_layout=True)
    ax.plot(freqs, applied, color=ASTROFLASH_COLORS["accent_alt"], linewidth=1.4, marker="o", markersize=4, label="Applied DM")
    ax.plot(freqs, best, color=ASTROFLASH_COLORS["accent"], linewidth=1.4, marker="o", markersize=4, label="Best-fit DM")
    ax.axhline(0.0, color=ASTROFLASH_COLORS["neutral"], linewidth=1.0, linestyle="--")
    ax.set_title("DM Residuals")
    ax.set_xlabel("Sub-band Center Frequency (MHz)")
    ax.set_ylabel("Residual Arrival Time (ms)")
    ax.legend(frameon=False, loc="best")
    _style_export_axis(ax)
    return fig


def _sorted_frequency_data(freq_axis_mhz: np.ndarray, data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    freqs = np.asarray(freq_axis_mhz, dtype=float)
    plot_data = np.asarray(data, dtype=float)
    if freqs.size and freqs[0] > freqs[-1]:
        return freqs[::-1], np.flipud(plot_data)
    return freqs, plot_data


def _robust_limits(values: np.ndarray) -> tuple[float, float]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return -1.0, 1.0
    lower = float(np.nanquantile(finite, 0.02))
    upper = float(np.nanquantile(finite, 0.995))
    if not np.isfinite(lower) or not np.isfinite(upper) or upper <= lower:
        lower = float(np.nanmin(finite))
        upper = float(np.nanmax(finite))
    if upper <= lower:
        upper = lower + 1.0
    return lower, upper


def _style_export_axis(ax: plt.Axes, *, grid: bool = True) -> None:
    ax.set_facecolor("white")
    if grid:
        ax.grid(True, color=ASTROFLASH_COLORS["grid"], linewidth=0.8)
    else:
        ax.grid(False)
    for spine in ax.spines.values():
        spine.set_color(ASTROFLASH_COLORS["border"])
    ax.tick_params(colors=ASTROFLASH_COLORS["muted"], labelcolor=ASTROFLASH_COLORS["muted"])
    ax.xaxis.label.set_color(ASTROFLASH_COLORS["ink"])
    ax.yaxis.label.set_color(ASTROFLASH_COLORS["ink"])
    ax.title.set_color(ASTROFLASH_COLORS["ink"])


def _style_colorbar(colorbar: Any) -> None:
    colorbar.ax.yaxis.label.set_color(ASTROFLASH_COLORS["ink"])
    colorbar.ax.tick_params(color=ASTROFLASH_COLORS["muted"], labelcolor=ASTROFLASH_COLORS["muted"])


def _figure_bytes(figure: plt.Figure, fmt: str) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format=fmt, dpi=200, bbox_inches="tight", facecolor="white")
    return buffer.getvalue()


def _figure_string(figure: plt.Figure, fmt: str) -> str:
    text = _figure_bytes(figure, fmt).decode("utf-8")
    text = re.sub(r"<\?xml[^>]*>\s*", "", text)
    text = re.sub(r"<!DOCTYPE[^>]*>\s*", "", text)
    return text


def _event_window_ms(
    time_axis_ms: np.ndarray,
    tsamp_ms: float,
    event_rel_start: int,
    event_rel_end: int,
) -> tuple[float, float]:
    if time_axis_ms.size == 0:
        return 0.0, 0.0
    start = float(time_axis_ms[max(0, min(event_rel_start, time_axis_ms.size - 1))])
    end_index = max(0, min(event_rel_end - 1, time_axis_ms.size - 1))
    end = float(time_axis_ms[end_index] + tsamp_ms)
    return start, end


def _join_list(values: Sequence[Any]) -> str:
    return ";".join(str(value) for value in values)
