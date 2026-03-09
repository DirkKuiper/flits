from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import warnings

import numpy as np
from astropy import units as u
from scipy.optimize import curve_fit

from flits.io import inspect_filterbank, load_filterbank_data
from flits.models import BurstMeasurements, FilterbankMetadata, GaussianFit1D
from flits.settings import ObservationConfig, get_preset
from flits.signal import block_reduce_mean, dedisperse, gaussian_1d, radiometer

try:
    import jess.channel_masks
except ImportError:  # pragma: no cover - optional dependency
    jess = None


def _safe_normalize(series: np.ndarray, offpulse: np.ndarray) -> np.ndarray:
    offpulse = offpulse[np.isfinite(offpulse)]
    baseline = float(np.nanmean(offpulse)) if offpulse.size else float(np.nanmean(series))
    sigma = float(np.nanstd(offpulse)) if offpulse.size else float(np.nanstd(series))
    if not np.isfinite(sigma) or sigma == 0:
        sigma = 1.0
    return (series - baseline) / sigma


def _jsonable_array(values: np.ndarray, digits: int = 4) -> list[Any]:
    rounded = np.round(values, digits)
    if rounded.ndim == 1:
        return [float(value) if np.isfinite(value) else None for value in rounded]
    return [
        [float(value) if np.isfinite(value) else None for value in row]
        for row in rounded
    ]


def _power_of_two_ceiling(value: float) -> int:
    factor = 1
    while factor < value:
        factor *= 2
    return factor


def _nanmean_profile(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return np.array([], dtype=float)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(values, axis=0)


def default_time_factor(num_time_bins: int, target_points: int = 1800) -> int:
    if num_time_bins <= target_points:
        return 1
    return _power_of_two_ceiling(num_time_bins / target_points)


def robust_color_limits(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return -1.0, 1.0

    vmin = float(np.nanquantile(finite, 0.02))
    vmax = float(np.nanquantile(finite, 0.995))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin = float(np.nanmin(finite))
        vmax = float(np.nanmax(finite))
    if vmax <= vmin:
        vmax = vmin + 1.0
    return vmin, vmax


@dataclass
class BurstSession:
    config: ObservationConfig
    metadata: FilterbankMetadata
    data: np.ndarray
    time_factor: int = 1
    freq_factor: int = 1
    crop_start: int = 0
    crop_end: int = 0
    event_start: int = 0
    event_end: int = 0
    spec_ex_lo: int = 0
    spec_ex_hi: int = 0
    burst_regions: list[tuple[int, int]] = field(default_factory=list)
    peak_positions: list[int] = field(default_factory=list)
    manual_peaks: bool = False
    channel_mask: np.ndarray | None = None
    mask_history: list[list[int]] = field(default_factory=list)
    results: BurstMeasurements | None = None

    @classmethod
    def from_file(
        cls,
        bfile: str,
        dm: float,
        telescope: str | None = None,
        *,
        sefd_jy: float | None = None,
        read_start_sec: float | None = None,
        initial_crop_sec: float | None = None,
        distance_mpc: float | None = None,
        redshift: float | None = None,
    ) -> "BurstSession":
        inspection = inspect_filterbank(bfile)
        preset_key = telescope if telescope is not None else inspection.detected_preset_key
        config = ObservationConfig.from_preset(
            dm=dm,
            preset_key=preset_key,
            sefd_jy=sefd_jy,
            read_start_sec=read_start_sec,
            initial_crop_sec=initial_crop_sec,
            distance_mpc=distance_mpc,
            redshift=redshift,
        )
        data, metadata = load_filterbank_data(bfile, config, inspection=inspection)
        num_time_bins = data.shape[1]
        return cls(
            config=config,
            metadata=metadata,
            data=data,
            time_factor=default_time_factor(num_time_bins),
            freq_factor=1,
            crop_start=0,
            crop_end=num_time_bins,
            event_start=2 * (num_time_bins // 10),
            event_end=3 * (num_time_bins // 10),
            spec_ex_lo=0,
            spec_ex_hi=data.shape[0] - 1,
            channel_mask=np.zeros(data.shape[0], dtype=bool),
        )

    @property
    def burst_file(self) -> str:
        return str(self.metadata.source_path)

    @property
    def dm(self) -> float:
        return self.config.dm

    @property
    def telescope(self) -> str:
        return self.config.telescope_label

    @property
    def detected_telescope(self) -> str:
        return get_preset(self.metadata.detected_preset_key).label

    @property
    def tsamp(self) -> float:
        return self.metadata.tsamp

    @property
    def freqres(self) -> float:
        return self.metadata.freqres

    @property
    def start_mjd(self) -> float:
        return self.metadata.start_mjd

    @property
    def plus_mjd_sec(self) -> float:
        return self.metadata.read_start_sec

    @property
    def sefd(self) -> float | None:
        return self.metadata.sefd_jy

    @property
    def bw(self) -> float:
        return self.metadata.bandwidth_mhz

    @property
    def npol(self) -> int:
        return self.metadata.npol

    @property
    def freqs(self) -> np.ndarray:
        return self.metadata.freqs_mhz

    def invalidate_results(self) -> None:
        self.results = None

    @property
    def total_time_bins(self) -> int:
        return int(self.data.shape[1])

    @property
    def total_channels(self) -> int:
        return int(self.data.shape[0])

    @property
    def tsamp_ms(self) -> float:
        return float(self.tsamp * 1e3)

    def bin_to_ms(self, time_bin: int | float) -> float:
        return float(time_bin) * self.tsamp_ms

    def ms_to_bin(self, time_ms: float) -> int:
        return int(round(float(time_ms) / self.tsamp_ms))

    def clamp_bin(self, value: int, is_end: bool = False) -> int:
        if is_end:
            return max(1, min(int(value), self.total_time_bins))
        return max(0, min(int(value), self.total_time_bins - 1))

    def clamp_channel(self, value: int) -> int:
        return max(0, min(int(value), self.total_channels - 1))

    def freq_to_channel(self, freq_mhz: float) -> int:
        return int(np.argmin(np.abs(self.freqs - float(freq_mhz))))

    def _current_peak_positions(self) -> list[int]:
        if self.manual_peaks and self.peak_positions:
            return sorted(p for p in self.peak_positions if self.crop_start <= p < self.crop_end)

        masked = self.get_masked_crop()
        if masked.size == 0:
            return []
        profile = np.nansum(masked, axis=0)
        if not np.isfinite(profile).any():
            return []
        peak_local = int(np.nanargmax(profile))
        return [self.crop_start + peak_local]

    def get_masked_crop(self) -> np.ndarray:
        arr = self.data[:, self.crop_start:self.crop_end].astype(float, copy=True)
        if self.channel_mask is not None and self.channel_mask.any():
            arr[self.channel_mask, :] = np.nan
        return arr

    def get_view(self) -> dict[str, Any]:
        masked = self.get_masked_crop()
        reduced = block_reduce_mean(masked, tfac=self.time_factor, ffac=self.freq_factor)

        time_axis = (
            (self.crop_start + np.arange(reduced.shape[1]) * self.time_factor) * self.tsamp_ms
            if reduced.size
            else np.array([], dtype=float)
        )
        freq_axis = self.freqs[: reduced.shape[0] * self.freq_factor]
        if self.freq_factor > 1 and freq_axis.size:
            freq_axis = np.nanmean(freq_axis.reshape(reduced.shape[0], self.freq_factor), axis=1)

        time_profile = np.nansum(reduced, axis=0) if reduced.size else np.array([], dtype=float)
        spectrum = np.nansum(reduced, axis=1) if reduced.size else np.array([], dtype=float)
        peak_positions = self._current_peak_positions()
        zmin, zmax = robust_color_limits(reduced)

        return {
            "meta": {
                "burst_file": self.burst_file,
                "burst_name": Path(self.burst_file).name,
                "dm": self.dm,
                "telescope": self.telescope,
                "preset_key": self.config.preset_key,
                "detected_telescope": self.detected_telescope,
                "detected_preset_key": self.metadata.detected_preset_key,
                "detection_basis": self.metadata.detection_basis,
                "telescope_id": self.metadata.telescope_id,
                "machine_id": self.metadata.machine_id,
                "source_name": self.metadata.source_name,
                "sefd_jy": self.sefd,
                "tsamp_us": self.tsamp * 1e6,
                "freqres_mhz": self.freqres,
                "npol": self.npol,
                "shape": [self.total_channels, self.total_time_bins],
                "view_shape": [int(reduced.shape[0]), int(reduced.shape[1])],
                "freq_range_mhz": [float(self.freqs[0]), float(self.freqs[-1])],
            },
            "state": {
                "time_factor": self.time_factor,
                "freq_factor": self.freq_factor,
                "crop_ms": [self.bin_to_ms(self.crop_start), self.bin_to_ms(self.crop_end)],
                "event_ms": [self.bin_to_ms(self.event_start), self.bin_to_ms(self.event_end)],
                "burst_regions_ms": [
                    [self.bin_to_ms(start), self.bin_to_ms(end)] for start, end in self.burst_regions
                ],
                "peak_ms": [self.bin_to_ms(peak) for peak in peak_positions],
                "manual_peaks": self.manual_peaks,
                "spectral_extent_mhz": [
                    float(self.freqs[self.spec_ex_lo]),
                    float(self.freqs[self.spec_ex_hi]),
                ],
                "masked_channels": np.flatnonzero(self.channel_mask).astype(int).tolist(),
            },
            "plot": {
                "heatmap": {
                    "x_ms": _jsonable_array(time_axis, digits=4),
                    "y_mhz": _jsonable_array(freq_axis, digits=4),
                    "z": _jsonable_array(reduced, digits=3),
                    "zmin": zmin,
                    "zmax": zmax,
                },
                "time_profile": {
                    "x_ms": _jsonable_array(time_axis, digits=4),
                    "y": _jsonable_array(time_profile, digits=4),
                },
                "spectrum": {
                    "x": _jsonable_array(spectrum, digits=4),
                    "y_mhz": _jsonable_array(freq_axis, digits=4),
                },
            },
            "results": self.results.to_dict() if self.results is not None else None,
        }

    def _sync_selections_to_crop(self) -> None:
        if self.crop_end <= self.crop_start + 1:
            self.crop_end = min(self.total_time_bins, self.crop_start + 2)

        self.time_factor = min(self.time_factor, max(1, self.crop_end - self.crop_start))
        self.freq_factor = min(self.freq_factor, max(1, self.total_channels))
        self.event_start = max(self.crop_start, min(self.event_start, self.crop_end - 1))
        self.event_end = max(self.event_start + 1, min(self.event_end, self.crop_end))
        if self.event_end <= self.event_start:
            span = max(2, (self.crop_end - self.crop_start) // 10)
            self.event_start = self.crop_start
            self.event_end = min(self.crop_end, self.crop_start + span)

        clipped_regions: list[tuple[int, int]] = []
        for start, end in self.burst_regions:
            start = max(self.crop_start, start)
            end = min(self.crop_end, end)
            if end - start >= 2:
                clipped_regions.append((start, end))
        self.burst_regions = clipped_regions

    def reset_view(self) -> None:
        self.time_factor = default_time_factor(self.total_time_bins)
        self.freq_factor = 1
        self.crop_start = 0
        self.crop_end = self.total_time_bins
        self.event_start = 2 * (self.total_time_bins // 10)
        self.event_end = 3 * (self.total_time_bins // 10)
        self.burst_regions = []
        self.peak_positions = []
        self.manual_peaks = False
        self.invalidate_results()

    def set_time_factor(self, factor: int) -> None:
        self.time_factor = max(1, min(int(factor), self.crop_end - self.crop_start))

    def set_freq_factor(self, factor: int) -> None:
        self.freq_factor = max(1, min(int(factor), self.total_channels))

    def set_crop_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        self.crop_start = self.clamp_bin(start)
        self.crop_end = self.clamp_bin(end, is_end=True)
        self._sync_selections_to_crop()
        self.invalidate_results()

    def set_event_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        self.event_start = max(self.crop_start, self.clamp_bin(start))
        self.event_end = min(self.crop_end, self.clamp_bin(end, is_end=True))
        if self.event_end <= self.event_start:
            self.event_end = min(self.crop_end, self.event_start + 2)
        self.invalidate_results()

    def add_region_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        start = max(self.crop_start, self.clamp_bin(start))
        end = min(self.crop_end, self.clamp_bin(end, is_end=True))
        if end - start >= 2:
            self.burst_regions.append((start, end))
            self.invalidate_results()

    def clear_regions(self) -> None:
        self.burst_regions = []
        self.invalidate_results()

    def add_peak_ms(self, time_ms: float) -> None:
        peak = self.clamp_bin(self.ms_to_bin(time_ms))
        if peak not in self.peak_positions:
            self.manual_peaks = True
            self.peak_positions.append(peak)
            self.peak_positions.sort()
            self.invalidate_results()

    def remove_peak_ms(self, time_ms: float, tolerance_bins: int = 40) -> None:
        if not self.peak_positions:
            return
        target = self.ms_to_bin(time_ms)
        distances = [abs(peak - target) for peak in self.peak_positions]
        index = int(np.argmin(distances))
        if distances[index] <= tolerance_bins:
            self.peak_positions.pop(index)
            self.manual_peaks = bool(self.peak_positions)
            self.invalidate_results()

    def _mask_batch(self, channels: list[int]) -> None:
        added: list[int] = []
        for chan in sorted(set(self.clamp_channel(chan) for chan in channels)):
            if not self.channel_mask[chan]:
                self.channel_mask[chan] = True
                added.append(chan)
        if added:
            self.mask_history.append(added)
            self.invalidate_results()

    def mask_channel_freq(self, freq_mhz: float) -> None:
        self._mask_batch([self.freq_to_channel(freq_mhz)])

    def mask_range_freq(self, low_freq_mhz: float, high_freq_mhz: float) -> None:
        low = self.freq_to_channel(min(low_freq_mhz, high_freq_mhz))
        high = self.freq_to_channel(max(low_freq_mhz, high_freq_mhz))
        self._mask_batch(list(range(low, high + 1)))

    def undo_mask(self) -> None:
        if not self.mask_history:
            return
        latest = self.mask_history.pop()
        for chan in latest:
            self.channel_mask[chan] = False
        self.invalidate_results()

    def reset_mask(self) -> None:
        self.channel_mask[:] = False
        self.mask_history = []
        self.invalidate_results()

    def set_spectral_extent_freq(self, low_freq_mhz: float, high_freq_mhz: float) -> None:
        low = self.freq_to_channel(min(low_freq_mhz, high_freq_mhz))
        high = self.freq_to_channel(max(low_freq_mhz, high_freq_mhz))
        self.spec_ex_lo, self.spec_ex_hi = low, high
        self.invalidate_results()

    def auto_mask_jess(self) -> None:
        if jess is None:
            raise RuntimeError("Jess is not installed in the active environment.")

        masked = self.get_masked_crop()
        rel_start = max(0, self.event_start - self.crop_start)
        rel_end = max(rel_start + 1, min(masked.shape[1], self.event_end - self.crop_start))
        offburst_parts = [masked[:, :rel_start], masked[:, rel_end:]]
        offburst = np.concatenate([part for part in offburst_parts if part.size], axis=1)
        if offburst.size == 0:
            offburst = masked
        filled = np.nan_to_num(offburst, nan=float(np.nanmedian(offburst)))
        bool_mask = jess.channel_masks.channel_masker(
            dynamic_spectra=filled.T,
            test="skew",
            sigma=3,
            show_plots=False,
        )
        channels = [idx for idx, flag in enumerate(bool_mask) if flag]
        self._mask_batch(channels)

    def set_dm(self, new_dm: float) -> None:
        new_dm = float(new_dm)
        if new_dm == self.dm:
            return
        delta_dm = new_dm - self.dm
        self.data = dedisperse(self.data, delta_dm, self.freqs, self.tsamp)
        self.config = ObservationConfig(
            dm=new_dm,
            preset_key=self.config.preset_key,
            telescope_label=self.config.telescope_label,
            sefd_jy=self.config.sefd_jy,
            read_start_sec=self.config.read_start_sec,
            initial_crop_sec=self.config.initial_crop_sec,
            normalization_tail_fraction=self.config.normalization_tail_fraction,
            distance_mpc=self.config.distance_mpc,
            redshift=self.config.redshift,
        )
        self.invalidate_results()

    def _compute_profiles(self) -> tuple[np.ndarray, np.ndarray]:
        masked = self.get_masked_crop()
        rel_start = max(0, self.event_start - self.crop_start)
        rel_end = max(rel_start + 1, min(masked.shape[1], self.event_end - self.crop_start))

        prof = _nanmean_profile(masked)
        offprof = np.concatenate([prof[:rel_start], prof[rel_end:]])
        prof = _safe_normalize(prof, offprof)

        burst_only = _nanmean_profile(masked[self.spec_ex_lo : self.spec_ex_hi + 1, :])
        offburst = np.concatenate([burst_only[:rel_start], burst_only[rel_end:]])
        burst_only = _safe_normalize(burst_only, offburst)
        return prof, burst_only

    def _effective_selected_bandwidth_mhz(self) -> float:
        masked = self.get_masked_crop()
        selected = masked[self.spec_ex_lo : self.spec_ex_hi + 1, :]
        if selected.size == 0:
            return 0.0
        active_channels = int(np.isfinite(selected).any(axis=1).sum())
        return float(active_channels * self.freqres)

    def compute_properties(self) -> BurstMeasurements:
        prof, burst_only_prof = self._compute_profiles()
        masked = self.get_masked_crop()
        zero_time_ms = (self.crop_start + np.arange(masked.shape[1])) * self.tsamp_ms

        gaussian_fits: list[GaussianFit1D] = []
        for start, end in self.burst_regions:
            rel_start = max(0, start - self.crop_start)
            rel_end = min(masked.shape[1], end - self.crop_start)
            if rel_end - rel_start < 4:
                continue
            xdata = zero_time_ms[rel_start:rel_end]
            ydata = prof[rel_start:rel_end]
            initial_guess = (
                float(np.nanmax(ydata)),
                float(xdata[int(np.nanargmax(ydata))]),
                float(max(xdata[1] - xdata[0], self.tsamp_ms)),
                0.0,
            )
            bounds = ([0.0, xdata[0], 0.0, -10.0], [np.inf, xdata[-1], xdata[-1] - xdata[0], 10.0])
            try:
                popt, _ = curve_fit(
                    gaussian_1d,
                    xdata,
                    ydata,
                    p0=initial_guess,
                    bounds=bounds,
                    maxfev=10000,
                )
                gaussian_fits.append(
                    GaussianFit1D(
                        amp=float(popt[0]),
                        mu_ms=float(popt[1]),
                        sigma_ms=float(popt[2]),
                        offset=float(popt[3]),
                    )
                )
            except Exception:
                continue

        peak_positions = self._current_peak_positions()
        peak_ms = [self.bin_to_ms(peak) for peak in peak_positions]
        plus_mjd_sec_updated = self.plus_mjd_sec + (peak_positions[0] * self.tsamp if peak_positions else 0.0)
        mjd_at_peak = self.start_mjd + (plus_mjd_sec_updated / (24 * 3600))

        rel_start = max(0, self.event_start - self.crop_start)
        rel_end = max(rel_start + 1, min(masked.shape[1], self.event_end - self.crop_start))
        integrated_sn = burst_only_prof[rel_start:rel_end]
        effective_bw_mhz = self._effective_selected_bandwidth_mhz()

        peak_flux_jy = None
        fluence_jyms = None
        iso_e = None
        if self.sefd is not None and effective_bw_mhz > 0:
            flux_prof = integrated_sn * radiometer(self.tsamp_ms, effective_bw_mhz, self.npol, self.sefd)
            peak_flux_jy = float(np.nanmax(flux_prof)) if flux_prof.size else 0.0
            fluence_jyms = float(np.nansum(flux_prof * self.tsamp_ms)) if flux_prof.size else 0.0
            if self.config.distance_mpc and self.config.redshift:
                iso_e = (
                    4
                    * np.pi
                    * fluence_jyms
                    * u.Jy
                    * u.ms
                    * effective_bw_mhz
                    * u.MHz
                    * (self.config.distance_mpc * u.megaparsec) ** 2
                    / (1 + self.config.redshift)
                ).to_value()

        self.results = BurstMeasurements(
            burst_name=Path(self.burst_file).stem,
            dm=self.dm,
            mjd_at_peak=float(mjd_at_peak),
            peak_positions_ms=[float(value) for value in peak_ms],
            peak_flux_jy=peak_flux_jy,
            fluence_jyms=fluence_jyms,
            event_duration_ms=float((self.event_end - self.event_start) * self.tsamp_ms),
            spectral_extent_mhz=float(self.freqs[self.spec_ex_hi] - self.freqs[self.spec_ex_lo]),
            gaussian_fits=gaussian_fits,
            mask_count=int(self.channel_mask.sum()),
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
            integrated_sn=np.asarray(integrated_sn),
            time_profile_sn=np.asarray(prof),
            burst_only_profile_sn=np.asarray(burst_only_prof),
            time_axis_ms=np.asarray(zero_time_ms),
            iso_e=iso_e,
        )
        return self.results
