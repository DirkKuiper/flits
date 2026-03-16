from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
import warnings

import numpy as np

from flits.analysis.dm import available_dm_metrics, optimize_dm_trials
from flits.analysis.spectral import run_averaged_spectral_analysis
from flits.analysis.widths import compute_width_analysis
from flits.exports import MAX_EXPORT_SNAPSHOTS, StoredExportSnapshot, create_export_snapshot
from flits.fitburst_adapter import fit_scattering_selected_band
from flits.measurements import (
    MeasurementContext,
    _offpulse_windows_ms,
    _primary_peak_bin,
    build_measurement_context,
    compute_burst_measurements,
    compute_subband_arrival_residuals,
    event_snr,
)
from flits.models import (
    AcceptedWidthSelection,
    AnalysisSessionSnapshot,
    AutoMaskRunSummary,
    BurstRegion,
    BurstMeasurements,
    DmComponentOptimizationResult,
    DmOptimizationProvenance,
    DmOptimizationResult,
    ExportArtifact,
    ExportManifest,
    FilterbankMetadata,
    NoiseEstimateSettings,
    OffPulseRegion,
    SessionSourceRef,
    SpectralAnalysisResult,
    WidthAnalysisSettings,
    WidthAnalysisSummary,
)
from flits.settings import ObservationConfig, get_auto_mask_profile, get_preset
from flits.signal import block_reduce_mean, dedisperse

try:
    import jess.channel_masks as _jess_channel_masks

    jess = SimpleNamespace(channel_masks=_jess_channel_masks)
except Exception:  # pragma: no cover - optional dependency
    jess = SimpleNamespace(channel_masks=SimpleNamespace(channel_masker=None))


SESSION_SNAPSHOT_SCHEMA_VERSION = "1.0"
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


def _coerce_snapshot(snapshot: AnalysisSessionSnapshot | dict[str, Any]) -> AnalysisSessionSnapshot:
    if isinstance(snapshot, AnalysisSessionSnapshot):
        return snapshot
    return AnalysisSessionSnapshot.from_dict(snapshot)


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
    offpulse_regions: list[tuple[int, int]] = field(default_factory=list)
    peak_positions: list[int] = field(default_factory=list)
    manual_peaks: bool = False
    channel_mask: np.ndarray | None = None
    mask_history: list[list[int]] = field(default_factory=list)
    last_auto_mask: AutoMaskRunSummary | None = None
    noise_settings: NoiseEstimateSettings = field(default_factory=NoiseEstimateSettings)
    width_settings: WidthAnalysisSettings = field(default_factory=WidthAnalysisSettings)
    width_analysis: WidthAnalysisSummary | None = None
    notes: str | None = None
    results: BurstMeasurements | None = None
    dm_optimization: DmOptimizationResult | None = None
    spectral_analysis: SpectralAnalysisResult | None = None
    export_snapshots: dict[str, StoredExportSnapshot] = field(default_factory=dict)
    export_order: list[str] = field(default_factory=list)

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

    @classmethod
    def from_snapshot(
        cls,
        snapshot: AnalysisSessionSnapshot | dict[str, Any],
        *,
        loader: Callable[..., "BurstSession"] | None = None,
    ) -> "BurstSession":
        snapshot = _coerce_snapshot(snapshot)
        session_loader = cls.from_file if loader is None else loader
        session = session_loader(
            str(snapshot.source.source_path),
            dm=float(snapshot.dm),
            telescope=snapshot.preset_key,
            sefd_jy=snapshot.sefd_jy,
            read_start_sec=snapshot.read_start_sec,
            initial_crop_sec=snapshot.initial_crop_sec,
            auto_mask_profile=snapshot.auto_mask_profile,
            distance_mpc=snapshot.distance_mpc,
            redshift=snapshot.redshift,
        )
        session._validate_snapshot_source(snapshot.source)

        if len(snapshot.crop_bins) == 2:
            session.crop_start = session.clamp_bin(snapshot.crop_bins[0])
            session.crop_end = session.clamp_bin(snapshot.crop_bins[1], is_end=True)
        if len(snapshot.event_bins) == 2:
            session.event_start = max(session.crop_start, session.clamp_bin(snapshot.event_bins[0]))
            session.event_end = min(session.crop_end, session.clamp_bin(snapshot.event_bins[1], is_end=True))
        if len(snapshot.spectral_extent_channels) == 2:
            session.spec_ex_lo, session.spec_ex_hi = session._ordered_channel_bounds(
                snapshot.spectral_extent_channels[0],
                snapshot.spectral_extent_channels[1],
            )
        session._sync_selections_to_crop()
        session.time_factor = max(1, min(int(snapshot.time_factor), session.crop_end - session.crop_start))
        session.freq_factor = max(1, min(int(snapshot.freq_factor), session.total_channels))

        session.offpulse_regions = [
            (max(0, int(region.start_bin)), min(session.total_time_bins, int(region.end_bin)))
            for region in snapshot.offpulse_regions
            if int(region.end_bin) - int(region.start_bin) >= 2
        ]
        session.burst_regions = [
            (max(0, int(region.start_bin)), min(session.total_time_bins, int(region.end_bin)))
            for region in snapshot.burst_regions
            if int(region.end_bin) - int(region.start_bin) >= 2
        ]
        session.peak_positions = [
            session.clamp_bin(int(peak)) for peak in snapshot.peak_bins if 0 <= int(peak) < session.total_time_bins
        ]
        session.manual_peaks = bool(snapshot.manual_peaks)
        session.channel_mask[:] = False
        if snapshot.masked_channels:
            session.channel_mask[np.asarray(snapshot.masked_channels, dtype=int)] = True
        session.last_auto_mask = snapshot.last_auto_mask
        session.noise_settings = snapshot.noise_settings
        session.width_settings = snapshot.width_settings
        session.notes = snapshot.notes
        session.results = snapshot.results
        session.width_analysis = snapshot.width_analysis
        session.dm_optimization = snapshot.dm_optimization
        session.spectral_analysis = snapshot.spectral_analysis
        if session.results is not None:
            session._apply_width_analysis_to_results()
        return session

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

    @property
    def total_time_bins(self) -> int:
        return int(self.data.shape[1])

    @property
    def total_channels(self) -> int:
        return int(self.data.shape[0])

    @property
    def tsamp_ms(self) -> float:
        return float(self.tsamp * 1e3)

    def invalidate_results(self) -> None:
        self.results = None

    def clear_width_analysis(self) -> None:
        self.width_analysis = None
        if self.results is not None:
            self.results = replace(self.results, width_results=[], accepted_width=None)

    def clear_dm_optimization(self) -> None:
        self.dm_optimization = None

    def clear_spectral_analysis(self) -> None:
        self.spectral_analysis = None

    def invalidate_analysis_state(self) -> None:
        self.invalidate_results()
        self.clear_width_analysis()
        self.clear_dm_optimization()
        self.clear_spectral_analysis()

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

    def _offpulse_regions_in_crop(self) -> list[tuple[int, int]]:
        clipped: list[tuple[int, int]] = []
        for start, end in self.offpulse_regions:
            rel_start = max(0, int(start) - self.crop_start)
            rel_end = min(self.crop_end - self.crop_start, int(end) - self.crop_start)
            if rel_end - rel_start >= 2:
                clipped.append((rel_start, rel_end))
        return clipped

    def _offpulse_regions_ms(self) -> list[list[float]]:
        return [
            [self.bin_to_ms(start), self.bin_to_ms(end)]
            for start, end in self.offpulse_regions
            if end - start >= 2
        ]

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

    def _build_measurement_context_for_data(
        self,
        data: np.ndarray | None = None,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> tuple[np.ndarray, MeasurementContext]:
        masked = self.get_masked_crop(data)
        if event_bounds_abs is None:
            rel_start, rel_end = self._event_bounds_in_crop(masked.shape[1])
        else:
            start_abs, end_abs = sorted((int(event_bounds_abs[0]), int(event_bounds_abs[1])))
            rel_start = max(0, min(masked.shape[1] - 1, start_abs - self.crop_start))
            rel_end = max(rel_start + 1, min(masked.shape[1], end_abs - self.crop_start))
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
            offpulse_regions=self._offpulse_regions_in_crop(),
            noise_settings=self.noise_settings,
        )
        return masked, context

    def _measurement_context_for_data(
        self,
        data: np.ndarray,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> MeasurementContext:
        _, context = self._build_measurement_context_for_data(data, event_bounds_abs=event_bounds_abs)
        return context

    def _score_event_sn(
        self,
        data: np.ndarray,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> float:
        context = self._measurement_context_for_data(data, event_bounds_abs=event_bounds_abs)
        return event_snr(context.selected_profile_sn, context.event_rel_start, context.event_rel_end)

    def _subband_residuals_for_data(
        self,
        data: np.ndarray,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
        masked, context = self._build_measurement_context_for_data(data, event_bounds_abs=event_bounds_abs)
        diagnostics = compute_subband_arrival_residuals(
            masked=masked,
            time_axis_ms=context.time_axis_ms,
            freqs_mhz=self.freqs,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
            spec_lo=context.spec_lo,
            spec_hi=context.spec_hi,
            freqres_mhz=self.freqres,
            offpulse_regions=self._offpulse_regions_in_crop(),
            noise_settings=self.noise_settings,
        )
        return (
            diagnostics.center_freqs_mhz,
            diagnostics.arrival_times_ms,
            diagnostics.residuals_ms,
            diagnostics.status,
        )

    def _dm_component_windows(self) -> list[tuple[str, tuple[int, int]]]:
        components: list[tuple[str, tuple[int, int]]] = []
        if len(self.burst_regions) >= 2:
            for index, (start, end) in enumerate(sorted(self.burst_regions), start=1):
                start = max(self.crop_start, int(start))
                end = min(self.crop_end, int(end))
                if end - start >= 2:
                    components.append((f"Component {index}", (start, end)))
            return components

        if self.manual_peaks and len(self.peak_positions) >= 2:
            peaks = sorted(
                peak for peak in self.peak_positions if self.crop_start <= int(peak) < self.crop_end
            )
            if len(peaks) < 2:
                return []

            boundaries = [self.event_start]
            boundaries.extend(int(round((left + right) / 2.0)) for left, right in zip(peaks[:-1], peaks[1:]))
            boundaries.append(self.event_end)
            for index, peak in enumerate(peaks, start=1):
                start = max(self.crop_start, boundaries[index - 1])
                end = min(self.crop_end, boundaries[index])
                if end - start >= 2 and start <= peak < end:
                    components.append((f"Peak {index}", (start, end)))
        return components

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
                "dm_metrics": available_dm_metrics(),
            },
            "state": {
                "time_factor": self.time_factor,
                "freq_factor": self.freq_factor,
                "crop_ms": [self.bin_to_ms(self.crop_start), self.bin_to_ms(self.crop_end)],
                "event_ms": [self.bin_to_ms(self.event_start), self.bin_to_ms(self.event_end)],
                "burst_regions_ms": [
                    [self.bin_to_ms(start), self.bin_to_ms(end)] for start, end in self.burst_regions
                ],
                "offpulse_ms": self._offpulse_regions_ms(),
                "peak_ms": [self.bin_to_ms(peak) for peak in peak_positions],
                "manual_peaks": self.manual_peaks,
                "spectral_extent_mhz": [spec_lo_mhz, spec_hi_mhz],
                "masked_channels": np.flatnonzero(self.channel_mask).astype(int).tolist(),
                "last_auto_mask": self.last_auto_mask.to_dict() if self.last_auto_mask is not None else None,
                "notes": self.notes,
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
            "width_analysis": self.width_analysis.to_dict() if self.width_analysis is not None else None,
            "dm_optimization": self.dm_optimization.to_dict() if self.dm_optimization is not None else None,
            "spectral_analysis": (
                None if self.spectral_analysis is None else self.spectral_analysis.to_dict()
            ),
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

        def _clip_regions(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
            clipped: list[tuple[int, int]] = []
            for start, end in regions:
                start = max(self.crop_start, start)
                end = min(self.crop_end, end)
                if end - start >= 2:
                    clipped.append((start, end))
            return clipped

        self.burst_regions = _clip_regions(self.burst_regions)
        self.offpulse_regions = _clip_regions(self.offpulse_regions)
        self.peak_positions = [
            peak for peak in self.peak_positions if self.crop_start <= int(peak) < self.crop_end
        ]
        self.manual_peaks = bool(self.peak_positions) if self.manual_peaks else self.manual_peaks

    def reset_view(self) -> None:
        self.time_factor = default_time_factor(self.total_time_bins)
        self.freq_factor = 1
        self.crop_start = 0
        self.crop_end = self.total_time_bins
        self.event_start = 2 * (self.total_time_bins // 10)
        self.event_end = 3 * (self.total_time_bins // 10)
        self.burst_regions = []
        self.offpulse_regions = []
        self.peak_positions = []
        self.manual_peaks = False
        self.invalidate_analysis_state()

    def set_time_factor(self, factor: int) -> None:
        self.time_factor = max(1, min(int(factor), self.crop_end - self.crop_start))

    def set_freq_factor(self, factor: int) -> None:
        self.freq_factor = max(1, min(int(factor), self.total_channels))

    def set_crop_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        self.crop_start = self.clamp_bin(start)
        self.crop_end = self.clamp_bin(end, is_end=True)
        self._sync_selections_to_crop()
        self.invalidate_analysis_state()

    def set_event_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        self.event_start = max(self.crop_start, self.clamp_bin(start))
        self.event_end = min(self.crop_end, self.clamp_bin(end, is_end=True))
        if self.event_end <= self.event_start:
            self.event_end = min(self.crop_end, self.event_start + 2)
        self.invalidate_analysis_state()

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

    def add_offpulse_ms(self, start_ms: float, end_ms: float) -> None:
        start, end = sorted((self.ms_to_bin(start_ms), self.ms_to_bin(end_ms)))
        start = max(self.crop_start, self.clamp_bin(start))
        end = min(self.crop_end, self.clamp_bin(end, is_end=True))
        if end - start >= 2:
            self.offpulse_regions.append((start, end))
            self.offpulse_regions = sorted(set(self.offpulse_regions))
            self.invalidate_analysis_state()

    def clear_offpulse(self) -> None:
        self.offpulse_regions = []
        self.invalidate_analysis_state()

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
            self.invalidate_analysis_state()

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
        self.invalidate_analysis_state()

    def reset_mask(self) -> None:
        self.channel_mask[:] = False
        self.mask_history = []
        self.invalidate_analysis_state()

    def set_spectral_extent_freq(self, low_freq_mhz: float, high_freq_mhz: float) -> None:
        low, high = self._channel_bounds_for_freqs(low_freq_mhz, high_freq_mhz)
        self.spec_ex_lo, self.spec_ex_hi = low, high
        self.invalidate_analysis_state()

    def set_notes(self, notes: str | None) -> None:
        text = None if notes is None else str(notes).strip()
        self.notes = text or None

    def _current_event_window_ms(self, context: MeasurementContext | None = None) -> tuple[float, float]:
        if context is not None and context.time_axis_ms.size and context.event_rel_end > context.event_rel_start:
            return (
                float(context.time_axis_ms[context.event_rel_start]),
                float(context.time_axis_ms[context.event_rel_end - 1] + self.tsamp_ms),
            )
        return (self.bin_to_ms(self.event_start), self.bin_to_ms(self.event_end))

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

        explicit_offpulse = self._offpulse_regions_in_crop()
        if explicit_offpulse:
            candidate_bins = np.concatenate(
                [np.arange(start, end, dtype=int) for start, end in explicit_offpulse if end > start]
            )
        else:
            rel_start, rel_end = self._event_bounds_in_crop(masked.shape[1])
            candidate_bins = np.concatenate(
                [
                    np.arange(0, rel_start, dtype=int),
                    np.arange(rel_end, masked.shape[1], dtype=int),
                ]
            )
        if candidate_bins.size == 0:
            candidate_bins = np.arange(masked.shape[1], dtype=int)

        eligible_channels = (
            np.flatnonzero(~self.channel_mask)
            if self.channel_mask is not None
            else np.arange(masked.shape[0], dtype=int)
        )
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

        previous_mask = (
            self.channel_mask.copy()
            if self.channel_mask is not None
            else np.zeros(self.total_channels, dtype=bool)
        )
        channels = sorted(set(detected_channels))
        self._mask_batch(channels)
        added_channel_count = (
            int(np.count_nonzero(self.channel_mask & ~previous_mask))
            if self.channel_mask is not None
            else 0
        )
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
        self.invalidate_analysis_state()

    def _dm_provenance(self, context: MeasurementContext) -> DmOptimizationProvenance:
        return DmOptimizationProvenance(
            event_window_ms=[
                float(context.time_axis_ms[context.event_rel_start]) if context.time_axis_ms.size else 0.0,
                float(context.time_axis_ms[context.event_rel_end - 1] + self.tsamp_ms)
                if context.time_axis_ms.size and context.event_rel_end > context.event_rel_start
                else 0.0,
            ],
            spectral_extent_mhz=[
                float(np.min(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
                float(np.max(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
            ],
            offpulse_windows_ms=_offpulse_windows_ms(
                offpulse_bins=context.offpulse_bins,
                time_axis_ms=context.time_axis_ms,
                tsamp_ms=self.tsamp_ms,
            ),
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
            effective_bandwidth_mhz=float(context.effective_bandwidth_mhz),
            tsamp_ms=float(self.tsamp_ms),
            freqres_mhz=float(abs(self.freqres)),
            algorithm_name="dm_trial_sweep",
            warning_flags=list(context.noise_summary.warning_flags),
        )

    def compute_widths(self) -> WidthAnalysisSummary:
        _, context = self._build_measurement_context_for_data()
        existing_method = None
        if self.width_analysis is not None and self.width_analysis.accepted_width is not None:
            existing_method = self.width_analysis.accepted_width.method
        self.width_analysis = compute_width_analysis(
            selected_profile=context.selected_profile_baselined,
            time_axis_ms=context.time_axis_ms,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
            tsamp_ms=self.tsamp_ms,
            noise_summary=context.noise_summary,
            settings=self.width_settings,
            event_window_ms=[
                float(context.time_axis_ms[context.event_rel_start]) if context.time_axis_ms.size else 0.0,
                float(context.time_axis_ms[context.event_rel_end - 1] + self.tsamp_ms)
                if context.time_axis_ms.size and context.event_rel_end > context.event_rel_start
                else 0.0,
            ],
            spectral_extent_mhz=[
                float(np.min(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
                float(np.max(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
            ],
            offpulse_windows_ms=_offpulse_windows_ms(
                offpulse_bins=context.offpulse_bins,
                time_axis_ms=context.time_axis_ms,
                tsamp_ms=self.tsamp_ms,
            ),
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
            effective_bandwidth_mhz=context.effective_bandwidth_mhz,
            existing_accepted_method=existing_method,
        )
        self._apply_width_analysis_to_results()
        return self.width_analysis

    def accept_width_result(self, method: str) -> WidthAnalysisSummary:
        if self.width_analysis is None:
            self.compute_widths()
        assert self.width_analysis is not None
        selection = next((result for result in self.width_analysis.results if result.method == method), None)
        if selection is None:
            raise ValueError(f"Unknown width method '{method}'.")
        self.width_analysis = replace(
            self.width_analysis,
            accepted_width=AcceptedWidthSelection.from_result(selection),
        )
        self._apply_width_analysis_to_results()
        return self.width_analysis

    def optimize_dm(
        self,
        center_dm: float,
        half_range: float,
        step: float,
        metric: str = "integrated_event_snr",
    ) -> DmOptimizationResult:
        _, context = self._build_measurement_context_for_data(self.data)
        optimization = optimize_dm_trials(
            data=self.data,
            current_dm=float(self.dm),
            freqs_mhz=self.freqs,
            tsamp_sec=self.tsamp,
            center_dm=float(center_dm),
            half_range=float(half_range),
            step=float(step),
            context_builder=lambda data: self._measurement_context_for_data(data),
            residuals=lambda data: self._subband_residuals_for_data(data),
            provenance=self._dm_provenance(context),
            metric=metric,
        )
        component_results: list[DmComponentOptimizationResult] = []
        for index, (label, event_bounds_abs) in enumerate(self._dm_component_windows(), start=1):
            component_context = self._measurement_context_for_data(
                self.data,
                event_bounds_abs=event_bounds_abs,
            )
            component_optimization = optimize_dm_trials(
                data=self.data,
                current_dm=float(self.dm),
                freqs_mhz=self.freqs,
                tsamp_sec=self.tsamp,
                center_dm=float(center_dm),
                half_range=float(half_range),
                step=float(step),
                context_builder=lambda data, bounds=event_bounds_abs: self._measurement_context_for_data(
                    data,
                    event_bounds_abs=bounds,
                ),
                residuals=lambda data, bounds=event_bounds_abs: self._subband_residuals_for_data(
                    data,
                    event_bounds_abs=bounds,
                ),
                provenance=self._dm_provenance(component_context),
                metric=metric,
            )
            component_results.append(
                DmComponentOptimizationResult(
                    component_id=f"component_{index}",
                    label=label,
                    event_window_ms=[
                        self.bin_to_ms(event_bounds_abs[0]),
                        self.bin_to_ms(event_bounds_abs[1]),
                    ],
                    trial_dms=np.asarray(component_optimization.trial_dms, dtype=float),
                    metric_values=np.asarray(component_optimization.snr, dtype=float),
                    metric=metric,
                    sampled_best_dm=float(component_optimization.sampled_best_dm),
                    sampled_best_value=float(component_optimization.sampled_best_sn),
                    best_dm=float(component_optimization.best_dm),
                    best_dm_uncertainty=component_optimization.best_dm_uncertainty,
                    best_value=float(component_optimization.best_sn),
                    fit_status=component_optimization.fit_status,
                )
            )

        self.dm_optimization = replace(
            optimization,
            component_results=component_results,
        )
        return self.dm_optimization

    def _apply_width_analysis_to_results(self) -> None:
        if self.results is None:
            return
        width_results = [] if self.width_analysis is None else list(self.width_analysis.results)
        accepted_width = None if self.width_analysis is None else self.width_analysis.accepted_width
        self.results = replace(
            self.results,
            width_results=width_results,
            accepted_width=accepted_width,
        )

    def compute_properties(self) -> BurstMeasurements:
        previous_results = self.results
        masked = self.get_masked_crop()
        event_rel_start, event_rel_end = self._event_bounds_in_crop(masked.shape[1])
        spec_lo, spec_hi = self._selected_channel_bounds()
        measurements = compute_burst_measurements(
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
            offpulse_regions_rel=self._offpulse_regions_in_crop(),
            noise_settings=self.noise_settings,
            width_results=[] if self.width_analysis is None else list(self.width_analysis.results),
            accepted_width=None if self.width_analysis is None else self.width_analysis.accepted_width,
        )
        if previous_results is not None and previous_results.diagnostics.scattering_fit is not None:
            measurements = replace(
                measurements,
                width_ms_model=previous_results.width_ms_model,
                tau_sc_ms=previous_results.tau_sc_ms,
                uncertainties=replace(
                    measurements.uncertainties,
                    width_ms_model=previous_results.uncertainties.width_ms_model,
                    tau_sc_ms=previous_results.uncertainties.tau_sc_ms,
                ),
                diagnostics=replace(
                    measurements.diagnostics,
                    scattering_fit=previous_results.diagnostics.scattering_fit,
                ),
            )
        self.results = measurements
        self._apply_width_analysis_to_results()
        return self.results

    def fit_scattering(self) -> BurstMeasurements:
        if self.results is None:
            self.compute_properties()
        assert self.results is not None

        masked, context = self._build_measurement_context_for_data()
        selected_band = np.asarray(masked[context.spec_lo : context.spec_hi + 1, :], dtype=float)
        selected_freqs = np.asarray(self.freqs[context.spec_lo : context.spec_hi + 1], dtype=float)
        peak_abs_bin = _primary_peak_bin(
            peak_bins_abs=self._current_peak_positions(),
            profile_sn=context.selected_profile_sn,
            crop_start_bin=self.crop_start,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
        )
        peak_rel_bin = None if peak_abs_bin is None else int(peak_abs_bin - self.crop_start)
        fit_result = fit_scattering_selected_band(
            selected_band=selected_band,
            freqs_mhz=selected_freqs,
            time_axis_ms=context.time_axis_ms,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
            offpulse_bins=context.offpulse_bins,
            tsamp_ms=self.tsamp_ms,
            peak_rel_bin=peak_rel_bin,
            width_guess_ms=self.results.width_ms_acf,
        )

        updated_flags = list(self.results.measurement_flags)
        if fit_result.status == "ok" and "fit" not in updated_flags:
            updated_flags.append("fit")

        self.results = replace(
            self.results,
            width_ms_model=fit_result.width_ms_model,
            tau_sc_ms=fit_result.tau_sc_ms,
            measurement_flags=updated_flags,
            uncertainties=replace(
                self.results.uncertainties,
                width_ms_model=fit_result.width_uncertainty_ms,
                tau_sc_ms=fit_result.tau_uncertainty_ms,
            ),
            diagnostics=replace(
                self.results.diagnostics,
                scattering_fit=fit_result.diagnostics,
            ),
        )
        self._apply_width_analysis_to_results()
        return self.results

    def run_spectral_analysis(self, segment_length_ms: float) -> SpectralAnalysisResult:
        _, context = self._build_measurement_context_for_data()
        event_series = np.asarray(
            context.selected_profile_baselined[context.event_rel_start:context.event_rel_end],
            dtype=float,
        )
        self.spectral_analysis = run_averaged_spectral_analysis(
            event_series=event_series,
            tsamp_ms=self.tsamp_ms,
            segment_length_ms=float(segment_length_ms),
            event_window_ms=self._current_event_window_ms(context),
            spectral_extent_mhz=self._selected_frequency_bounds_mhz(),
        )
        return self.spectral_analysis

    def _build_source_ref(self) -> SessionSourceRef:
        source_path = Path(self.burst_file).expanduser().resolve()
        try:
            stat = source_path.stat()
            file_size = int(stat.st_size)
            mtime_unix = float(stat.st_mtime)
        except FileNotFoundError:
            file_size = 0
            mtime_unix = 0.0
        freq_lo, freq_hi = self._frequency_range_mhz()
        return SessionSourceRef(
            source_path=source_path,
            source_name=self.metadata.source_name,
            file_size_bytes=file_size,
            mtime_unix=mtime_unix,
            shape=[self.total_channels, self.total_time_bins],
            tsamp=float(self.tsamp),
            freqres=float(self.freqres),
            start_mjd=float(self.start_mjd),
            npol=int(self.npol),
            freq_range_mhz=[float(freq_lo), float(freq_hi)],
        )

    def _validate_snapshot_source(self, source: SessionSourceRef) -> None:
        path = Path(source.source_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Session source file not found: {path}")
        current = self._build_source_ref()
        if current.file_size_bytes != int(source.file_size_bytes):
            raise ValueError("Session source file size does not match the saved snapshot.")
        if current.shape != list(source.shape):
            raise ValueError("Session source shape does not match the saved snapshot.")
        if not np.isclose(current.mtime_unix, float(source.mtime_unix)):
            raise ValueError("Session source modification time does not match the saved snapshot.")
        comparisons = [
            ("tsamp", current.tsamp, float(source.tsamp)),
            ("freqres", current.freqres, float(source.freqres)),
            ("start_mjd", current.start_mjd, float(source.start_mjd)),
            ("npol", float(current.npol), float(source.npol)),
            ("freq_range_mhz[0]", current.freq_range_mhz[0], float(source.freq_range_mhz[0])),
            ("freq_range_mhz[1]", current.freq_range_mhz[1], float(source.freq_range_mhz[1])),
        ]
        for label, current_value, saved_value in comparisons:
            if not np.isclose(current_value, saved_value):
                raise ValueError(f"Session source metadata mismatch for {label}.")

    def to_snapshot(self) -> AnalysisSessionSnapshot:
        return AnalysisSessionSnapshot(
            schema_version=SESSION_SNAPSHOT_SCHEMA_VERSION,
            source=self._build_source_ref(),
            dm=float(self.dm),
            preset_key=self.config.preset_key,
            sefd_jy=self.config.sefd_jy,
            read_start_sec=float(self.config.read_start_sec),
            initial_crop_sec=self.config.initial_crop_sec,
            auto_mask_profile=self.config.auto_mask_profile,
            distance_mpc=self.config.distance_mpc,
            redshift=self.config.redshift,
            time_factor=int(self.time_factor),
            freq_factor=int(self.freq_factor),
            crop_bins=[int(self.crop_start), int(self.crop_end)],
            event_bins=[int(self.event_start), int(self.event_end)],
            spectral_extent_channels=[int(self.spec_ex_lo), int(self.spec_ex_hi)],
            burst_regions=[
                BurstRegion(start_bin=int(start), end_bin=int(end))
                for start, end in self.burst_regions
            ],
            offpulse_regions=[
                OffPulseRegion(start_bin=int(start), end_bin=int(end))
                for start, end in self.offpulse_regions
            ],
            peak_bins=[int(value) for value in self.peak_positions],
            manual_peaks=bool(self.manual_peaks),
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
            last_auto_mask=self.last_auto_mask,
            noise_settings=self.noise_settings,
            width_settings=self.width_settings,
            notes=self.notes,
            results=self.results,
            width_analysis=self.width_analysis,
            dm_optimization=self.dm_optimization,
            spectral_analysis=self.spectral_analysis,
        )

    def snapshot_dict(self) -> dict[str, Any]:
        return self.to_snapshot().to_dict()

    def export_results(
        self,
        *,
        session_id: str,
        include: list[str] | tuple[str, ...] | None = None,
        plot_formats: list[str] | tuple[str, ...] | None = None,
    ) -> ExportManifest:
        snapshot = create_export_snapshot(
            self,
            session_id=session_id,
            include=include,
            plot_formats=plot_formats,
        )
        self.export_snapshots[snapshot.manifest.export_id] = snapshot
        self.export_order.append(snapshot.manifest.export_id)
        while len(self.export_order) > MAX_EXPORT_SNAPSHOTS:
            evicted_id = self.export_order.pop(0)
            self.export_snapshots.pop(evicted_id, None)
        return snapshot.manifest

    def get_export_manifest(self, export_id: str) -> ExportManifest:
        snapshot = self.export_snapshots.get(export_id)
        if snapshot is None:
            raise KeyError(export_id)
        return snapshot.manifest

    def get_export_artifact(self, export_id: str, artifact_name: str) -> tuple[ExportArtifact, bytes]:
        snapshot = self.export_snapshots.get(export_id)
        if snapshot is None:
            raise KeyError(export_id)

        artifact = next((item for item in snapshot.manifest.artifacts if item.name == artifact_name), None)
        if artifact is None or artifact.status != "ready":
            raise KeyError(artifact_name)

        content = snapshot.contents.get(artifact_name)
        if content is None:
            raise KeyError(artifact_name)
        return artifact, content
