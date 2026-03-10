from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import warnings

import numpy as np

from flits.measurements import build_measurement_context, compute_burst_measurements, event_snr
from flits.models import BurstMeasurements, DmOptimizationResult, FilterbankMetadata
from flits.settings import ObservationConfig, get_auto_mask_profile, get_preset
from flits.signal import block_reduce_mean, dedisperse

try:
    import jess.channel_masks as _jess_channel_masks
    jess = SimpleNamespace(channel_masks=_jess_channel_masks)
except Exception:  # pragma: no cover - optional dependency
    jess = SimpleNamespace(channel_masks=SimpleNamespace(channel_masker=None))


JESS_MASK_DTYPE = np.float32
JESS_WORKING_COPY_FACTOR = 6
JESS_TEST_SEQUENCE = ("skew", "stand-dev")


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


def _adaptive_jess_time_bin_limit(
    eligible_channels: int,
    memory_budget_mb: int,
    *,
    dtype: np.dtype[Any] = np.dtype(JESS_MASK_DTYPE),
    working_copy_factor: int = JESS_WORKING_COPY_FACTOR,
) -> int:
    eligible_channels = max(0, int(eligible_channels))
    if eligible_channels == 0:
        return 0
    budget_bytes = max(1, int(float(memory_budget_mb) * 1024 * 1024))
    bytes_per_time_bin = eligible_channels * int(dtype.itemsize) * max(1, int(working_copy_factor))
    return max(1, budget_bytes // bytes_per_time_bin)


def _sample_time_bins_evenly(candidate_bins: np.ndarray, max_samples: int) -> np.ndarray:
    if candidate_bins.size == 0 or max_samples <= 0:
        return np.array([], dtype=int)
    if candidate_bins.size <= max_samples:
        return candidate_bins.astype(int, copy=False)
    sample_positions = np.linspace(0, candidate_bins.size - 1, num=max_samples, dtype=int)
    return candidate_bins[sample_positions]


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
    last_auto_mask: AutoMaskRunSummary | None = None
    results: BurstMeasurements | None = None
    dm_optimization: DmOptimizationResult | None = None

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
        auto_mask_profile: str | None = "auto",
        distance_mpc: float | None = None,
        redshift: float | None = None,
    ) -> "BurstSession":
        from flits.io import inspect_filterbank, load_filterbank_data

        inspection = inspect_filterbank(bfile)
        preset_key = telescope if telescope is not None else inspection.detected_preset_key
        config = ObservationConfig.from_preset(
            dm=dm,
            preset_key=preset_key,
            sefd_jy=sefd_jy,
            read_start_sec=read_start_sec,
            initial_crop_sec=initial_crop_sec,
            auto_mask_profile=auto_mask_profile,
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

    def clear_dm_optimization(self) -> None:
        self.dm_optimization = None

    def invalidate_selection_state(self) -> None:
        self.invalidate_results()
        self.clear_dm_optimization()

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

    def _ordered_channel_bounds(self, start: int, end: int) -> tuple[int, int]:
        return tuple(sorted((self.clamp_channel(start), self.clamp_channel(end))))

    def _channel_bounds_for_freqs(self, low_freq_mhz: float, high_freq_mhz: float) -> tuple[int, int]:
        return self._ordered_channel_bounds(
            self.freq_to_channel(low_freq_mhz),
            self.freq_to_channel(high_freq_mhz),
        )

    def _selected_channel_bounds(self) -> tuple[int, int]:
        return self._ordered_channel_bounds(self.spec_ex_lo, self.spec_ex_hi)

    def _selected_frequency_bounds_mhz(self) -> tuple[float, float]:
        low_chan, high_chan = self._selected_channel_bounds()
        return tuple(sorted((float(self.freqs[low_chan]), float(self.freqs[high_chan]))))

    def _frequency_range_mhz(self) -> tuple[float, float]:
        return float(np.min(self.freqs)), float(np.max(self.freqs))

    def _current_peak_positions(self, display: np.ndarray | None = None) -> list[int]:
        if self.manual_peaks and self.peak_positions:
            return sorted(p for p in self.peak_positions if self.crop_start <= p < self.crop_end)

        display = self.get_display_crop() if display is None else display
        if display.size == 0:
            return []
        profile = np.nansum(display, axis=0)
        if not np.isfinite(profile).any():
            return []
        peak_local = int(np.nanargmax(profile))
        return [self.crop_start + peak_local]

    def get_masked_crop(self, data: np.ndarray | None = None) -> np.ndarray:
        source = self.data if data is None else data
        arr = np.array(source[:, self.crop_start:self.crop_end], copy=True)
        if not np.issubdtype(arr.dtype, np.floating):
            arr = arr.astype(np.float32, copy=False)
        if self.channel_mask is not None and self.channel_mask.any():
            arr[self.channel_mask, :] = np.nan
        return arr

    def get_display_crop(self, data: np.ndarray | None = None) -> np.ndarray:
        display = self.get_masked_crop(data)
        if display.size == 0:
            return display

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            row_means = np.nanmean(display, axis=1, keepdims=True)
        row_means = np.where(np.isfinite(row_means), row_means, 0.0).astype(display.dtype, copy=False)
        display -= row_means
        return display

    def _event_bounds_in_crop(self, time_bins: int) -> tuple[int, int]:
        rel_start = max(0, self.event_start - self.crop_start)
        rel_end = max(rel_start + 1, min(time_bins, self.event_end - self.crop_start))
        return rel_start, rel_end

    def _compute_profiles_for_data(self, data: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
        masked = self.get_masked_crop(data)
        rel_start, rel_end = self._event_bounds_in_crop(masked.shape[1])
        spec_lo, spec_hi = self._selected_channel_bounds()
        context = build_measurement_context(
            masked=masked,
            time_axis_ms=(self.crop_start + np.arange(masked.shape[1], dtype=float)) * self.tsamp_ms,
            freqs_mhz=self.freqs,
            event_rel_start=rel_start,
            event_rel_end=rel_end,
            spec_lo=spec_lo,
            spec_hi=spec_hi,
            freqres_mhz=self.freqres,
        )
        return masked, context.time_profile_sn, context.selected_profile_sn, rel_start, rel_end

    def _score_event_sn(self, burst_only_profile: np.ndarray, rel_start: int, rel_end: int) -> float:
        return event_snr(burst_only_profile, rel_start, rel_end)

    def get_view(self) -> dict[str, Any]:
        display = self.get_display_crop()
        reduced = block_reduce_mean(display, tfac=self.time_factor, ffac=self.freq_factor)
        freq_lo_mhz, freq_hi_mhz = self._frequency_range_mhz()
        spec_lo_mhz, spec_hi_mhz = self._selected_frequency_bounds_mhz()

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
        peak_positions = self._current_peak_positions(display=display)
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
                "auto_mask_profile": self.config.auto_mask_profile,
                "auto_mask_profile_label": get_auto_mask_profile(self.config.auto_mask_profile).label,
                "tsamp_us": self.tsamp * 1e6,
                "freqres_mhz": self.freqres,
                "npol": self.npol,
                "distance_mpc": self.config.distance_mpc,
                "redshift": self.config.redshift,
                "shape": [self.total_channels, self.total_time_bins],
                "view_shape": [int(reduced.shape[0]), int(reduced.shape[1])],
                "freq_range_mhz": [freq_lo_mhz, freq_hi_mhz],
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
                "spectral_extent_mhz": [spec_lo_mhz, spec_hi_mhz],
                "masked_channels": np.flatnonzero(self.channel_mask).astype(int).tolist(),
                "last_auto_mask": self.last_auto_mask.to_dict() if self.last_auto_mask is not None else None,
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
            "dm_optimization": self.dm_optimization.to_dict() if self.dm_optimization is not None else None,
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
        self.invalidate_selection_state()

    def set_time_factor(self, factor: int) -> None:
        self.time_factor = max(1, min(int(factor), self.crop_end - self.crop_start))

    def set_freq_factor(self, factor: int) -> None:
        self.freq_factor = max(1, min(int(factor), self.total_channels))

    def set_crop_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        self.crop_start = self.clamp_bin(start)
        self.crop_end = self.clamp_bin(end, is_end=True)
        self._sync_selections_to_crop()
        self.invalidate_selection_state()

    def set_event_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        self.event_start = max(self.crop_start, self.clamp_bin(start))
        self.event_end = min(self.crop_end, self.clamp_bin(end, is_end=True))
        if self.event_end <= self.event_start:
            self.event_end = min(self.crop_end, self.event_start + 2)
        self.invalidate_selection_state()

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
            self.invalidate_selection_state()

    def mask_channel_freq(self, freq_mhz: float) -> None:
        self._mask_batch([self.freq_to_channel(freq_mhz)])

    def mask_range_freq(self, low_freq_mhz: float, high_freq_mhz: float) -> None:
        low, high = self._channel_bounds_for_freqs(low_freq_mhz, high_freq_mhz)
        self._mask_batch(list(range(low, high + 1)))

    def undo_mask(self) -> None:
        if not self.mask_history:
            return
        latest = self.mask_history.pop()
        for chan in latest:
            self.channel_mask[chan] = False
        self.invalidate_selection_state()

    def reset_mask(self) -> None:
        self.channel_mask[:] = False
        self.mask_history = []
        self.invalidate_selection_state()

    def set_spectral_extent_freq(self, low_freq_mhz: float, high_freq_mhz: float) -> None:
        low, high = self._channel_bounds_for_freqs(low_freq_mhz, high_freq_mhz)
        self.spec_ex_lo, self.spec_ex_hi = low, high
        self.invalidate_selection_state()

    def auto_mask_jess(self, profile: str | None = None) -> None:
        if jess.channel_masks.channel_masker is None:
            raise RuntimeError("Jess is not installed in the active environment.")

        mask_profile = get_auto_mask_profile(self.config.auto_mask_profile if profile is None else profile)
        if mask_profile.key != self.config.auto_mask_profile:
            self.config = replace(self.config, auto_mask_profile=mask_profile.key)
        masked = self.get_masked_crop()
        if masked.size == 0:
            self.last_auto_mask = AutoMaskRunSummary(
                profile=mask_profile.key,
                profile_label=mask_profile.label,
                memory_budget_mb=mask_profile.memory_budget_mb,
                candidate_time_bins=0,
                sampled_time_bins=0,
                eligible_channels=0,
                constant_channel_count=0,
                detected_channel_count=0,
                added_channel_count=0,
                test_used=None,
                tests_tried=(),
            )
            return

        rel_start, rel_end = self._event_bounds_in_crop(masked.shape[1])
        candidate_bins = np.concatenate(
            [
                np.arange(0, rel_start, dtype=int),
                np.arange(rel_end, masked.shape[1], dtype=int),
            ]
        )
        if candidate_bins.size == 0:
            candidate_bins = np.arange(masked.shape[1], dtype=int)

        eligible_channels = np.flatnonzero(~self.channel_mask) if self.channel_mask is not None else np.arange(masked.shape[0], dtype=int)
        max_samples = _adaptive_jess_time_bin_limit(eligible_channels.size, mask_profile.memory_budget_mb)
        sampled_bins = _sample_time_bins_evenly(candidate_bins, max_samples)
        if eligible_channels.size == 0 or sampled_bins.size == 0:
            self.last_auto_mask = AutoMaskRunSummary(
                profile=mask_profile.key,
                profile_label=mask_profile.label,
                memory_budget_mb=mask_profile.memory_budget_mb,
                candidate_time_bins=int(candidate_bins.size),
                sampled_time_bins=int(sampled_bins.size),
                eligible_channels=int(eligible_channels.size),
                constant_channel_count=0,
                detected_channel_count=0,
                added_channel_count=0,
                test_used=None,
                tests_tried=(),
            )
            return

        offburst = masked[eligible_channels][:, sampled_bins]
        fill_value = float(np.nanmedian(offburst)) if np.isfinite(offburst).any() else 0.0
        filled = np.nan_to_num(offburst.astype(JESS_MASK_DTYPE, copy=False), nan=fill_value)

        channel_sigma = np.nanstd(filled, axis=1)
        variable_local = np.isfinite(channel_sigma) & (channel_sigma > 0)
        constant_channels = eligible_channels[~variable_local].astype(int)
        active_channels = eligible_channels[variable_local].astype(int)

        tests_tried: list[str] = []
        detected_channels = constant_channels.tolist()
        test_used: str | None = "constant" if constant_channels.size else None
        if active_channels.size:
            active_spectra = filled[variable_local, :].T
            for test_name in JESS_TEST_SEQUENCE:
                tests_tried.append(test_name)
                bool_mask = np.atleast_1d(
                    np.asarray(
                        jess.channel_masks.channel_masker(
                            dynamic_spectra=active_spectra,
                            test=test_name,
                            sigma=3,
                            show_plots=False,
                        ),
                        dtype=bool,
                    )
                )
                if bool_mask.shape != (active_channels.size,):
                    raise RuntimeError("Jess returned an unexpected mask shape.")
                detected_channels.extend(active_channels[bool_mask].astype(int).tolist())
                test_used = test_name
                if bool_mask.any():
                    break

        previous_mask = self.channel_mask.copy() if self.channel_mask is not None else np.zeros(self.total_channels, dtype=bool)
        channels = sorted(set(detected_channels))
        self._mask_batch(channels)
        added_channel_count = int(np.count_nonzero(self.channel_mask & ~previous_mask)) if self.channel_mask is not None else 0
        self.last_auto_mask = AutoMaskRunSummary(
            profile=mask_profile.key,
            profile_label=mask_profile.label,
            memory_budget_mb=mask_profile.memory_budget_mb,
            candidate_time_bins=int(candidate_bins.size),
            sampled_time_bins=int(sampled_bins.size),
            eligible_channels=int(eligible_channels.size),
            constant_channel_count=int(constant_channels.size),
            detected_channel_count=len(channels),
            added_channel_count=added_channel_count,
            test_used=test_used,
            tests_tried=tuple(tests_tried),
        )

    def set_dm(self, new_dm: float) -> None:
        new_dm = float(new_dm)
        if new_dm == self.dm:
            return
        delta_dm = new_dm - self.dm
        self.data = dedisperse(self.data, delta_dm, self.freqs, self.tsamp)
        self.config = replace(self.config, dm=new_dm)
        self.invalidate_results()

    def _effective_selected_bandwidth_mhz(self, masked: np.ndarray | None = None) -> float:
        masked = self.get_masked_crop() if masked is None else masked
        spec_lo, spec_hi = self._selected_channel_bounds()
        selected = masked[spec_lo : spec_hi + 1, :]
        if selected.size == 0:
            return 0.0
        active_channels = int(np.isfinite(selected).any(axis=1).sum())
        return float(active_channels * self.freqres)

    def _fit_dm_peak(
        self,
        trial_dms: np.ndarray,
        snr: np.ndarray,
        peak_index: int,
    ) -> tuple[float, float, float | None, str]:
        sampled_best_dm = float(trial_dms[peak_index])
        sampled_best_sn = float(snr[peak_index])

        if peak_index < 2 or peak_index > len(trial_dms) - 3:
            return sampled_best_dm, sampled_best_sn, None, "peak_on_sweep_edge"

        x = np.asarray(trial_dms[peak_index - 2 : peak_index + 3], dtype=float)
        y = np.asarray(snr[peak_index - 2 : peak_index + 3], dtype=float)
        if x.size != 5 or not np.all(np.isfinite(y)):
            return sampled_best_dm, sampled_best_sn, None, "insufficient_peak_window"

        try:
            coeffs = np.polyfit(x, y, 2)
        except Exception:
            return sampled_best_dm, sampled_best_sn, None, "quadratic_fit_failed"

        a, b, c = (float(value) for value in coeffs)
        if not np.all(np.isfinite(coeffs)):
            return sampled_best_dm, sampled_best_sn, None, "quadratic_fit_failed"
        if a >= 0:
            return sampled_best_dm, sampled_best_sn, None, "quadratic_not_concave"

        best_dm = float(-b / (2 * a))
        if best_dm < float(x[0]) or best_dm > float(x[-1]):
            return sampled_best_dm, sampled_best_sn, None, "fit_vertex_outside_peak_window"

        best_sn = float(np.polyval(coeffs, best_dm))
        if not np.isfinite(best_sn):
            return sampled_best_dm, sampled_best_sn, None, "quadratic_fit_failed"

        target_sn = best_sn - 1.0
        uncertainty: float | None = None
        status = "quadratic_peak_fit"
        try:
            roots = np.roots([a, b, c - target_sn])
        except Exception:
            roots = np.array([], dtype=complex)

        real_roots = sorted(
            float(root.real)
            for root in np.atleast_1d(roots)
            if np.isfinite(root.real) and abs(float(root.imag)) < 1e-6
        )
        lower = max((root for root in real_roots if root <= best_dm), default=None)
        upper = min((root for root in real_roots if root >= best_dm), default=None)
        if lower is not None and upper is not None:
            sweep_min = float(np.min(trial_dms))
            sweep_max = float(np.max(trial_dms))
            candidate = 0.5 * (upper - lower)
            if (
                np.isfinite(candidate)
                and candidate > 0
                and lower >= sweep_min
                and upper <= sweep_max
            ):
                uncertainty = float(candidate)
            else:
                status = "quadratic_peak_fit_uncertainty_unavailable"
        else:
            status = "quadratic_peak_fit_uncertainty_unavailable"

        return best_dm, best_sn, uncertainty, status

    def optimize_dm(self, center_dm: float, half_range: float, step: float) -> DmOptimizationResult:
        center_dm = float(center_dm)
        half_range = float(half_range)
        step = float(step)
        if not all(np.isfinite(value) for value in (center_dm, half_range, step)):
            raise ValueError("DM sweep parameters must be finite numbers.")
        if half_range <= 0:
            raise ValueError("DM sweep half-range must be greater than zero.")
        if step <= 0:
            raise ValueError("DM sweep step must be greater than zero.")

        num_side = int(np.floor((half_range / step) + 1e-12))
        num_trials = 2 * num_side + 1
        if num_trials < 5:
            raise ValueError("DM sweep must include at least 5 trial DMs.")
        if num_trials > 121:
            raise ValueError("DM sweep supports at most 121 trial DMs.")

        offsets = np.arange(-num_side, num_side + 1, dtype=float) * step
        trial_dms = np.round(center_dm + offsets, 12)
        actual_half_range = float(abs(offsets[-1])) if offsets.size else 0.0

        snr = np.empty(trial_dms.size, dtype=float)
        for idx, trial_dm in enumerate(trial_dms):
            if np.isclose(trial_dm, self.dm):
                trial_data = self.data
            else:
                trial_data = dedisperse(self.data, float(trial_dm - self.dm), self.freqs, self.tsamp)
            _, _, burst_only, rel_start, rel_end = self._compute_profiles_for_data(trial_data)
            snr[idx] = self._score_event_sn(burst_only, rel_start, rel_end)

        if not np.isfinite(snr).any():
            raise ValueError("Unable to compute DM sweep for the current selection.")

        peak_index = int(np.nanargmax(snr))
        sampled_best_dm = float(trial_dms[peak_index])
        sampled_best_sn = float(snr[peak_index])
        best_dm, best_sn, best_dm_uncertainty, fit_status = self._fit_dm_peak(trial_dms, snr, peak_index)

        self.dm_optimization = DmOptimizationResult(
            center_dm=center_dm,
            requested_half_range=half_range,
            actual_half_range=actual_half_range,
            step=step,
            trial_dms=trial_dms,
            snr=snr,
            sampled_best_dm=sampled_best_dm,
            sampled_best_sn=sampled_best_sn,
            best_dm=best_dm,
            best_dm_uncertainty=best_dm_uncertainty,
            best_sn=best_sn,
            fit_status=fit_status,
        )
        return self.dm_optimization

    def compute_properties(self) -> BurstMeasurements:
        masked = self.get_masked_crop()
        event_rel_start, event_rel_end = self._event_bounds_in_crop(masked.shape[1])
        spec_lo, spec_hi = self._selected_channel_bounds()
        self.results = compute_burst_measurements(
            burst_name=Path(self.burst_file).stem,
            dm=self.dm,
            start_mjd=self.start_mjd,
            read_start_sec=self.plus_mjd_sec,
            crop_start_bin=self.crop_start,
            tsamp_ms=self.tsamp_ms,
            freqres_mhz=self.freqres,
            freqs_mhz=self.freqs,
            masked=masked,
            event_rel_start=event_rel_start,
            event_rel_end=event_rel_end,
            spec_lo=spec_lo,
            spec_hi=spec_hi,
            peak_bins_abs=self._current_peak_positions(),
            burst_regions_abs=tuple(self.burst_regions),
            manual_selection=bool(self.manual_peaks or self.burst_regions),
            manual_peak_selection=bool(self.manual_peaks),
            sefd_jy=self.sefd,
            npol=self.npol,
            distance_mpc=self.config.distance_mpc,
            redshift=self.config.redshift,
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
        )
        return self.results
