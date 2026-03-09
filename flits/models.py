from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


def _jsonable_1d(values: np.ndarray, digits: int = 4) -> list[float | None]:
    rounded = np.round(np.asarray(values), digits)
    return [float(value) if np.isfinite(value) else None for value in rounded]


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
class BurstMeasurements:
    burst_name: str
    dm: float
    mjd_at_peak: float
    peak_positions_ms: list[float]
    peak_flux_jy: float | None
    fluence_jyms: float | None
    event_duration_ms: float
    spectral_extent_mhz: float
    gaussian_fits: list[GaussianFit1D]
    mask_count: int
    masked_channels: list[int]
    integrated_sn: np.ndarray
    time_profile_sn: np.ndarray
    burst_only_profile_sn: np.ndarray
    time_axis_ms: np.ndarray
    iso_e: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "burst_name": self.burst_name,
            "dm": self.dm,
            "mjd_at_peak": self.mjd_at_peak,
            "peak_positions_ms": self.peak_positions_ms,
            "peak_flux_jy": self.peak_flux_jy,
            "fluence_jyms": self.fluence_jyms,
            "event_duration_ms": self.event_duration_ms,
            "spectral_extent_mhz": self.spectral_extent_mhz,
            "gaussian_fits": [fit.to_dict() for fit in self.gaussian_fits],
            "mask_count": self.mask_count,
            "masked_channels": self.masked_channels,
            "integrated_sn": _jsonable_1d(self.integrated_sn),
            "time_profile_sn": _jsonable_1d(self.time_profile_sn),
            "burst_only_profile_sn": _jsonable_1d(self.burst_only_profile_sn),
            "time_axis_ms": _jsonable_1d(self.time_axis_ms),
            "iso_e": self.iso_e,
        }
