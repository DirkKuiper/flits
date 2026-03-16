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

from flits import __version__
from flits.measurements import _acf_width
from flits.models import ExportArtifact, ExportManifest


if TYPE_CHECKING:
    from flits.session import BurstSession


EXPORT_SCHEMA_VERSION = "1.0"
DEFAULT_EXPORT_INCLUDE = ("json", "csv", "npz", "plots")
DEFAULT_PLOT_FORMATS = ("png", "svg")
MAX_EXPORT_SNAPSHOTS = 3

_VALID_INCLUDE = frozenset(DEFAULT_EXPORT_INCLUDE)
_VALID_PLOT_FORMATS = frozenset(DEFAULT_PLOT_FORMATS)


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


def create_export_snapshot(
    session: "BurstSession",
    *,
    session_id: str,
    include: Sequence[str] | None = None,
    plot_formats: Sequence[str] | None = None,
) -> StoredExportSnapshot:
    include_types = _normalize_requested(include, _VALID_INCLUDE, DEFAULT_EXPORT_INCLUDE, "export include")
    formats = _normalize_requested(plot_formats, _VALID_PLOT_FORMATS, DEFAULT_PLOT_FORMATS, "plot format")
    snapshot = _build_snapshot_data(session)

    specs: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}

    if "csv" in include_types:
        csv_name = f"{snapshot.bundle_name}_catalog.csv"
        csv_bytes = _build_catalog_csv(snapshot)
        specs.append(
            _ready_artifact_spec(
                session_id=session_id,
                export_id=snapshot.export_id,
                name=csv_name,
                kind="catalog",
                content_type="text/csv; charset=utf-8",
                content=csv_bytes,
            )
        )
        contents[csv_name] = csv_bytes

    if "npz" in include_types:
        npz_name = f"{snapshot.bundle_name}_diagnostics.npz"
        npz_bytes = _build_diagnostics_npz(snapshot)
        specs.append(
            _ready_artifact_spec(
                session_id=session_id,
                export_id=snapshot.export_id,
                name=npz_name,
                kind="arrays",
                content_type="application/x-npz",
                content=npz_bytes,
            )
        )
        contents[npz_name] = npz_bytes

    if "plots" in include_types:
        for plot_name, figure, reason in _build_plot_figures(snapshot):
            for fmt in formats:
                artifact_name = f"{snapshot.bundle_name}_{plot_name}.{fmt}"
                content_type = "image/png" if fmt == "png" else "image/svg+xml"
                if figure is None:
                    specs.append(
                        _omitted_artifact_spec(
                            name=artifact_name,
                            kind="plot",
                            content_type=content_type,
                            reason=reason or "plot_unavailable",
                        )
                    )
                    continue
                plot_bytes = _figure_bytes(figure, fmt)
                specs.append(
                    _ready_artifact_spec(
                        session_id=session_id,
                        export_id=snapshot.export_id,
                        name=artifact_name,
                        kind="plot",
                        content_type=content_type,
                        content=plot_bytes,
                    )
                )
                contents[artifact_name] = plot_bytes
            if figure is not None:
                plt.close(figure)

    json_name = f"{snapshot.bundle_name}_science.json"
    json_bytes = b""
    json_size: int | None = None
    if "json" in include_types:
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
                kind="structured",
                content_type="application/json; charset=utf-8",
                content=json_bytes,
                size_override=json_size,
            ),
        )
        contents[json_name] = json_bytes

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
    masked, context = session._build_measurement_context_for_data()
    spec_lo, spec_hi = session._selected_channel_bounds()
    event_start_ms, event_end_ms = _event_window_ms(context.time_axis_ms, session.tsamp_ms, context.event_rel_start, context.event_rel_end)
    spectral_extent = tuple(sorted((float(session.freqs[spec_lo]), float(session.freqs[spec_hi]))))
    peak_positions = np.asarray([session.bin_to_ms(peak) for peak in session._current_peak_positions()], dtype=float)
    temporal_acf_lags_ms, temporal_acf = _acf_width(context.event_profile_sn, session.tsamp_ms)[1:]
    spectral_acf_lags_mhz, spectral_acf = _acf_width(context.spectrum_sn, abs(session.freqres))[1:]

    return ExportSnapshotData(
        export_id=export_id,
        bundle_name=bundle_name,
        created_at_utc=created_at_utc,
        meta=view["meta"],
        state=view["state"],
        results=session.results.to_dict() if session.results is not None else None,
        width_analysis=session.width_analysis.to_dict() if session.width_analysis is not None else None,
        dm_optimization=session.dm_optimization.to_dict() if session.dm_optimization is not None else None,
        dynamic_spectrum=np.asarray(masked, dtype=float),
        time_axis_ms=np.asarray(context.time_axis_ms, dtype=float),
        freq_axis_mhz=np.asarray(session.freqs, dtype=float),
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
        crop_start_bin=int(session.crop_start),
        crop_end_bin=int(session.crop_end),
        event_start_rel_bin=int(context.event_rel_start),
        event_end_rel_bin=int(context.event_rel_end),
        selected_channel_start=int(spec_lo),
        selected_channel_end=int(spec_hi),
        masked_channels=np.asarray(view["state"]["masked_channels"], dtype=int),
        peak_positions_ms=peak_positions,
    )


def _normalize_requested(
    values: Sequence[str] | None,
    allowed: frozenset[str],
    defaults: Sequence[str],
    label: str,
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
    if not normalized:
        raise ValueError(f"At least one {label} must be requested.")
    return tuple(normalized)


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
        "artifacts": manifest.to_dict()["artifacts"],
    }
    return (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _build_catalog_csv(snapshot: ExportSnapshotData) -> bytes:
    results = snapshot.results or {}
    width_analysis = snapshot.width_analysis or {}
    accepted_width = width_analysis.get("accepted_width") or results.get("accepted_width") or {}
    dm = snapshot.dm_optimization or {}
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
        "event_window_start_ms": snapshot.state.get("event_ms", ["", ""])[0],
        "event_window_end_ms": snapshot.state.get("event_ms", ["", ""])[1],
        "spectral_extent_start_mhz": snapshot.state.get("spectral_extent_mhz", ["", ""])[0],
        "spectral_extent_end_mhz": snapshot.state.get("spectral_extent_mhz", ["", ""])[1],
        "peak_positions_ms": _join_list(results.get("peak_positions_ms", [])),
        "measurement_flags": _join_list(results.get("measurement_flags", [])),
        "mask_count": results.get("mask_count", len(snapshot.state.get("masked_channels", []))),
        "masked_channels": _join_list(snapshot.state.get("masked_channels", [])),
        "sefd_jy": snapshot.meta.get("sefd_jy", ""),
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

    buffer = io.BytesIO()
    np.savez_compressed(buffer, **payload)
    return buffer.getvalue()


def _build_plot_figures(
    snapshot: ExportSnapshotData,
) -> list[tuple[str, plt.Figure | None, str | None]]:
    figures: list[tuple[str, plt.Figure | None, str | None]] = [
        ("dynamic_spectrum", _dynamic_spectrum_figure(snapshot), None),
        ("profile_diagnostics", _profile_diagnostics_figure(snapshot), None),
    ]

    if snapshot.temporal_acf.size and snapshot.spectral_acf.size:
        figures.append(("acf_panel", _acf_panel_figure(snapshot), None))
    else:
        figures.append(("acf_panel", None, "acf_diagnostics_unavailable"))

    if snapshot.dm_optimization is None:
        figures.append(("dm_curve", None, "dm_optimization_unavailable"))
        figures.append(("dm_residuals", None, "dm_optimization_unavailable"))
    else:
        figures.append(("dm_curve", _dm_curve_figure(snapshot), None))
        if snapshot.dm_optimization.get("residual_status") == "ok":
            figures.append(("dm_residuals", _dm_residuals_figure(snapshot), None))
        else:
            figures.append(("dm_residuals", None, "residual_diagnostics_unavailable"))
    return figures


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
        cmap="magma",
        vmin=_robust_limits(dynamic)[0],
        vmax=_robust_limits(dynamic)[1],
    )
    ax.axvline(snapshot.event_window_ms[0], color="#e16a1c", linewidth=1.5)
    ax.axvline(snapshot.event_window_ms[1], color="#e16a1c", linewidth=1.5)
    ax.axhline(snapshot.spectral_extent_mhz[0], color="#0f766e", linewidth=1.4, linestyle="--")
    ax.axhline(snapshot.spectral_extent_mhz[1], color="#0f766e", linewidth=1.4, linestyle="--")
    for peak in snapshot.peak_positions_ms:
        ax.axvline(float(peak), color="#c92d2d", linewidth=1.2, linestyle=":")
    ax.set_title("Dynamic Spectrum")
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Frequency (MHz)")
    fig.colorbar(image, ax=ax, pad=0.01, label="Intensity (arb.)")
    return fig


def _profile_diagnostics_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    fig, (ax_time, ax_spec) = plt.subplots(2, 1, figsize=(9.0, 6.4), constrained_layout=True)
    ax_time.plot(snapshot.time_axis_ms, snapshot.time_profile_sn, color="#475569", linewidth=1.5, label="Full-band profile")
    ax_time.plot(snapshot.time_axis_ms, snapshot.burst_only_profile_sn, color="#0f766e", linewidth=1.5, label="Selected-band profile")
    ax_time.axvspan(snapshot.event_window_ms[0], snapshot.event_window_ms[1], color="#f59e0b", alpha=0.14)
    for peak in snapshot.peak_positions_ms:
        ax_time.axvline(float(peak), color="#c92d2d", linewidth=1.2, linestyle=":")
    ax_time.set_title("Profile Diagnostics")
    ax_time.set_ylabel("S/N")
    ax_time.legend(loc="upper right", frameon=False)

    freqs = snapshot.spectral_axis_mhz
    spectrum = snapshot.spectrum_sn
    if freqs.size and freqs[0] > freqs[-1]:
        freqs = freqs[::-1]
        spectrum = spectrum[::-1]
    ax_spec.plot(freqs, spectrum, color="#1d4ed8", linewidth=1.5)
    ax_spec.set_xlabel("Frequency (MHz)")
    ax_spec.set_ylabel("S/N")
    return fig


def _acf_panel_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    fig, (ax_time, ax_spec) = plt.subplots(1, 2, figsize=(9.0, 4.2), constrained_layout=True)
    ax_time.plot(snapshot.temporal_acf_lags_ms, snapshot.temporal_acf, color="#0f766e", linewidth=1.5)
    ax_time.axhline(0.5, color="#a85516", linewidth=1.0, linestyle="--")
    ax_time.set_title("Temporal ACF")
    ax_time.set_xlabel("Lag (ms)")
    ax_time.set_ylabel("Normalized ACF")
    ax_time.set_ylim(-0.05, 1.05)

    ax_spec.plot(snapshot.spectral_acf_lags_mhz, snapshot.spectral_acf, color="#7c3aed", linewidth=1.5)
    ax_spec.axhline(0.5, color="#a85516", linewidth=1.0, linestyle="--")
    ax_spec.set_title("Spectral ACF")
    ax_spec.set_xlabel("Lag (MHz)")
    ax_spec.set_ylabel("Normalized ACF")
    ax_spec.set_ylim(-0.05, 1.05)
    return fig


def _dm_curve_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    optimization = snapshot.dm_optimization or {}
    fig, ax = plt.subplots(figsize=(8.6, 4.8), constrained_layout=True)
    trial_dms = np.asarray(optimization.get("trial_dms", []), dtype=float)
    snr = np.asarray(optimization.get("snr", []), dtype=float)
    ax.plot(trial_dms, snr, color="#1d4ed8", linewidth=1.5, marker="o", markersize=3.5)
    ax.axvline(float(optimization.get("center_dm", np.nan)), color="#64748b", linewidth=1.0, linestyle=":")
    ax.axvline(float(optimization.get("sampled_best_dm", np.nan)), color="#d97706", linewidth=1.2, linestyle="--")
    ax.axvline(float(optimization.get("best_dm", np.nan)), color="#15803d", linewidth=1.4)
    ax.axvline(float(optimization.get("applied_dm", np.nan)), color="#b91c1c", linewidth=1.0, linestyle=":")
    ax.set_title("DM Curve")
    ax.set_xlabel("Dispersion Measure")
    ax.set_ylabel("Integrated Event S/N")
    return fig


def _dm_residuals_figure(snapshot: ExportSnapshotData) -> plt.Figure:
    optimization = snapshot.dm_optimization or {}
    freqs = np.asarray(optimization.get("subband_freqs_mhz", []), dtype=float)
    applied = np.asarray(optimization.get("residuals_applied_ms", []), dtype=float)
    best = np.asarray(optimization.get("residuals_best_ms", []), dtype=float)
    fig, ax = plt.subplots(figsize=(8.6, 4.8), constrained_layout=True)
    ax.plot(freqs, applied, color="#b91c1c", linewidth=1.4, marker="o", markersize=4, label="Applied DM")
    ax.plot(freqs, best, color="#15803d", linewidth=1.4, marker="o", markersize=4, label="Best-fit DM")
    ax.axhline(0.0, color="#64748b", linewidth=1.0, linestyle="--")
    ax.set_title("DM Residuals")
    ax.set_xlabel("Sub-band Center Frequency (MHz)")
    ax.set_ylabel("Residual Arrival Time (ms)")
    ax.legend(frameon=False, loc="best")
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


def _figure_bytes(figure: plt.Figure, fmt: str) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format=fmt, dpi=200, bbox_inches="tight", facecolor="white")
    return buffer.getvalue()


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
