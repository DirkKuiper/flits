from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


def _jsonable_1d(values: np.ndarray, digits: int = 4) -> list[float | None]:
    rounded = np.round(np.asarray(values, dtype=float), digits)
    return [float(value) if np.isfinite(value) else None for value in rounded]


def _jsonable(values: np.ndarray, digits: int = 4) -> list[Any]:
    rounded = np.round(np.asarray(values, dtype=float), digits)
    if rounded.ndim == 1:
        return [float(value) if np.isfinite(value) else None for value in rounded]
    return [
        [float(value) if np.isfinite(value) else None for value in row]
        for row in rounded
    ]


@dataclass(frozen=True)
class FilterbankMetadata:
    source_path: Path
    source_name: str | None
    tsamp: float
    freqres: float
    start_mjd: float
    read_start_sec: float
    sefd_jy: float | None
    bandwidth_mhz: float
    npol: int
    freqs_mhz: np.ndarray
    header_npol: int
    telescope_id: int | None
    machine_id: int | None
    detected_preset_key: str
    detection_basis: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "source_name": self.source_name,
            "tsamp": self.tsamp,
            "freqres": self.freqres,
            "start_mjd": self.start_mjd,
            "read_start_sec": self.read_start_sec,
            "sefd_jy": self.sefd_jy,
            "bandwidth_mhz": self.bandwidth_mhz,
            "npol": self.npol,
            "header_npol": self.header_npol,
            "telescope_id": self.telescope_id,
            "machine_id": self.machine_id,
            "detected_preset_key": self.detected_preset_key,
            "detection_basis": self.detection_basis,
            "freqs_mhz": _jsonable_1d(self.freqs_mhz),
        }


@dataclass(frozen=True)
class GaussianFit1D:
    amp: float
    mu_ms: float
    sigma_ms: float
    offset: float

    def to_dict(self) -> dict[str, float]:
        return {
            "amp": self.amp,
            "mu_ms": self.mu_ms,
            "sigma_ms": self.sigma_ms,
            "offset": self.offset,
        }


@dataclass(frozen=True)
class DmOptimizationResult:
    center_dm: float
    requested_half_range: float
    actual_half_range: float
    step: float
    trial_dms: np.ndarray
    snr: np.ndarray
    snr_metric: str
    applied_dm: float
    sampled_best_dm: float
    sampled_best_sn: float
    best_dm: float
    best_dm_uncertainty: float | None
    best_sn: float
    fit_status: str
    subband_freqs_mhz: np.ndarray
    arrival_times_applied_ms: np.ndarray
    arrival_times_best_ms: np.ndarray
    residuals_applied_ms: np.ndarray
    residuals_best_ms: np.ndarray
    residual_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "center_dm": self.center_dm,
            "requested_half_range": self.requested_half_range,
            "actual_half_range": self.actual_half_range,
            "step": self.step,
            "trial_dms": _jsonable_1d(self.trial_dms, digits=6),
            "snr": _jsonable_1d(self.snr, digits=6),
            "snr_metric": self.snr_metric,
            "applied_dm": self.applied_dm,
            "sampled_best_dm": self.sampled_best_dm,
            "sampled_best_sn": self.sampled_best_sn,
            "best_dm": self.best_dm,
            "best_dm_uncertainty": self.best_dm_uncertainty,
            "best_sn": self.best_sn,
            "fit_status": self.fit_status,
            "subband_freqs_mhz": _jsonable_1d(self.subband_freqs_mhz, digits=6),
            "arrival_times_applied_ms": _jsonable_1d(self.arrival_times_applied_ms, digits=6),
            "arrival_times_best_ms": _jsonable_1d(self.arrival_times_best_ms, digits=6),
            "residuals_applied_ms": _jsonable_1d(self.residuals_applied_ms, digits=6),
            "residuals_best_ms": _jsonable_1d(self.residuals_best_ms, digits=6),
            "residual_status": self.residual_status,
        }


@dataclass(frozen=True)
class MeasurementUncertainties:
    toa_topo_mjd: float | None = None
    snr_peak: float | None = None
    snr_integrated: float | None = None
    width_ms_acf: float | None = None
    spectral_width_mhz_acf: float | None = None
    peak_flux_jy: float | None = None
    fluence_jyms: float | None = None
    iso_e: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "toa_topo_mjd": self.toa_topo_mjd,
            "snr_peak": self.snr_peak,
            "snr_integrated": self.snr_integrated,
            "width_ms_acf": self.width_ms_acf,
            "spectral_width_mhz_acf": self.spectral_width_mhz_acf,
            "peak_flux_jy": self.peak_flux_jy,
            "fluence_jyms": self.fluence_jyms,
            "iso_e": self.iso_e,
        }


@dataclass(frozen=True)
class MeasurementProvenance:
    manual_selection: bool
    peak_selection: str
    width_method: str
    spectral_width_method: str
    calibration_method: str
    energy_unit: str | None
    uncertainty_basis: str
    event_window_ms: list[float]
    spectral_extent_mhz: list[float]
    offpulse_bin_count: int
    burst_bin_count: int
    selected_channel_count: int
    active_channel_count: int
    selected_bandwidth_mhz: float
    effective_bandwidth_mhz: float
    masked_fraction: float
    tsamp_ms: float
    freqres_mhz: float
    npol: int
    sefd_jy: float | None
    low_sn_threshold: float
    heavily_masked_threshold: float
    deprecated_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manual_selection": self.manual_selection,
            "peak_selection": self.peak_selection,
            "width_method": self.width_method,
            "spectral_width_method": self.spectral_width_method,
            "calibration_method": self.calibration_method,
            "energy_unit": self.energy_unit,
            "uncertainty_basis": self.uncertainty_basis,
            "event_window_ms": self.event_window_ms,
            "spectral_extent_mhz": self.spectral_extent_mhz,
            "offpulse_bin_count": self.offpulse_bin_count,
            "burst_bin_count": self.burst_bin_count,
            "selected_channel_count": self.selected_channel_count,
            "active_channel_count": self.active_channel_count,
            "selected_bandwidth_mhz": self.selected_bandwidth_mhz,
            "effective_bandwidth_mhz": self.effective_bandwidth_mhz,
            "masked_fraction": self.masked_fraction,
            "tsamp_ms": self.tsamp_ms,
            "freqres_mhz": self.freqres_mhz,
            "npol": self.npol,
            "sefd_jy": self.sefd_jy,
            "low_sn_threshold": self.low_sn_threshold,
            "heavily_masked_threshold": self.heavily_masked_threshold,
            "deprecated_fields": self.deprecated_fields,
        }


@dataclass(frozen=True)
class MeasurementDiagnostics:
    gaussian_fits: list[GaussianFit1D]
    time_axis_ms: np.ndarray
    time_profile_sn: np.ndarray
    burst_only_profile_sn: np.ndarray
    event_profile_sn: np.ndarray
    spectral_axis_mhz: np.ndarray
    spectrum_sn: np.ndarray
    temporal_acf: np.ndarray
    temporal_acf_lags_ms: np.ndarray
    spectral_acf: np.ndarray
    spectral_acf_lags_mhz: np.ndarray

    def to_dict(self) -> dict[str, Any]:
        return {
            "gaussian_fits": [fit.to_dict() for fit in self.gaussian_fits],
            "time_axis_ms": _jsonable_1d(self.time_axis_ms),
            "time_profile_sn": _jsonable_1d(self.time_profile_sn),
            "burst_only_profile_sn": _jsonable_1d(self.burst_only_profile_sn),
            "event_profile_sn": _jsonable_1d(self.event_profile_sn),
            "spectral_axis_mhz": _jsonable_1d(self.spectral_axis_mhz),
            "spectrum_sn": _jsonable_1d(self.spectrum_sn),
            "temporal_acf": _jsonable_1d(self.temporal_acf),
            "temporal_acf_lags_ms": _jsonable_1d(self.temporal_acf_lags_ms),
            "spectral_acf": _jsonable_1d(self.spectral_acf),
            "spectral_acf_lags_mhz": _jsonable_1d(self.spectral_acf_lags_mhz),
        }


@dataclass(frozen=True)
class BurstMeasurements:
    burst_name: str
    dm: float
    toa_topo_mjd: float | None
    mjd_at_peak: float | None
    peak_positions_ms: list[float]
    snr_peak: float | None
    snr_integrated: float | None
    width_ms_acf: float | None
    spectral_width_mhz_acf: float | None
    peak_flux_jy: float | None
    fluence_jyms: float | None
    iso_e: float | None
    event_duration_ms: float
    spectral_extent_mhz: float
    measurement_flags: list[str]
    uncertainties: MeasurementUncertainties
    provenance: MeasurementProvenance
    diagnostics: MeasurementDiagnostics
    mask_count: int
    masked_channels: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "burst_name": self.burst_name,
            "dm": self.dm,
            "toa_topo_mjd": self.toa_topo_mjd,
            "mjd_at_peak": self.mjd_at_peak,
            "peak_positions_ms": self.peak_positions_ms,
            "snr_peak": self.snr_peak,
            "snr_integrated": self.snr_integrated,
            "width_ms_acf": self.width_ms_acf,
            "spectral_width_mhz_acf": self.spectral_width_mhz_acf,
            "peak_flux_jy": self.peak_flux_jy,
            "fluence_jyms": self.fluence_jyms,
            "iso_e": self.iso_e,
            "event_duration_ms": self.event_duration_ms,
            "spectral_extent_mhz": self.spectral_extent_mhz,
            "measurement_flags": self.measurement_flags,
            "uncertainties": self.uncertainties.to_dict(),
            "provenance": self.provenance.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
            "mask_count": self.mask_count,
            "masked_channels": self.masked_channels,
        }


@dataclass(frozen=True)
class ExportArtifact:
    name: str
    kind: str
    content_type: str
    size_bytes: int | None
    url: str | None
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "url": self.url,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExportManifest:
    export_id: str
    bundle_name: str
    schema_version: str
    created_at_utc: str
    artifacts: list[ExportArtifact]

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "bundle_name": self.bundle_name,
            "schema_version": self.schema_version,
            "created_at_utc": self.created_at_utc,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }
