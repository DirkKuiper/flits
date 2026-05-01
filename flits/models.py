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


def _jsonable_parameter_dict(
    values: dict[str, list[float] | None],
    digits: int = 6,
) -> dict[str, list[float | None] | None]:
    payload: dict[str, list[float | None] | None] = {}
    for key, current in values.items():
        if current is None:
            payload[key] = None
            continue
        rounded = np.round(np.asarray(current, dtype=float), digits)
        payload[key] = [float(value) if np.isfinite(value) else None for value in rounded]
    return payload


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _array_1d(values: Any, *, dtype: np.dtype[Any] | type[np.floating[Any]] | type[np.integer[Any]] = float) -> np.ndarray:
    if values is None:
        return np.array([], dtype=dtype)
    return np.asarray(values, dtype=dtype)


def _array_2d(values: Any, *, dtype: np.dtype[Any] | type[np.floating[Any]] | type[np.integer[Any]] = float) -> np.ndarray:
    if values is None:
        return np.empty((0, 0), dtype=dtype)
    arr = np.asarray(values, dtype=dtype)
    if arr.ndim == 1:
        return arr.reshape(1, -1) if arr.size else np.empty((0, 0), dtype=dtype)
    return arr


FORMAL_UNCERTAINTY_CLASSIFICATIONS: frozenset[str] = frozenset({"formal_1sigma", "model_hessian"})


def _uncertainty_detail_map_to_dict(values: dict[str, "UncertaintyDetail"]) -> dict[str, Any]:
    return {str(key): detail.to_dict() for key, detail in values.items()}


def _uncertainty_detail_map_from_dict(payload: dict[str, Any] | None) -> dict[str, "UncertaintyDetail"]:
    if payload is None:
        return {}
    return {
        str(key): UncertaintyDetail.from_dict(value)
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def compatible_scalar_uncertainty(detail: "UncertaintyDetail | None") -> float | None:
    if detail is None or detail.value is None:
        return None
    if detail.classification not in FORMAL_UNCERTAINTY_CLASSIFICATIONS:
        return None
    return float(detail.value)


@dataclass(frozen=True)
class UncertaintyDetail:
    value: float | None
    units: str | None
    classification: str
    is_formal_1sigma: bool
    publishable: bool
    basis: str
    tooltip: str
    warning_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": _float_or_none(self.value),
            "units": self.units,
            "classification": self.classification,
            "is_formal_1sigma": bool(self.is_formal_1sigma),
            "publishable": bool(self.publishable),
            "basis": self.basis,
            "tooltip": self.tooltip,
            "warning_flags": [str(flag) for flag in self.warning_flags],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UncertaintyDetail":
        return cls(
            value=_float_or_none(payload.get("value")),
            units=None if payload.get("units") in (None, "") else str(payload.get("units")),
            classification=str(payload.get("classification", "diagnostic_only")),
            is_formal_1sigma=bool(payload.get("is_formal_1sigma", False)),
            publishable=bool(payload.get("publishable", False)),
            basis=str(payload.get("basis", "")),
            tooltip=str(payload.get("tooltip", "")),
            warning_flags=[str(flag) for flag in payload.get("warning_flags", [])],
        )


@dataclass(frozen=True)
class BurstRegion:
    start_bin: int
    end_bin: int

    def to_dict(self) -> dict[str, int]:
        return {"start_bin": int(self.start_bin), "end_bin": int(self.end_bin)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BurstRegion":
        return cls(start_bin=int(payload["start_bin"]), end_bin=int(payload["end_bin"]))


@dataclass(frozen=True)
class OffPulseRegion:
    start_bin: int
    end_bin: int

    def to_dict(self) -> dict[str, int]:
        return {"start_bin": int(self.start_bin), "end_bin": int(self.end_bin)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OffPulseRegion":
        return cls(start_bin=int(payload["start_bin"]), end_bin=int(payload["end_bin"]))


@dataclass(frozen=True)
class NoiseEstimateSettings:
    estimator: str = "mean_std"

    def to_dict(self) -> dict[str, str]:
        return {"estimator": self.estimator}

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "NoiseEstimateSettings":
        if payload is None:
            return cls()
        return cls(estimator=str(payload.get("estimator", "mean_std")))


@dataclass(frozen=True)
class NoiseEstimateSummary:
    estimator: str
    basis: str
    baseline: float
    sigma: float
    offpulse_bin_count: int
    offpulse_bins: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    warning_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimator": self.estimator,
            "basis": self.basis,
            "baseline": float(self.baseline),
            "sigma": float(self.sigma),
            "offpulse_bin_count": int(self.offpulse_bin_count),
            "offpulse_bins": [int(value) for value in np.asarray(self.offpulse_bins, dtype=int)],
            "warning_flags": list(self.warning_flags),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NoiseEstimateSummary":
        return cls(
            estimator=str(payload.get("estimator", "mean_std")),
            basis=str(payload.get("basis", "implicit_event_complement")),
            baseline=float(payload.get("baseline", 0.0)),
            sigma=float(payload.get("sigma", 1.0)),
            offpulse_bin_count=int(payload.get("offpulse_bin_count", 0)),
            offpulse_bins=_array_1d(payload.get("offpulse_bins"), dtype=int),
            warning_flags=[str(flag) for flag in payload.get("warning_flags", [])],
        )


@dataclass(frozen=True)
class WidthAnalysisSettings:
    percentile_low: float = 5.0
    percentile_high: float = 95.0
    uncertainty_trials: int = 200
    min_successful_trials: int = 40

    def to_dict(self) -> dict[str, Any]:
        return {
            "percentile_low": float(self.percentile_low),
            "percentile_high": float(self.percentile_high),
            "uncertainty_trials": int(self.uncertainty_trials),
            "min_successful_trials": int(self.min_successful_trials),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "WidthAnalysisSettings":
        if payload is None:
            return cls()
        return cls(
            percentile_low=float(payload.get("percentile_low", 5.0)),
            percentile_high=float(payload.get("percentile_high", 95.0)),
            uncertainty_trials=int(payload.get("uncertainty_trials", 200)),
            min_successful_trials=int(payload.get("min_successful_trials", 40)),
        )


@dataclass(frozen=True)
class WidthResult:
    method: str
    label: str
    value: float | None
    uncertainty: float | None
    units: str
    event_window_ms: list[float]
    spectral_extent_mhz: list[float]
    offpulse_windows_ms: list[list[float]]
    masked_channels: list[int]
    effective_bandwidth_mhz: float | None
    algorithm_name: str
    uncertainty_details: dict[str, UncertaintyDetail] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "label": self.label,
            "value": self.value,
            "uncertainty": self.uncertainty,
            "units": self.units,
            "event_window_ms": [float(value) for value in self.event_window_ms],
            "spectral_extent_mhz": [float(value) for value in self.spectral_extent_mhz],
            "offpulse_windows_ms": [
                [float(value) for value in window] for window in self.offpulse_windows_ms
            ],
            "masked_channels": [int(value) for value in self.masked_channels],
            "effective_bandwidth_mhz": _float_or_none(self.effective_bandwidth_mhz),
            "algorithm_name": self.algorithm_name,
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
            "quality_flags": list(self.quality_flags),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WidthResult":
        return cls(
            method=str(payload["method"]),
            label=str(payload.get("label", payload["method"])),
            value=_float_or_none(payload.get("value")),
            uncertainty=_float_or_none(payload.get("uncertainty")),
            units=str(payload.get("units", "ms")),
            event_window_ms=[float(value) for value in payload.get("event_window_ms", [])],
            spectral_extent_mhz=[float(value) for value in payload.get("spectral_extent_mhz", [])],
            offpulse_windows_ms=[
                [float(value) for value in window] for window in payload.get("offpulse_windows_ms", [])
            ],
            masked_channels=[int(value) for value in payload.get("masked_channels", [])],
            effective_bandwidth_mhz=_float_or_none(payload.get("effective_bandwidth_mhz")),
            algorithm_name=str(payload.get("algorithm_name", payload.get("method", "unknown"))),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
            quality_flags=[str(flag) for flag in payload.get("quality_flags", [])],
        )


@dataclass(frozen=True)
class AcceptedWidthSelection:
    method: str
    value: float | None
    uncertainty: float | None
    units: str
    uncertainty_detail: UncertaintyDetail | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "value": self.value,
            "uncertainty": self.uncertainty,
            "units": self.units,
            "uncertainty_detail": None if self.uncertainty_detail is None else self.uncertainty_detail.to_dict(),
        }

    @classmethod
    def from_result(cls, result: WidthResult) -> "AcceptedWidthSelection":
        return cls(
            method=result.method,
            value=result.value,
            uncertainty=result.uncertainty,
            units=result.units,
            uncertainty_detail=result.uncertainty_details.get("uncertainty"),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AcceptedWidthSelection":
        return cls(
            method=str(payload["method"]),
            value=_float_or_none(payload.get("value")),
            uncertainty=_float_or_none(payload.get("uncertainty")),
            units=str(payload.get("units", "ms")),
            uncertainty_detail=(
                None
                if payload.get("uncertainty_detail") is None
                else UncertaintyDetail.from_dict(payload["uncertainty_detail"])
            ),
        )


@dataclass(frozen=True)
class WidthAnalysisSummary:
    settings: WidthAnalysisSettings
    results: list[WidthResult]
    accepted_width: AcceptedWidthSelection | None
    noise_summary: NoiseEstimateSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings": self.settings.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "accepted_width": None if self.accepted_width is None else self.accepted_width.to_dict(),
            "noise_summary": self.noise_summary.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WidthAnalysisSummary":
        return cls(
            settings=WidthAnalysisSettings.from_dict(payload.get("settings")),
            results=[WidthResult.from_dict(item) for item in payload.get("results", [])],
            accepted_width=(
                None
                if payload.get("accepted_width") is None
                else AcceptedWidthSelection.from_dict(payload["accepted_width"])
            ),
            noise_summary=NoiseEstimateSummary.from_dict(payload.get("noise_summary", {})),
        )


@dataclass(frozen=True)
class DmMetricReference:
    label: str
    citation: str
    url: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "citation": self.citation,
            "url": self.url,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DmMetricReference":
        return cls(
            label=str(payload.get("label", "")),
            citation=str(payload.get("citation", "")),
            url=str(payload.get("url", "")),
            note=None if payload.get("note") in (None, "") else str(payload.get("note")),
        )


@dataclass(frozen=True)
class DmMetricDefinition:
    key: str
    label: str
    summary: str
    formula: str
    origin: str
    references: list[DmMetricReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "summary": self.summary,
            "formula": self.formula,
            "origin": self.origin,
            "references": [item.to_dict() for item in self.references],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DmMetricDefinition":
        return cls(
            key=str(payload.get("key", "")),
            label=str(payload.get("label", payload.get("key", ""))),
            summary=str(payload.get("summary", "")),
            formula=str(payload.get("formula", "")),
            origin=str(payload.get("origin", "unspecified")),
            references=[DmMetricReference.from_dict(item) for item in payload.get("references", [])],
        )


@dataclass(frozen=True)
class DmOptimizationSettings:
    center_dm: float
    half_range: float
    step: float
    metric: str = "integrated_event_snr"

    def to_dict(self) -> dict[str, Any]:
        return {
            "center_dm": float(self.center_dm),
            "half_range": float(self.half_range),
            "step": float(self.step),
            "metric": self.metric,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DmOptimizationSettings":
        return cls(
            center_dm=float(payload.get("center_dm", 0.0)),
            half_range=float(payload.get("half_range", payload.get("requested_half_range", 0.0))),
            step=float(payload.get("step", 0.0)),
            metric=str(payload.get("metric", "integrated_event_snr")),
        )


@dataclass(frozen=True)
class DmOptimizationProvenance:
    event_window_ms: list[float]
    spectral_extent_mhz: list[float]
    offpulse_windows_ms: list[list[float]]
    masked_channels: list[int]
    effective_bandwidth_mhz: float | None
    tsamp_ms: float
    freqres_mhz: float
    algorithm_name: str
    warning_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_window_ms": [float(value) for value in self.event_window_ms],
            "spectral_extent_mhz": [float(value) for value in self.spectral_extent_mhz],
            "offpulse_windows_ms": [
                [float(value) for value in window] for window in self.offpulse_windows_ms
            ],
            "masked_channels": [int(value) for value in self.masked_channels],
            "effective_bandwidth_mhz": _float_or_none(self.effective_bandwidth_mhz),
            "tsamp_ms": float(self.tsamp_ms),
            "freqres_mhz": float(self.freqres_mhz),
            "algorithm_name": self.algorithm_name,
            "warning_flags": list(self.warning_flags),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "DmOptimizationProvenance":
        if payload is None:
            return cls(
                event_window_ms=[],
                spectral_extent_mhz=[],
                offpulse_windows_ms=[],
                masked_channels=[],
                effective_bandwidth_mhz=None,
                tsamp_ms=0.0,
                freqres_mhz=0.0,
                algorithm_name="dm_trial_sweep",
            )
        return cls(
            event_window_ms=[float(value) for value in payload.get("event_window_ms", [])],
            spectral_extent_mhz=[float(value) for value in payload.get("spectral_extent_mhz", [])],
            offpulse_windows_ms=[
                [float(value) for value in window] for window in payload.get("offpulse_windows_ms", [])
            ],
            masked_channels=[int(value) for value in payload.get("masked_channels", [])],
            effective_bandwidth_mhz=_float_or_none(payload.get("effective_bandwidth_mhz")),
            tsamp_ms=float(payload.get("tsamp_ms", 0.0)),
            freqres_mhz=float(payload.get("freqres_mhz", 0.0)),
            algorithm_name=str(payload.get("algorithm_name", "dm_trial_sweep")),
            warning_flags=[str(flag) for flag in payload.get("warning_flags", [])],
        )


@dataclass(frozen=True)
class DmComponentOptimizationResult:
    component_id: str
    label: str
    event_window_ms: list[float]
    trial_dms: np.ndarray
    metric_values: np.ndarray
    metric: str
    sampled_best_dm: float
    sampled_best_value: float
    best_dm: float
    best_dm_uncertainty: float | None
    best_value: float
    fit_status: str
    uncertainty_details: dict[str, UncertaintyDetail] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "label": self.label,
            "event_window_ms": [float(value) for value in self.event_window_ms],
            "trial_dms": _jsonable_1d(self.trial_dms, digits=6),
            "metric_values": _jsonable_1d(self.metric_values, digits=6),
            "metric": self.metric,
            "sampled_best_dm": float(self.sampled_best_dm),
            "sampled_best_value": float(self.sampled_best_value),
            "best_dm": float(self.best_dm),
            "best_dm_uncertainty": _float_or_none(self.best_dm_uncertainty),
            "best_value": float(self.best_value),
            "fit_status": self.fit_status,
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DmComponentOptimizationResult":
        return cls(
            component_id=str(payload.get("component_id", "component")),
            label=str(payload.get("label", payload.get("component_id", "Component"))),
            event_window_ms=[float(value) for value in payload.get("event_window_ms", [])],
            trial_dms=_array_1d(payload.get("trial_dms"), dtype=float),
            metric_values=_array_1d(payload.get("metric_values", payload.get("snr")), dtype=float),
            metric=str(payload.get("metric", payload.get("snr_metric", "integrated_event_snr"))),
            sampled_best_dm=float(payload.get("sampled_best_dm", payload.get("best_dm", 0.0))),
            sampled_best_value=float(payload.get("sampled_best_value", payload.get("sampled_best_sn", 0.0))),
            best_dm=float(payload.get("best_dm", 0.0)),
            best_dm_uncertainty=_float_or_none(payload.get("best_dm_uncertainty")),
            best_value=float(payload.get("best_value", payload.get("best_sn", 0.0))),
            fit_status=str(payload.get("fit_status", "unknown")),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
        )


@dataclass(frozen=True)
class SessionSourceRef:
    source_path: Path
    source_name: str | None
    file_size_bytes: int
    mtime_unix: float
    shape: list[int]
    tsamp: float
    freqres: float
    start_mjd: float
    npol: int
    freq_range_mhz: list[float]
    file_name: str | None = None
    data_dir_relative_path: str | None = None
    content_hash_algorithm: str | None = None
    content_hash_sha256: str | None = None
    header_npol: int | None = None
    polarization_order: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "source_name": self.source_name,
            "file_size_bytes": int(self.file_size_bytes),
            "mtime_unix": float(self.mtime_unix),
            "shape": [int(value) for value in self.shape],
            "tsamp": float(self.tsamp),
            "freqres": float(self.freqres),
            "start_mjd": float(self.start_mjd),
            "npol": int(self.npol),
            "header_npol": None if self.header_npol is None else int(self.header_npol),
            "polarization_order": self.polarization_order,
            "freq_range_mhz": [float(value) for value in self.freq_range_mhz],
            "file_name": self.file_name,
            "data_dir_relative_path": self.data_dir_relative_path,
            "content_hash_algorithm": self.content_hash_algorithm,
            "content_hash_sha256": self.content_hash_sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionSourceRef":
        source_path = Path(payload["source_path"])
        return cls(
            source_path=source_path,
            source_name=payload.get("source_name"),
            file_size_bytes=int(payload.get("file_size_bytes", 0)),
            mtime_unix=float(payload.get("mtime_unix", 0.0)),
            shape=[int(value) for value in payload.get("shape", [])],
            tsamp=float(payload.get("tsamp", 0.0)),
            freqres=float(payload.get("freqres", 0.0)),
            start_mjd=float(payload.get("start_mjd", 0.0)),
            npol=int(payload.get("npol", 0)),
            freq_range_mhz=[float(value) for value in payload.get("freq_range_mhz", [])],
            file_name=payload.get("file_name") or source_path.name or None,
            data_dir_relative_path=payload.get("data_dir_relative_path"),
            content_hash_algorithm=payload.get("content_hash_algorithm"),
            content_hash_sha256=payload.get("content_hash_sha256"),
            header_npol=_int_or_none(payload.get("header_npol")),
            polarization_order=payload.get("polarization_order"),
        )


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
    polarization_order: str | None = None
    source_ra_deg: float | None = None
    source_dec_deg: float | None = None
    source_position_frame: str = "icrs"
    source_position_basis: str | None = None
    time_scale: str = "utc"
    time_reference_frame: str = "topocentric"
    barycentric_header_flag: bool | None = None
    pulsarcentric_header_flag: bool | None = None
    dedispersion_reference_frequency_mhz: float | None = None
    dedispersion_reference_basis: str | None = None

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
            "polarization_order": self.polarization_order,
            "telescope_id": self.telescope_id,
            "machine_id": self.machine_id,
            "detected_preset_key": self.detected_preset_key,
            "detection_basis": self.detection_basis,
            "freqs_mhz": _jsonable_1d(self.freqs_mhz),
            "source_ra_deg": _float_or_none(self.source_ra_deg),
            "source_dec_deg": _float_or_none(self.source_dec_deg),
            "source_position_frame": self.source_position_frame,
            "source_position_basis": self.source_position_basis,
            "time_scale": self.time_scale,
            "time_reference_frame": self.time_reference_frame,
            "barycentric_header_flag": _bool_or_none(self.barycentric_header_flag),
            "pulsarcentric_header_flag": _bool_or_none(self.pulsarcentric_header_flag),
            "dedispersion_reference_frequency_mhz": _float_or_none(self.dedispersion_reference_frequency_mhz),
            "dedispersion_reference_basis": self.dedispersion_reference_basis,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FilterbankMetadata":
        return cls(
            source_path=Path(payload["source_path"]),
            source_name=payload.get("source_name"),
            tsamp=float(payload["tsamp"]),
            freqres=float(payload["freqres"]),
            start_mjd=float(payload["start_mjd"]),
            read_start_sec=float(payload["read_start_sec"]),
            sefd_jy=_float_or_none(payload.get("sefd_jy")),
            bandwidth_mhz=float(payload["bandwidth_mhz"]),
            npol=int(payload["npol"]),
            freqs_mhz=_array_1d(payload.get("freqs_mhz"), dtype=float),
            header_npol=int(payload["header_npol"]),
            polarization_order=payload.get("polarization_order"),
            telescope_id=_int_or_none(payload.get("telescope_id")),
            machine_id=_int_or_none(payload.get("machine_id")),
            detected_preset_key=str(payload["detected_preset_key"]),
            detection_basis=str(payload["detection_basis"]),
            source_ra_deg=_float_or_none(payload.get("source_ra_deg")),
            source_dec_deg=_float_or_none(payload.get("source_dec_deg")),
            source_position_frame=str(payload.get("source_position_frame", "icrs")),
            source_position_basis=payload.get("source_position_basis"),
            time_scale=str(payload.get("time_scale", "utc")),
            time_reference_frame=str(payload.get("time_reference_frame", "topocentric")),
            barycentric_header_flag=_bool_or_none(payload.get("barycentric_header_flag")),
            pulsarcentric_header_flag=_bool_or_none(payload.get("pulsarcentric_header_flag")),
            dedispersion_reference_frequency_mhz=_float_or_none(
                payload.get("dedispersion_reference_frequency_mhz")
            ),
            dedispersion_reference_basis=payload.get("dedispersion_reference_basis"),
        )


@dataclass(frozen=True)
class AutoMaskRunSummary:
    profile: str
    profile_label: str
    memory_budget_mb: int
    candidate_time_bins: int
    sampled_time_bins: int
    eligible_channels: int
    constant_channel_count: int
    detected_channel_count: int
    added_channel_count: int
    test_used: str | None
    tests_tried: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "profile_label": self.profile_label,
            "memory_budget_mb": self.memory_budget_mb,
            "candidate_time_bins": self.candidate_time_bins,
            "sampled_time_bins": self.sampled_time_bins,
            "eligible_channels": self.eligible_channels,
            "constant_channel_count": self.constant_channel_count,
            "detected_channel_count": self.detected_channel_count,
            "added_channel_count": self.added_channel_count,
            "test_used": self.test_used,
            "tests_tried": list(self.tests_tried),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AutoMaskRunSummary | None":
        if payload is None:
            return None
        return cls(
            profile=str(payload.get("profile", "auto")),
            profile_label=str(payload.get("profile_label", "Auto")),
            memory_budget_mb=int(payload.get("memory_budget_mb", 0)),
            candidate_time_bins=int(payload.get("candidate_time_bins", 0)),
            sampled_time_bins=int(payload.get("sampled_time_bins", 0)),
            eligible_channels=int(payload.get("eligible_channels", 0)),
            constant_channel_count=int(payload.get("constant_channel_count", 0)),
            detected_channel_count=int(payload.get("detected_channel_count", 0)),
            added_channel_count=int(payload.get("added_channel_count", 0)),
            test_used=payload.get("test_used"),
            tests_tried=tuple(str(value) for value in payload.get("tests_tried", [])),
        )


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GaussianFit1D":
        return cls(
            amp=float(payload["amp"]),
            mu_ms=float(payload["mu_ms"]),
            sigma_ms=float(payload["sigma_ms"]),
            offset=float(payload["offset"]),
        )


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
    residual_rms_applied_ms: float | None = None
    residual_rms_best_ms: float | None = None
    residual_slope_applied_ms_per_mhz: float | None = None
    residual_slope_best_ms_per_mhz: float | None = None
    uncertainty_details: dict[str, UncertaintyDetail] = field(default_factory=dict)
    component_results: list[DmComponentOptimizationResult] = field(default_factory=list)
    settings: DmOptimizationSettings | None = None
    provenance: DmOptimizationProvenance | None = None

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
            "residual_rms_applied_ms": _float_or_none(self.residual_rms_applied_ms),
            "residual_rms_best_ms": _float_or_none(self.residual_rms_best_ms),
            "residual_slope_applied_ms_per_mhz": _float_or_none(self.residual_slope_applied_ms_per_mhz),
            "residual_slope_best_ms_per_mhz": _float_or_none(self.residual_slope_best_ms_per_mhz),
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
            "component_results": [item.to_dict() for item in self.component_results],
            "settings": None if self.settings is None else self.settings.to_dict(),
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DmOptimizationResult":
        return cls(
            center_dm=float(payload["center_dm"]),
            requested_half_range=float(payload.get("requested_half_range", payload.get("actual_half_range", 0.0))),
            actual_half_range=float(payload.get("actual_half_range", payload.get("requested_half_range", 0.0))),
            step=float(payload["step"]),
            trial_dms=_array_1d(payload.get("trial_dms"), dtype=float),
            snr=_array_1d(payload.get("snr"), dtype=float),
            snr_metric=str(payload.get("snr_metric", "integrated_event_snr")),
            applied_dm=float(payload.get("applied_dm", payload.get("center_dm", 0.0))),
            sampled_best_dm=float(payload.get("sampled_best_dm", payload.get("best_dm", 0.0))),
            sampled_best_sn=float(payload.get("sampled_best_sn", payload.get("best_sn", 0.0))),
            best_dm=float(payload["best_dm"]),
            best_dm_uncertainty=_float_or_none(payload.get("best_dm_uncertainty")),
            best_sn=float(payload.get("best_sn", 0.0)),
            fit_status=str(payload.get("fit_status", "unknown")),
            subband_freqs_mhz=_array_1d(payload.get("subband_freqs_mhz"), dtype=float),
            arrival_times_applied_ms=_array_1d(payload.get("arrival_times_applied_ms"), dtype=float),
            arrival_times_best_ms=_array_1d(payload.get("arrival_times_best_ms"), dtype=float),
            residuals_applied_ms=_array_1d(payload.get("residuals_applied_ms"), dtype=float),
            residuals_best_ms=_array_1d(payload.get("residuals_best_ms"), dtype=float),
            residual_status=str(payload.get("residual_status", "unknown")),
            residual_rms_applied_ms=_float_or_none(payload.get("residual_rms_applied_ms")),
            residual_rms_best_ms=_float_or_none(payload.get("residual_rms_best_ms")),
            residual_slope_applied_ms_per_mhz=_float_or_none(payload.get("residual_slope_applied_ms_per_mhz")),
            residual_slope_best_ms_per_mhz=_float_or_none(payload.get("residual_slope_best_ms_per_mhz")),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
            component_results=[
                DmComponentOptimizationResult.from_dict(item)
                for item in payload.get("component_results", [])
            ],
            settings=(
                None if payload.get("settings") is None else DmOptimizationSettings.from_dict(payload["settings"])
            ),
            provenance=(
                None
                if payload.get("provenance") is None
                else DmOptimizationProvenance.from_dict(payload["provenance"])
            ),
        )


@dataclass(frozen=True)
class SpectralAnalysisResult:
    status: str
    message: str | None
    segment_length_ms: float | None
    segment_bins: int | None
    segment_count: int | None
    normalization: str
    event_window_ms: list[float]
    spectral_extent_mhz: list[float]
    tsamp_ms: float
    frequency_resolution_hz: float | None
    nyquist_hz: float | None
    freq_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    power_law_a: float | None = None
    power_law_alpha: float | None = None
    power_law_c: float | None = None
    power_law_a_err: float | None = None
    power_law_alpha_err: float | None = None
    power_law_c_err: float | None = None
    crossover_frequency_hz: float | None = None
    crossover_frequency_status: str = "unavailable"
    crossover_frequency_hz_3sigma_low: float | None = None
    crossover_frequency_hz_3sigma_high: float | None = None
    noise_psd_freq_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    noise_psd_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    noise_psd_segment_count: int | None = None
    uncertainty_details: dict[str, UncertaintyDetail] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "segment_length_ms": _float_or_none(self.segment_length_ms),
            "segment_bins": _int_or_none(self.segment_bins),
            "segment_count": _int_or_none(self.segment_count),
            "normalization": self.normalization,
            "event_window_ms": [float(value) for value in self.event_window_ms],
            "spectral_extent_mhz": [float(value) for value in self.spectral_extent_mhz],
            "tsamp_ms": float(self.tsamp_ms),
            "frequency_resolution_hz": _float_or_none(self.frequency_resolution_hz),
            "nyquist_hz": _float_or_none(self.nyquist_hz),
            "freq_hz": _jsonable_1d(self.freq_hz, digits=6),
            "power": _jsonable_1d(self.power, digits=6),
            "power_law_a": _float_or_none(self.power_law_a),
            "power_law_alpha": _float_or_none(self.power_law_alpha),
            "power_law_c": _float_or_none(self.power_law_c),
            "power_law_a_err": _float_or_none(self.power_law_a_err),
            "power_law_alpha_err": _float_or_none(self.power_law_alpha_err),
            "power_law_c_err": _float_or_none(self.power_law_c_err),
            "crossover_frequency_hz": _float_or_none(self.crossover_frequency_hz),
            "crossover_frequency_status": self.crossover_frequency_status,
            "crossover_frequency_hz_3sigma_low": _float_or_none(self.crossover_frequency_hz_3sigma_low),
            "crossover_frequency_hz_3sigma_high": _float_or_none(self.crossover_frequency_hz_3sigma_high),
            "noise_psd_freq_hz": _jsonable_1d(self.noise_psd_freq_hz, digits=6),
            "noise_psd_power": _jsonable_1d(self.noise_psd_power, digits=6),
            "noise_psd_segment_count": _int_or_none(self.noise_psd_segment_count),
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SpectralAnalysisResult | None":
        if payload is None:
            return None
        return cls(
            status=str(payload.get("status", "unknown")),
            message=None if payload.get("message") in (None, "") else str(payload.get("message")),
            segment_length_ms=_float_or_none(payload.get("segment_length_ms")),
            segment_bins=_int_or_none(payload.get("segment_bins")),
            segment_count=_int_or_none(payload.get("segment_count")),
            normalization=str(payload.get("normalization", "none")),
            event_window_ms=[float(value) for value in payload.get("event_window_ms", [])],
            spectral_extent_mhz=[float(value) for value in payload.get("spectral_extent_mhz", [])],
            tsamp_ms=float(payload.get("tsamp_ms", 0.0)),
            frequency_resolution_hz=_float_or_none(payload.get("frequency_resolution_hz")),
            nyquist_hz=_float_or_none(payload.get("nyquist_hz")),
            freq_hz=_array_1d(payload.get("freq_hz"), dtype=float),
            power=_array_1d(payload.get("power"), dtype=float),
            power_law_a=_float_or_none(payload.get("power_law_a")),
            power_law_alpha=_float_or_none(payload.get("power_law_alpha")),
            power_law_c=_float_or_none(payload.get("power_law_c")),
            power_law_a_err=_float_or_none(payload.get("power_law_a_err")),
            power_law_alpha_err=_float_or_none(payload.get("power_law_alpha_err")),
            power_law_c_err=_float_or_none(payload.get("power_law_c_err")),
            crossover_frequency_hz=_float_or_none(payload.get("crossover_frequency_hz")),
            crossover_frequency_status=str(payload.get("crossover_frequency_status", "unavailable")),
            crossover_frequency_hz_3sigma_low=_float_or_none(payload.get("crossover_frequency_hz_3sigma_low")),
            crossover_frequency_hz_3sigma_high=_float_or_none(payload.get("crossover_frequency_hz_3sigma_high")),
            noise_psd_freq_hz=_array_1d(payload.get("noise_psd_freq_hz"), dtype=float),
            noise_psd_power=_array_1d(payload.get("noise_psd_power"), dtype=float),
            noise_psd_segment_count=_int_or_none(payload.get("noise_psd_segment_count")),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
        )


@dataclass(frozen=True)
class TemporalStructureResult:
    status: str
    message: str | None
    segment_length_ms: float | None
    segment_bins: int | None
    segment_count: int | None
    normalization: str
    event_window_ms: list[float]
    spectral_extent_mhz: list[float]
    tsamp_ms: float
    frequency_resolution_hz: float | None
    nyquist_hz: float | None
    min_structure_ms_primary: float | None = None
    min_structure_ms_wavelet: float | None = None
    fitburst_min_component_ms: float | None = None
    power_law_fit_status: str = "unavailable"
    power_law_fit_message: str | None = None
    raw_periodogram_freq_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    raw_periodogram_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    averaged_psd_freq_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    averaged_psd_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    matched_filter_scales_ms: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    matched_filter_boxcar_sigma: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    matched_filter_gaussian_sigma: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    matched_filter_threshold_sigma: float | None = None
    wavelet_scales_ms: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    wavelet_sigma: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    wavelet_threshold_sigma: float | None = None
    power_law_a: float | None = None
    power_law_alpha: float | None = None
    power_law_c: float | None = None
    power_law_a_err: float | None = None
    power_law_alpha_err: float | None = None
    power_law_c_err: float | None = None
    crossover_frequency_hz: float | None = None
    crossover_frequency_status: str = "unavailable"
    crossover_frequency_hz_3sigma_low: float | None = None
    crossover_frequency_hz_3sigma_high: float | None = None
    noise_psd_freq_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    noise_psd_power: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    noise_psd_segment_count: int | None = None
    uncertainty_details: dict[str, UncertaintyDetail] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "segment_length_ms": _float_or_none(self.segment_length_ms),
            "segment_bins": _int_or_none(self.segment_bins),
            "segment_count": _int_or_none(self.segment_count),
            "normalization": self.normalization,
            "event_window_ms": [float(value) for value in self.event_window_ms],
            "spectral_extent_mhz": [float(value) for value in self.spectral_extent_mhz],
            "tsamp_ms": float(self.tsamp_ms),
            "frequency_resolution_hz": _float_or_none(self.frequency_resolution_hz),
            "nyquist_hz": _float_or_none(self.nyquist_hz),
            "min_structure_ms_primary": _float_or_none(self.min_structure_ms_primary),
            "min_structure_ms_wavelet": _float_or_none(self.min_structure_ms_wavelet),
            "fitburst_min_component_ms": _float_or_none(self.fitburst_min_component_ms),
            "power_law_fit_status": self.power_law_fit_status,
            "power_law_fit_message": self.power_law_fit_message,
            "raw_periodogram_freq_hz": _jsonable_1d(self.raw_periodogram_freq_hz, digits=6),
            "raw_periodogram_power": _jsonable_1d(self.raw_periodogram_power, digits=6),
            "averaged_psd_freq_hz": _jsonable_1d(self.averaged_psd_freq_hz, digits=6),
            "averaged_psd_power": _jsonable_1d(self.averaged_psd_power, digits=6),
            "matched_filter_scales_ms": _jsonable_1d(self.matched_filter_scales_ms, digits=6),
            "matched_filter_boxcar_sigma": _jsonable_1d(self.matched_filter_boxcar_sigma, digits=6),
            "matched_filter_gaussian_sigma": _jsonable_1d(self.matched_filter_gaussian_sigma, digits=6),
            "matched_filter_threshold_sigma": _float_or_none(self.matched_filter_threshold_sigma),
            "wavelet_scales_ms": _jsonable_1d(self.wavelet_scales_ms, digits=6),
            "wavelet_sigma": _jsonable_1d(self.wavelet_sigma, digits=6),
            "wavelet_threshold_sigma": _float_or_none(self.wavelet_threshold_sigma),
            "power_law_a": _float_or_none(self.power_law_a),
            "power_law_alpha": _float_or_none(self.power_law_alpha),
            "power_law_c": _float_or_none(self.power_law_c),
            "power_law_a_err": _float_or_none(self.power_law_a_err),
            "power_law_alpha_err": _float_or_none(self.power_law_alpha_err),
            "power_law_c_err": _float_or_none(self.power_law_c_err),
            "crossover_frequency_hz": _float_or_none(self.crossover_frequency_hz),
            "crossover_frequency_status": self.crossover_frequency_status,
            "crossover_frequency_hz_3sigma_low": _float_or_none(self.crossover_frequency_hz_3sigma_low),
            "crossover_frequency_hz_3sigma_high": _float_or_none(self.crossover_frequency_hz_3sigma_high),
            "noise_psd_freq_hz": _jsonable_1d(self.noise_psd_freq_hz, digits=6),
            "noise_psd_power": _jsonable_1d(self.noise_psd_power, digits=6),
            "noise_psd_segment_count": _int_or_none(self.noise_psd_segment_count),
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "TemporalStructureResult | None":
        if payload is None:
            return None
        return cls(
            status=str(payload.get("status", "unknown")),
            message=None if payload.get("message") in (None, "") else str(payload.get("message")),
            segment_length_ms=_float_or_none(payload.get("segment_length_ms")),
            segment_bins=_int_or_none(payload.get("segment_bins")),
            segment_count=_int_or_none(payload.get("segment_count")),
            normalization=str(payload.get("normalization", "none")),
            event_window_ms=[float(value) for value in payload.get("event_window_ms", [])],
            spectral_extent_mhz=[float(value) for value in payload.get("spectral_extent_mhz", [])],
            tsamp_ms=float(payload.get("tsamp_ms", 0.0)),
            frequency_resolution_hz=_float_or_none(payload.get("frequency_resolution_hz")),
            nyquist_hz=_float_or_none(payload.get("nyquist_hz")),
            min_structure_ms_primary=_float_or_none(payload.get("min_structure_ms_primary")),
            min_structure_ms_wavelet=_float_or_none(payload.get("min_structure_ms_wavelet")),
            fitburst_min_component_ms=_float_or_none(payload.get("fitburst_min_component_ms")),
            power_law_fit_status=str(payload.get("power_law_fit_status", "unavailable")),
            power_law_fit_message=None
            if payload.get("power_law_fit_message") in (None, "")
            else str(payload.get("power_law_fit_message")),
            raw_periodogram_freq_hz=_array_1d(payload.get("raw_periodogram_freq_hz"), dtype=float),
            raw_periodogram_power=_array_1d(payload.get("raw_periodogram_power"), dtype=float),
            averaged_psd_freq_hz=_array_1d(payload.get("averaged_psd_freq_hz"), dtype=float),
            averaged_psd_power=_array_1d(payload.get("averaged_psd_power"), dtype=float),
            matched_filter_scales_ms=_array_1d(payload.get("matched_filter_scales_ms"), dtype=float),
            matched_filter_boxcar_sigma=_array_1d(payload.get("matched_filter_boxcar_sigma"), dtype=float),
            matched_filter_gaussian_sigma=_array_1d(payload.get("matched_filter_gaussian_sigma"), dtype=float),
            matched_filter_threshold_sigma=_float_or_none(payload.get("matched_filter_threshold_sigma")),
            wavelet_scales_ms=_array_1d(payload.get("wavelet_scales_ms"), dtype=float),
            wavelet_sigma=_array_1d(payload.get("wavelet_sigma"), dtype=float),
            wavelet_threshold_sigma=_float_or_none(payload.get("wavelet_threshold_sigma")),
            power_law_a=_float_or_none(payload.get("power_law_a")),
            power_law_alpha=_float_or_none(payload.get("power_law_alpha")),
            power_law_c=_float_or_none(payload.get("power_law_c")),
            power_law_a_err=_float_or_none(payload.get("power_law_a_err")),
            power_law_alpha_err=_float_or_none(payload.get("power_law_alpha_err")),
            power_law_c_err=_float_or_none(payload.get("power_law_c_err")),
            crossover_frequency_hz=_float_or_none(payload.get("crossover_frequency_hz")),
            crossover_frequency_status=str(payload.get("crossover_frequency_status", "unavailable")),
            crossover_frequency_hz_3sigma_low=_float_or_none(payload.get("crossover_frequency_hz_3sigma_low")),
            crossover_frequency_hz_3sigma_high=_float_or_none(payload.get("crossover_frequency_hz_3sigma_high")),
            noise_psd_freq_hz=_array_1d(payload.get("noise_psd_freq_hz"), dtype=float),
            noise_psd_power=_array_1d(payload.get("noise_psd_power"), dtype=float),
            noise_psd_segment_count=_int_or_none(payload.get("noise_psd_segment_count")),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
        )


@dataclass(frozen=True)
class MeasurementUncertainties:
    toa_peak_topo_mjd: float | None = None
    toa_topo_mjd: float | None = None
    toa_inf_topo_mjd: float | None = None
    toa_inf_bary_mjd_tdb: float | None = None
    snr_peak: float | None = None
    snr_integrated: float | None = None
    width_ms_acf: float | None = None
    width_ms_model: float | None = None
    spectral_width_mhz_acf: float | None = None
    tau_sc_ms: float | None = None
    peak_flux_jy: float | None = None
    fluence_jyms: float | None = None
    iso_e: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "toa_peak_topo_mjd": self.toa_peak_topo_mjd,
            "toa_topo_mjd": self.toa_topo_mjd,
            "toa_inf_topo_mjd": self.toa_inf_topo_mjd,
            "toa_inf_bary_mjd_tdb": self.toa_inf_bary_mjd_tdb,
            "snr_peak": self.snr_peak,
            "snr_integrated": self.snr_integrated,
            "width_ms_acf": self.width_ms_acf,
            "width_ms_model": self.width_ms_model,
            "spectral_width_mhz_acf": self.spectral_width_mhz_acf,
            "tau_sc_ms": self.tau_sc_ms,
            "peak_flux_jy": self.peak_flux_jy,
            "fluence_jyms": self.fluence_jyms,
            "iso_e": self.iso_e,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "MeasurementUncertainties":
        if payload is None:
            return cls()
        return cls(
            toa_peak_topo_mjd=_float_or_none(
                payload.get("toa_peak_topo_mjd", payload.get("toa_topo_mjd"))
            ),
            toa_topo_mjd=_float_or_none(payload.get("toa_topo_mjd")),
            toa_inf_topo_mjd=_float_or_none(payload.get("toa_inf_topo_mjd")),
            toa_inf_bary_mjd_tdb=_float_or_none(payload.get("toa_inf_bary_mjd_tdb")),
            snr_peak=_float_or_none(payload.get("snr_peak")),
            snr_integrated=_float_or_none(payload.get("snr_integrated")),
            width_ms_acf=_float_or_none(payload.get("width_ms_acf")),
            width_ms_model=_float_or_none(payload.get("width_ms_model")),
            spectral_width_mhz_acf=_float_or_none(payload.get("spectral_width_mhz_acf")),
            tau_sc_ms=_float_or_none(payload.get("tau_sc_ms")),
            peak_flux_jy=_float_or_none(payload.get("peak_flux_jy")),
            fluence_jyms=_float_or_none(payload.get("fluence_jyms")),
            iso_e=_float_or_none(payload.get("iso_e")),
        )


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
    offpulse_windows_ms: list[list[float]]
    offpulse_bin_count: int
    burst_bin_count: int
    selected_channel_count: int
    active_channel_count: int
    selected_bandwidth_mhz: float
    effective_bandwidth_mhz: float
    masked_fraction: float
    masked_channels: list[int]
    tsamp_ms: float
    freqres_mhz: float
    npol: int
    sefd_jy: float | None
    calibration_assumptions: list[str]
    noise_basis: str
    noise_estimator: str
    algorithm_name: str
    warning_flags: list[str]
    low_sn_threshold: float
    heavily_masked_threshold: float
    toa_method: str = "peak_bin"
    toa_peak_selection: str = "automatic_event_peak"
    toa_reference_frame: str = "topocentric_source_header"
    toa_time_scale: str = "utc"
    toa_reference_frequency_mhz: float | None = None
    toa_reference_frequency_basis: str | None = None
    toa_status: str = "peak_topo_only"
    toa_status_reason: str = ""
    source_ra_deg: float | None = None
    source_dec_deg: float | None = None
    source_position_basis: str | None = None
    observatory_name: str | None = None
    observatory_longitude_deg: float | None = None
    observatory_latitude_deg: float | None = None
    observatory_height_m: float | None = None
    observatory_location_basis: str | None = None
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
            "offpulse_windows_ms": self.offpulse_windows_ms,
            "offpulse_bin_count": self.offpulse_bin_count,
            "burst_bin_count": self.burst_bin_count,
            "selected_channel_count": self.selected_channel_count,
            "active_channel_count": self.active_channel_count,
            "selected_bandwidth_mhz": self.selected_bandwidth_mhz,
            "effective_bandwidth_mhz": self.effective_bandwidth_mhz,
            "masked_fraction": self.masked_fraction,
            "masked_channels": self.masked_channels,
            "tsamp_ms": self.tsamp_ms,
            "freqres_mhz": self.freqres_mhz,
            "npol": self.npol,
            "sefd_jy": self.sefd_jy,
            "calibration_assumptions": self.calibration_assumptions,
            "noise_basis": self.noise_basis,
            "noise_estimator": self.noise_estimator,
            "algorithm_name": self.algorithm_name,
            "warning_flags": self.warning_flags,
            "low_sn_threshold": self.low_sn_threshold,
            "heavily_masked_threshold": self.heavily_masked_threshold,
            "toa_method": self.toa_method,
            "toa_peak_selection": self.toa_peak_selection,
            "toa_reference_frame": self.toa_reference_frame,
            "toa_time_scale": self.toa_time_scale,
            "toa_reference_frequency_mhz": _float_or_none(self.toa_reference_frequency_mhz),
            "toa_reference_frequency_basis": self.toa_reference_frequency_basis,
            "toa_status": self.toa_status,
            "toa_status_reason": self.toa_status_reason,
            "source_ra_deg": _float_or_none(self.source_ra_deg),
            "source_dec_deg": _float_or_none(self.source_dec_deg),
            "source_position_basis": self.source_position_basis,
            "observatory_name": self.observatory_name,
            "observatory_longitude_deg": _float_or_none(self.observatory_longitude_deg),
            "observatory_latitude_deg": _float_or_none(self.observatory_latitude_deg),
            "observatory_height_m": _float_or_none(self.observatory_height_m),
            "observatory_location_basis": self.observatory_location_basis,
            "deprecated_fields": self.deprecated_fields,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MeasurementProvenance":
        return cls(
            manual_selection=bool(payload.get("manual_selection", False)),
            peak_selection=str(payload.get("peak_selection", "automatic")),
            width_method=str(payload.get("width_method", "unknown")),
            spectral_width_method=str(payload.get("spectral_width_method", "unknown")),
            calibration_method=str(payload.get("calibration_method", "uncalibrated")),
            energy_unit=payload.get("energy_unit"),
            uncertainty_basis=str(payload.get("uncertainty_basis", "")),
            event_window_ms=[float(value) for value in payload.get("event_window_ms", [])],
            spectral_extent_mhz=[float(value) for value in payload.get("spectral_extent_mhz", [])],
            offpulse_windows_ms=[
                [float(value) for value in window] for window in payload.get("offpulse_windows_ms", [])
            ],
            offpulse_bin_count=int(payload.get("offpulse_bin_count", 0)),
            burst_bin_count=int(payload.get("burst_bin_count", 0)),
            selected_channel_count=int(payload.get("selected_channel_count", 0)),
            active_channel_count=int(payload.get("active_channel_count", 0)),
            selected_bandwidth_mhz=float(payload.get("selected_bandwidth_mhz", 0.0)),
            effective_bandwidth_mhz=float(payload.get("effective_bandwidth_mhz", 0.0)),
            masked_fraction=float(payload.get("masked_fraction", 0.0)),
            masked_channels=[int(value) for value in payload.get("masked_channels", [])],
            tsamp_ms=float(payload.get("tsamp_ms", 0.0)),
            freqres_mhz=float(payload.get("freqres_mhz", 0.0)),
            npol=int(payload.get("npol", 0)),
            sefd_jy=_float_or_none(payload.get("sefd_jy")),
            calibration_assumptions=[str(value) for value in payload.get("calibration_assumptions", [])],
            noise_basis=str(payload.get("noise_basis", "implicit_event_complement")),
            noise_estimator=str(payload.get("noise_estimator", "mean_std")),
            algorithm_name=str(payload.get("algorithm_name", "burst_measurements")),
            warning_flags=[str(value) for value in payload.get("warning_flags", [])],
            low_sn_threshold=float(payload.get("low_sn_threshold", 0.0)),
            heavily_masked_threshold=float(payload.get("heavily_masked_threshold", 0.0)),
            toa_method=str(payload.get("toa_method", "peak_bin")),
            toa_peak_selection=str(payload.get("toa_peak_selection", "automatic_event_peak")),
            toa_reference_frame=str(payload.get("toa_reference_frame", "topocentric_source_header")),
            toa_time_scale=str(payload.get("toa_time_scale", "utc")),
            toa_reference_frequency_mhz=_float_or_none(payload.get("toa_reference_frequency_mhz")),
            toa_reference_frequency_basis=payload.get("toa_reference_frequency_basis"),
            toa_status=str(payload.get("toa_status", "peak_topo_only")),
            toa_status_reason=str(payload.get("toa_status_reason", "")),
            source_ra_deg=_float_or_none(payload.get("source_ra_deg")),
            source_dec_deg=_float_or_none(payload.get("source_dec_deg")),
            source_position_basis=payload.get("source_position_basis"),
            observatory_name=payload.get("observatory_name"),
            observatory_longitude_deg=_float_or_none(payload.get("observatory_longitude_deg")),
            observatory_latitude_deg=_float_or_none(payload.get("observatory_latitude_deg")),
            observatory_height_m=_float_or_none(payload.get("observatory_height_m")),
            observatory_location_basis=payload.get("observatory_location_basis"),
            deprecated_fields=[str(value) for value in payload.get("deprecated_fields", [])],
        )


@dataclass(frozen=True)
class ScatteringFitDiagnostics:
    status: str
    message: str | None
    fitter: str | None
    component_count: int
    fit_parameters: list[str] = field(default_factory=list)
    fixed_parameters: list[str] = field(default_factory=list)
    weighted_fit: bool | None = None
    weight_range: list[int] | None = None
    weight_range_basis: str | None = None
    fit_iterations_requested: int | None = None
    fit_iterations_completed: int | None = None
    failure_stdout: str | None = None
    failure_stderr: str | None = None
    failure_exception: str | None = None
    initial_parameters: dict[str, list[float] | None] = field(default_factory=dict)
    bestfit_parameters: dict[str, list[float] | None] = field(default_factory=dict)
    bestfit_uncertainties: dict[str, list[float] | None] = field(default_factory=dict)
    fit_statistics: dict[str, float | int | None] = field(default_factory=dict)
    freq_axis_mhz: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    time_axis_ms: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    data_dynamic_spectrum_sn: np.ndarray = field(default_factory=lambda: np.empty((0, 0), dtype=float))
    model_dynamic_spectrum_sn: np.ndarray = field(default_factory=lambda: np.empty((0, 0), dtype=float))
    residual_dynamic_spectrum_sn: np.ndarray = field(default_factory=lambda: np.empty((0, 0), dtype=float))
    data_profile_sn: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    model_profile_sn: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    residual_profile_sn: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    data_freq_profile_sn: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    model_freq_profile_sn: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    residual_freq_profile_sn: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    uncertainty_details: dict[str, UncertaintyDetail] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        fit_statistics = {
            key: (
                None
                if value is None
                else float(value)
                if isinstance(value, (np.floating, float))
                else int(value)
                if isinstance(value, (np.integer, int))
                else value
            )
            for key, value in self.fit_statistics.items()
        }
        return {
            "status": self.status,
            "message": self.message,
            "fitter": self.fitter,
            "component_count": self.component_count,
            "fit_parameters": self.fit_parameters,
            "fixed_parameters": self.fixed_parameters,
            "weighted_fit": _bool_or_none(self.weighted_fit),
            "weight_range": None if self.weight_range is None else [int(value) for value in self.weight_range],
            "weight_range_basis": self.weight_range_basis,
            "fit_iterations_requested": _int_or_none(self.fit_iterations_requested),
            "fit_iterations_completed": _int_or_none(self.fit_iterations_completed),
            "failure_stdout": self.failure_stdout,
            "failure_stderr": self.failure_stderr,
            "failure_exception": self.failure_exception,
            "initial_parameters": _jsonable_parameter_dict(self.initial_parameters),
            "bestfit_parameters": _jsonable_parameter_dict(self.bestfit_parameters),
            "bestfit_uncertainties": _jsonable_parameter_dict(self.bestfit_uncertainties),
            "fit_statistics": fit_statistics,
            "freq_axis_mhz": _jsonable_1d(self.freq_axis_mhz, digits=6),
            "time_axis_ms": _jsonable_1d(self.time_axis_ms, digits=6),
            "data_dynamic_spectrum_sn": _jsonable(self.data_dynamic_spectrum_sn, digits=6),
            "model_dynamic_spectrum_sn": _jsonable(self.model_dynamic_spectrum_sn, digits=6),
            "residual_dynamic_spectrum_sn": _jsonable(self.residual_dynamic_spectrum_sn, digits=6),
            "data_profile_sn": _jsonable_1d(self.data_profile_sn, digits=6),
            "model_profile_sn": _jsonable_1d(self.model_profile_sn, digits=6),
            "residual_profile_sn": _jsonable_1d(self.residual_profile_sn, digits=6),
            "data_freq_profile_sn": _jsonable_1d(self.data_freq_profile_sn, digits=6),
            "model_freq_profile_sn": _jsonable_1d(self.model_freq_profile_sn, digits=6),
            "residual_freq_profile_sn": _jsonable_1d(self.residual_freq_profile_sn, digits=6),
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScatteringFitDiagnostics":
        return cls(
            status=str(payload.get("status", "unknown")),
            message=payload.get("message"),
            fitter=payload.get("fitter"),
            component_count=int(payload.get("component_count", 0)),
            fit_parameters=[str(value) for value in payload.get("fit_parameters", [])],
            fixed_parameters=[str(value) for value in payload.get("fixed_parameters", [])],
            weighted_fit=_bool_or_none(payload.get("weighted_fit")),
            weight_range=(
                None
                if payload.get("weight_range") is None
                else [int(value) for value in payload.get("weight_range", [])[:2]]
            ),
            weight_range_basis=payload.get("weight_range_basis"),
            fit_iterations_requested=_int_or_none(payload.get("fit_iterations_requested")),
            fit_iterations_completed=_int_or_none(payload.get("fit_iterations_completed")),
            failure_stdout=payload.get("failure_stdout"),
            failure_stderr=payload.get("failure_stderr"),
            failure_exception=payload.get("failure_exception"),
            initial_parameters={str(key): value for key, value in payload.get("initial_parameters", {}).items()},
            bestfit_parameters={str(key): value for key, value in payload.get("bestfit_parameters", {}).items()},
            bestfit_uncertainties={str(key): value for key, value in payload.get("bestfit_uncertainties", {}).items()},
            fit_statistics={str(key): value for key, value in payload.get("fit_statistics", {}).items()},
            freq_axis_mhz=_array_1d(payload.get("freq_axis_mhz"), dtype=float),
            time_axis_ms=_array_1d(payload.get("time_axis_ms"), dtype=float),
            data_dynamic_spectrum_sn=_array_2d(payload.get("data_dynamic_spectrum_sn"), dtype=float),
            model_dynamic_spectrum_sn=_array_2d(payload.get("model_dynamic_spectrum_sn"), dtype=float),
            residual_dynamic_spectrum_sn=_array_2d(payload.get("residual_dynamic_spectrum_sn"), dtype=float),
            data_profile_sn=_array_1d(payload.get("data_profile_sn"), dtype=float),
            model_profile_sn=_array_1d(payload.get("model_profile_sn"), dtype=float),
            residual_profile_sn=_array_1d(payload.get("residual_profile_sn"), dtype=float),
            data_freq_profile_sn=_array_1d(payload.get("data_freq_profile_sn"), dtype=float),
            model_freq_profile_sn=_array_1d(payload.get("model_freq_profile_sn"), dtype=float),
            residual_freq_profile_sn=_array_1d(payload.get("residual_freq_profile_sn"), dtype=float),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
        )


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
    scattering_fit: ScatteringFitDiagnostics | None = None

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
            "scattering_fit": self.scattering_fit.to_dict() if self.scattering_fit is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MeasurementDiagnostics":
        return cls(
            gaussian_fits=[GaussianFit1D.from_dict(item) for item in payload.get("gaussian_fits", [])],
            time_axis_ms=_array_1d(payload.get("time_axis_ms"), dtype=float),
            time_profile_sn=_array_1d(payload.get("time_profile_sn"), dtype=float),
            burst_only_profile_sn=_array_1d(payload.get("burst_only_profile_sn"), dtype=float),
            event_profile_sn=_array_1d(payload.get("event_profile_sn"), dtype=float),
            spectral_axis_mhz=_array_1d(payload.get("spectral_axis_mhz"), dtype=float),
            spectrum_sn=_array_1d(payload.get("spectrum_sn"), dtype=float),
            temporal_acf=_array_1d(payload.get("temporal_acf"), dtype=float),
            temporal_acf_lags_ms=_array_1d(payload.get("temporal_acf_lags_ms"), dtype=float),
            spectral_acf=_array_1d(payload.get("spectral_acf"), dtype=float),
            spectral_acf_lags_mhz=_array_1d(payload.get("spectral_acf_lags_mhz"), dtype=float),
            scattering_fit=(
                None
                if payload.get("scattering_fit") is None
                else ScatteringFitDiagnostics.from_dict(payload["scattering_fit"])
            ),
        )


@dataclass(frozen=True)
class BurstMeasurements:
    burst_name: str
    dm: float
    toa_peak_topo_mjd: float | None
    toa_topo_mjd: float | None
    mjd_at_peak: float | None
    toa_inf_topo_mjd: float | None
    toa_inf_bary_mjd_tdb: float | None
    dispersion_to_infinite_frequency_ms: float | None
    barycentric_correction_ms: float | None
    toa_reference_frequency_mhz: float | None
    toa_status: str
    toa_status_reason: str
    peak_positions_ms: list[float]
    snr_peak: float | None
    snr_integrated: float | None
    width_ms_acf: float | None
    width_ms_model: float | None
    spectral_width_mhz_acf: float | None
    tau_sc_ms: float | None
    peak_flux_jy: float | None
    fluence_jyms: float | None
    iso_e: float | None
    event_duration_ms: float
    spectral_extent_mhz: float
    measurement_flags: list[str]
    uncertainties: MeasurementUncertainties
    uncertainty_details: dict[str, UncertaintyDetail]
    provenance: MeasurementProvenance
    diagnostics: MeasurementDiagnostics
    mask_count: int
    masked_channels: list[int]
    width_results: list[WidthResult] = field(default_factory=list)
    accepted_width: AcceptedWidthSelection | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "burst_name": self.burst_name,
            "dm": self.dm,
            "toa_peak_topo_mjd": self.toa_peak_topo_mjd,
            "toa_topo_mjd": self.toa_topo_mjd,
            "mjd_at_peak": self.mjd_at_peak,
            "toa_inf_topo_mjd": self.toa_inf_topo_mjd,
            "toa_inf_bary_mjd_tdb": self.toa_inf_bary_mjd_tdb,
            "dispersion_to_infinite_frequency_ms": self.dispersion_to_infinite_frequency_ms,
            "barycentric_correction_ms": self.barycentric_correction_ms,
            "toa_reference_frequency_mhz": self.toa_reference_frequency_mhz,
            "toa_status": self.toa_status,
            "toa_status_reason": self.toa_status_reason,
            "peak_positions_ms": self.peak_positions_ms,
            "snr_peak": self.snr_peak,
            "snr_integrated": self.snr_integrated,
            "width_ms_acf": self.width_ms_acf,
            "width_ms_model": self.width_ms_model,
            "spectral_width_mhz_acf": self.spectral_width_mhz_acf,
            "tau_sc_ms": self.tau_sc_ms,
            "peak_flux_jy": self.peak_flux_jy,
            "fluence_jyms": self.fluence_jyms,
            "iso_e": self.iso_e,
            "event_duration_ms": self.event_duration_ms,
            "spectral_extent_mhz": self.spectral_extent_mhz,
            "measurement_flags": self.measurement_flags,
            "uncertainties": self.uncertainties.to_dict(),
            "uncertainty_details": _uncertainty_detail_map_to_dict(self.uncertainty_details),
            "provenance": self.provenance.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
            "mask_count": self.mask_count,
            "masked_channels": self.masked_channels,
            "width_results": [result.to_dict() for result in self.width_results],
            "accepted_width": None if self.accepted_width is None else self.accepted_width.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BurstMeasurements":
        return cls(
            burst_name=str(payload["burst_name"]),
            dm=float(payload["dm"]),
            toa_peak_topo_mjd=_float_or_none(
                payload.get("toa_peak_topo_mjd", payload.get("toa_topo_mjd"))
            ),
            toa_topo_mjd=_float_or_none(payload.get("toa_topo_mjd")),
            mjd_at_peak=_float_or_none(payload.get("mjd_at_peak")),
            toa_inf_topo_mjd=_float_or_none(payload.get("toa_inf_topo_mjd")),
            toa_inf_bary_mjd_tdb=_float_or_none(payload.get("toa_inf_bary_mjd_tdb")),
            dispersion_to_infinite_frequency_ms=_float_or_none(
                payload.get("dispersion_to_infinite_frequency_ms")
            ),
            barycentric_correction_ms=_float_or_none(payload.get("barycentric_correction_ms")),
            toa_reference_frequency_mhz=_float_or_none(payload.get("toa_reference_frequency_mhz")),
            toa_status=str(payload.get("toa_status", "peak_topo_only")),
            toa_status_reason=str(payload.get("toa_status_reason", "")),
            peak_positions_ms=[float(value) for value in payload.get("peak_positions_ms", [])],
            snr_peak=_float_or_none(payload.get("snr_peak")),
            snr_integrated=_float_or_none(payload.get("snr_integrated")),
            width_ms_acf=_float_or_none(payload.get("width_ms_acf")),
            width_ms_model=_float_or_none(payload.get("width_ms_model")),
            spectral_width_mhz_acf=_float_or_none(payload.get("spectral_width_mhz_acf")),
            tau_sc_ms=_float_or_none(payload.get("tau_sc_ms")),
            peak_flux_jy=_float_or_none(payload.get("peak_flux_jy")),
            fluence_jyms=_float_or_none(payload.get("fluence_jyms")),
            iso_e=_float_or_none(payload.get("iso_e")),
            event_duration_ms=float(payload.get("event_duration_ms", 0.0)),
            spectral_extent_mhz=float(payload.get("spectral_extent_mhz", 0.0)),
            measurement_flags=[str(flag) for flag in payload.get("measurement_flags", [])],
            uncertainties=MeasurementUncertainties.from_dict(payload.get("uncertainties")),
            uncertainty_details=_uncertainty_detail_map_from_dict(payload.get("uncertainty_details")),
            provenance=MeasurementProvenance.from_dict(payload.get("provenance", {})),
            diagnostics=MeasurementDiagnostics.from_dict(payload.get("diagnostics", {})),
            mask_count=int(payload.get("mask_count", 0)),
            masked_channels=[int(value) for value in payload.get("masked_channels", [])],
            width_results=[WidthResult.from_dict(item) for item in payload.get("width_results", [])],
            accepted_width=(
                None
                if payload.get("accepted_width") is None
                else AcceptedWidthSelection.from_dict(payload["accepted_width"])
            ),
        )


@dataclass(frozen=True)
class AnalysisSessionSnapshot:
    schema_version: str
    source: SessionSourceRef
    dm: float
    preset_key: str
    sefd_jy: float | None
    npol_override: int | None
    read_start_sec: float
    read_end_sec: float | None
    auto_mask_profile: str
    distance_mpc: float | None
    redshift: float | None
    sefd_fractional_uncertainty: float | None
    distance_fractional_uncertainty: float | None
    time_factor: int
    freq_factor: int
    crop_bins: list[int]
    event_bins: list[int]
    spectral_extent_channels: list[int]
    burst_regions: list[BurstRegion]
    offpulse_regions: list[OffPulseRegion]
    peak_bins: list[int]
    manual_peaks: bool
    masked_channels: list[int]
    last_auto_mask: AutoMaskRunSummary | None
    noise_settings: NoiseEstimateSettings
    width_settings: WidthAnalysisSettings
    notes: str | None
    results: BurstMeasurements | None
    width_analysis: WidthAnalysisSummary | None
    dm_optimization: DmOptimizationResult | None
    spectral_analysis: SpectralAnalysisResult | None
    temporal_structure: TemporalStructureResult | None
    source_ra_deg: float | None = None
    source_dec_deg: float | None = None
    time_scale: str | None = None
    observatory_longitude_deg: float | None = None
    observatory_latitude_deg: float | None = None
    observatory_height_m: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "dm": float(self.dm),
            "preset_key": self.preset_key,
            "sefd_jy": _float_or_none(self.sefd_jy),
            "npol_override": None if self.npol_override is None else int(self.npol_override),
            "read_start_sec": float(self.read_start_sec),
            "read_end_sec": _float_or_none(self.read_end_sec),
            "auto_mask_profile": self.auto_mask_profile,
            "distance_mpc": _float_or_none(self.distance_mpc),
            "redshift": _float_or_none(self.redshift),
            "sefd_fractional_uncertainty": _float_or_none(self.sefd_fractional_uncertainty),
            "distance_fractional_uncertainty": _float_or_none(self.distance_fractional_uncertainty),
            "time_factor": int(self.time_factor),
            "freq_factor": int(self.freq_factor),
            "crop_bins": [int(value) for value in self.crop_bins],
            "event_bins": [int(value) for value in self.event_bins],
            "spectral_extent_channels": [int(value) for value in self.spectral_extent_channels],
            "burst_regions": [region.to_dict() for region in self.burst_regions],
            "offpulse_regions": [region.to_dict() for region in self.offpulse_regions],
            "peak_bins": [int(value) for value in self.peak_bins],
            "manual_peaks": bool(self.manual_peaks),
            "masked_channels": [int(value) for value in self.masked_channels],
            "last_auto_mask": None if self.last_auto_mask is None else self.last_auto_mask.to_dict(),
            "noise_settings": self.noise_settings.to_dict(),
            "width_settings": self.width_settings.to_dict(),
            "notes": self.notes,
            "results": None if self.results is None else self.results.to_dict(),
            "width_analysis": None if self.width_analysis is None else self.width_analysis.to_dict(),
            "dm_optimization": None if self.dm_optimization is None else self.dm_optimization.to_dict(),
            "spectral_analysis": None if self.spectral_analysis is None else self.spectral_analysis.to_dict(),
            "temporal_structure": None if self.temporal_structure is None else self.temporal_structure.to_dict(),
            "source_ra_deg": _float_or_none(self.source_ra_deg),
            "source_dec_deg": _float_or_none(self.source_dec_deg),
            "time_scale": self.time_scale,
            "observatory_longitude_deg": _float_or_none(self.observatory_longitude_deg),
            "observatory_latitude_deg": _float_or_none(self.observatory_latitude_deg),
            "observatory_height_m": _float_or_none(self.observatory_height_m),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnalysisSessionSnapshot":
        return cls(
            schema_version=str(payload.get("schema_version", "1.0")),
            source=SessionSourceRef.from_dict(payload["source"]),
            dm=float(payload["dm"]),
            preset_key=str(payload.get("preset_key", "generic")),
            sefd_jy=_float_or_none(payload.get("sefd_jy")),
            npol_override=_int_or_none(payload.get("npol_override")),
            read_start_sec=float(payload.get("read_start_sec", 0.0)),
            read_end_sec=_float_or_none(payload.get("read_end_sec")),
            auto_mask_profile=str(payload.get("auto_mask_profile", "auto")),
            distance_mpc=_float_or_none(payload.get("distance_mpc")),
            redshift=_float_or_none(payload.get("redshift")),
            sefd_fractional_uncertainty=_float_or_none(payload.get("sefd_fractional_uncertainty")),
            distance_fractional_uncertainty=_float_or_none(payload.get("distance_fractional_uncertainty")),
            time_factor=int(payload.get("time_factor", 1)),
            freq_factor=int(payload.get("freq_factor", 1)),
            crop_bins=[int(value) for value in payload.get("crop_bins", [])],
            event_bins=[int(value) for value in payload.get("event_bins", [])],
            spectral_extent_channels=[int(value) for value in payload.get("spectral_extent_channels", [])],
            burst_regions=[BurstRegion.from_dict(item) for item in payload.get("burst_regions", [])],
            offpulse_regions=[OffPulseRegion.from_dict(item) for item in payload.get("offpulse_regions", [])],
            peak_bins=[int(value) for value in payload.get("peak_bins", [])],
            manual_peaks=bool(payload.get("manual_peaks", False)),
            masked_channels=[int(value) for value in payload.get("masked_channels", [])],
            last_auto_mask=AutoMaskRunSummary.from_dict(payload.get("last_auto_mask")),
            noise_settings=NoiseEstimateSettings.from_dict(payload.get("noise_settings")),
            width_settings=WidthAnalysisSettings.from_dict(payload.get("width_settings")),
            notes=payload.get("notes"),
            results=(
                None
                if payload.get("results") is None
                else BurstMeasurements.from_dict(payload["results"])
            ),
            width_analysis=(
                None
                if payload.get("width_analysis") is None
                else WidthAnalysisSummary.from_dict(payload["width_analysis"])
            ),
            dm_optimization=(
                None
                if payload.get("dm_optimization") is None
                else DmOptimizationResult.from_dict(payload["dm_optimization"])
            ),
            spectral_analysis=SpectralAnalysisResult.from_dict(payload.get("spectral_analysis")),
            temporal_structure=TemporalStructureResult.from_dict(payload.get("temporal_structure")),
            source_ra_deg=_float_or_none(payload.get("source_ra_deg")),
            source_dec_deg=_float_or_none(payload.get("source_dec_deg")),
            time_scale=None if payload.get("time_scale") is None else str(payload.get("time_scale")),
            observatory_longitude_deg=_float_or_none(payload.get("observatory_longitude_deg")),
            observatory_latitude_deg=_float_or_none(payload.get("observatory_latitude_deg")),
            observatory_height_m=_float_or_none(payload.get("observatory_height_m")),
        )


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


@dataclass(frozen=True)
class ExportPreviewArtifact:
    label: str
    kind: str
    content_type: str
    format: str | None
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "content_type": self.content_type,
            "format": self.format,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExportPlotPreview:
    plot_key: str
    title: str
    status: str
    reason: str | None
    svg: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plot_key": self.plot_key,
            "title": self.title,
            "status": self.status,
            "reason": self.reason,
            "svg": self.svg,
        }


@dataclass(frozen=True)
class ExportPreview:
    selection: dict[str, list[str]]
    artifacts: list[ExportPreviewArtifact]
    plot_previews: list[ExportPlotPreview]
    generated_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection": self.selection,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "plot_previews": [preview.to_dict() for preview in self.plot_previews],
            "generated_at_utc": self.generated_at_utc,
        }
