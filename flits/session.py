from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
import warnings

import numpy as np

from flits.analysis.dm_optimization import DMMetricInput, available_dm_metrics, optimize_dm_trials
from flits.analysis.spectral.core import run_averaged_spectral_analysis
from flits.analysis.morphology import compute_width_analysis
from flits.analysis.temporal.core import run_temporal_structure_analysis, temporal_to_spectral_result
from flits.exports import MAX_EXPORT_SNAPSHOTS, StoredExportSnapshot, create_export_snapshot, preview_export
from flits.analysis.fitting import fit_scattering_selected_band
from flits.analysis.fitting.fitburst_adapter import FitburstRequestConfig
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
    ExportPreview,
    FilterbankMetadata,
    NoiseEstimateSettings,
    OffPulseRegion,
    SessionSourceRef,
    SpectralAnalysisResult,
    TemporalStructureResult,
    WidthAnalysisSettings,
    WidthAnalysisSummary,
    compatible_scalar_uncertainty,
)
from flits.settings import ObservationConfig, get_auto_mask_profile, get_preset
from flits.signal import block_reduce_mean, dedisperse
from flits.timing import ObservatoryLocation, TimingContext

try:
    import jess.channel_masks as _jess_channel_masks

    jess = SimpleNamespace(channel_masks=_jess_channel_masks)
    _jess_import_error: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency
    _jess_import_error = exc
    jess = SimpleNamespace(channel_masks=SimpleNamespace(channel_masker=None))


SESSION_SNAPSHOT_SCHEMA_VERSION = "1.4"
SOURCE_HASH_ALGORITHM = "sha256"
SOURCE_HASH_CHUNK_BYTES = 1024 * 1024
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


def _resolve_existing_or_candidate(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except OSError:
        return expanded


def _configured_data_dir() -> Path | None:
    configured = os.environ.get("FLITS_DATA_DIR")
    if not configured:
        return None
    return _resolve_existing_or_candidate(Path(configured))


def _data_dir_relative_path(path: Path) -> str | None:
    data_root = _configured_data_dir()
    if data_root is None:
        return None
    try:
        return path.relative_to(data_root).as_posix()
    except ValueError:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(SOURCE_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_expected_sha256(source: SessionSourceRef) -> str | None:
    expected = source.content_hash_sha256
    if not expected:
        return None
    algorithm = (source.content_hash_algorithm or SOURCE_HASH_ALGORITHM).lower()
    if algorithm != SOURCE_HASH_ALGORITHM:
        raise ValueError(f"Unsupported session source hash algorithm: {source.content_hash_algorithm}")
    return str(expected).lower()


def _format_candidate_paths(candidates: list[Path], *, limit: int = 12) -> str:
    shown = [str(candidate) for candidate in candidates[:limit]]
    if len(candidates) > limit:
        shown.append(f"... {len(candidates) - limit} more")
    return ", ".join(shown)


def _data_dir_hint() -> str:
    return (
        "Start FLITS with --data-dir or FLITS_DATA_DIR pointing at a directory "
        "that contains the source data."
    )


def _snapshot_source_candidates(source: SessionSourceRef) -> list[Path]:
    expanded = Path(source.source_path).expanduser()
    candidates: list[Path] = []

    def add(candidate: Path) -> None:
        resolved = _resolve_existing_or_candidate(candidate)
        if resolved not in candidates:
            candidates.append(resolved)

    add(expanded)
    if not expanded.is_absolute():
        add(Path.cwd() / expanded)

    data_root = _configured_data_dir()
    if data_root is not None:
        if source.data_dir_relative_path:
            add(data_root / source.data_dir_relative_path)

        if not expanded.is_absolute():
            add(data_root / expanded)

        parts = [part for part in expanded.parts if part not in {expanded.anchor, ""}]
        for index in range(len(parts)):
            suffix = Path(*parts[index:])
            add(data_root / suffix)

        file_name = source.file_name or expanded.name
        if file_name and data_root.exists():
            try:
                for candidate in data_root.rglob(file_name):
                    add(candidate)
            except OSError:
                pass

    return candidates


def _resolve_snapshot_source_path(source: SessionSourceRef) -> Path:
    candidates = _snapshot_source_candidates(source)
    existing = [candidate for candidate in candidates if candidate.exists()]
    if not existing:
        attempted = _format_candidate_paths(candidates)
        raise FileNotFoundError(
            f"Session source file not found: {source.source_path}. {_data_dir_hint()} Tried: {attempted}"
        )

    expected_size = int(source.file_size_bytes)
    expected_hash = _snapshot_expected_sha256(source)
    hash_cache: dict[Path, str] = {}

    def identity_matches(candidate: Path) -> bool:
        if expected_size > 0:
            try:
                if candidate.stat().st_size != expected_size:
                    return False
            except OSError:
                return False
        if expected_hash is None:
            return True
        try:
            if candidate not in hash_cache:
                hash_cache[candidate] = _sha256_file(candidate)
            candidate_hash = hash_cache[candidate]
        except OSError:
            return False
        return candidate_hash.lower() == expected_hash

    for candidate in existing:
        if identity_matches(candidate):
            return candidate

    attempted = _format_candidate_paths(existing)
    if expected_size > 0 or expected_hash is not None:
        expected = f"size {expected_size} bytes"
        if expected_hash is not None:
            expected += f" and SHA-256 {expected_hash}"
        raise ValueError(
            f"Session source file identity did not match the saved snapshot ({expected}). "
            f"{_data_dir_hint()} Tried: {attempted}"
        )
    return existing[0]


@dataclass(frozen=True)
class ReducedAnalysisGrid:
    masked: np.ndarray
    display: np.ndarray
    time_axis_ms: np.ndarray
    freqs_mhz: np.ndarray
    event_rel_start: int
    event_rel_end: int
    spec_lo: int
    spec_hi: int
    burst_regions_rel: list[tuple[int, int]]
    offpulse_regions_rel: list[tuple[int, int]]
    peak_bins: list[int]
    effective_tsamp_ms: float
    effective_freqres_mhz: float


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
    temporal_structure: TemporalStructureResult | None = None
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
        npol_override: int | None = None,
        read_start_sec: float | None = None,
        read_end_sec: float | None = None,
        auto_mask_profile: str | None = "auto",
        distance_mpc: float | None = None,
        redshift: float | None = None,
        sefd_fractional_uncertainty: float | None = None,
        distance_fractional_uncertainty: float | None = None,
        source_ra_deg: float | None = None,
        source_dec_deg: float | None = None,
        time_scale: str | None = None,
        observatory_longitude_deg: float | None = None,
        observatory_latitude_deg: float | None = None,
        observatory_height_m: float | None = None,
    ) -> "BurstSession":
        from flits.io import inspect_filterbank, load_filterbank_data

        inspection = inspect_filterbank(bfile)
        preset_key = telescope if telescope is not None else inspection.detected_preset_key
        config = ObservationConfig.from_preset(
            dm=dm,
            preset_key=preset_key,
            sefd_jy=sefd_jy,
            npol_override=npol_override,
            read_start_sec=read_start_sec,
            read_end_sec=read_end_sec,
            auto_mask_profile=auto_mask_profile,
            distance_mpc=distance_mpc,
            redshift=redshift,
            sefd_fractional_uncertainty=sefd_fractional_uncertainty,
            distance_fractional_uncertainty=distance_fractional_uncertainty,
            source_ra_deg=source_ra_deg,
            source_dec_deg=source_dec_deg,
            time_scale=time_scale,
            observatory_longitude_deg=observatory_longitude_deg,
            observatory_latitude_deg=observatory_latitude_deg,
            observatory_height_m=observatory_height_m,
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
        source_path = _resolve_snapshot_source_path(snapshot.source)
        session = session_loader(
            str(source_path),
            dm=float(snapshot.dm),
            telescope=snapshot.preset_key,
            sefd_jy=snapshot.sefd_jy,
            npol_override=snapshot.npol_override,
            read_start_sec=snapshot.read_start_sec,
            read_end_sec=snapshot.read_end_sec,
            auto_mask_profile=snapshot.auto_mask_profile,
            distance_mpc=snapshot.distance_mpc,
            redshift=snapshot.redshift,
            sefd_fractional_uncertainty=snapshot.sefd_fractional_uncertainty,
            distance_fractional_uncertainty=snapshot.distance_fractional_uncertainty,
            source_ra_deg=snapshot.source_ra_deg,
            source_dec_deg=snapshot.source_dec_deg,
            time_scale=snapshot.time_scale,
            observatory_longitude_deg=snapshot.observatory_longitude_deg,
            observatory_latitude_deg=snapshot.observatory_latitude_deg,
            observatory_height_m=snapshot.observatory_height_m,
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
        if snapshot.schema_version != "1.0":
            session.results = snapshot.results
            session.width_analysis = snapshot.width_analysis
            session.dm_optimization = snapshot.dm_optimization
            session.spectral_analysis = snapshot.spectral_analysis
            session.temporal_structure = snapshot.temporal_structure
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
    def header_npol(self) -> int:
        return self.metadata.header_npol

    @property
    def polarization_order(self) -> str | None:
        return self.metadata.polarization_order

    @property
    def freqs(self) -> np.ndarray:
        return self.metadata.freqs_mhz

    def _source_position(self) -> tuple[float | None, float | None, str | None]:
        if self.config.source_ra_deg is not None and self.config.source_dec_deg is not None:
            return float(self.config.source_ra_deg), float(self.config.source_dec_deg), "user_override"
        if self.metadata.source_ra_deg is not None and self.metadata.source_dec_deg is not None:
            return (
                float(self.metadata.source_ra_deg),
                float(self.metadata.source_dec_deg),
                self.metadata.source_position_basis or "reader_metadata",
            )
        return None, None, None

    def _observatory_location(self) -> ObservatoryLocation | None:
        if (
            self.config.observatory_longitude_deg is not None
            and self.config.observatory_latitude_deg is not None
            and self.config.observatory_height_m is not None
        ):
            return ObservatoryLocation(
                name="User override",
                longitude_deg=float(self.config.observatory_longitude_deg),
                latitude_deg=float(self.config.observatory_latitude_deg),
                height_m=float(self.config.observatory_height_m),
                basis="user_override",
            )
        preset = get_preset(self.config.preset_key)
        if preset.longitude_deg is None or preset.latitude_deg is None or preset.height_m is None:
            return None
        return ObservatoryLocation(
            name=preset.observatory_name,
            longitude_deg=preset.longitude_deg,
            latitude_deg=preset.latitude_deg,
            height_m=preset.height_m,
            basis=preset.observatory_location_basis,
        )

    def _timing_context(self) -> TimingContext:
        source_ra_deg, source_dec_deg, source_basis = self._source_position()
        return TimingContext(
            dm=float(self.dm),
            reference_frequency_mhz=self.metadata.dedispersion_reference_frequency_mhz,
            reference_frequency_basis=self.metadata.dedispersion_reference_basis,
            source_ra_deg=source_ra_deg,
            source_dec_deg=source_dec_deg,
            source_position_frame=self.metadata.source_position_frame,
            source_position_basis=source_basis,
            time_scale=str(self.config.time_scale or self.metadata.time_scale or "utc").lower(),
            time_reference_frame=self.metadata.time_reference_frame,
            barycentric_header_flag=self.metadata.barycentric_header_flag,
            pulsarcentric_header_flag=self.metadata.pulsarcentric_header_flag,
            observatory=self._observatory_location(),
        )

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

    def clear_temporal_structure(self) -> None:
        self.temporal_structure = None

    def invalidate_analysis_state(self) -> None:
        self.invalidate_results()
        self.clear_width_analysis()
        self.clear_dm_optimization()
        self.clear_spectral_analysis()
        self.clear_temporal_structure()

    def bin_to_ms(self, time_bin: int | float) -> float:
        return float(time_bin) * self.tsamp_ms + float(self.config.read_start_sec) * 1000.0

    def ms_to_bin(self, time_ms: float) -> int:
        return int(round((float(time_ms) - float(self.config.read_start_sec) * 1000.0) / self.tsamp_ms))

    def _bins_to_ms_array(self, time_bins: np.ndarray) -> np.ndarray:
        return np.asarray(time_bins, dtype=float) * self.tsamp_ms + float(self.config.read_start_sec) * 1000.0

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

    def _reduce_interval(
        self,
        start: int,
        end: int,
        *,
        base: int,
        factor: int,
        max_bins: int,
        require_nonempty: bool = False,
    ) -> tuple[int, int] | None:
        if max_bins <= 0:
            return (0, 0) if require_nonempty else None

        lo = int(np.floor((int(start) - int(base)) / int(factor)))
        hi = int(np.ceil((int(end) - int(base)) / int(factor)))

        if require_nonempty:
            lo = max(0, min(lo, max_bins - 1))
            hi = max(lo + 1, min(hi, max_bins))
            return lo, hi

        lo = max(0, min(lo, max_bins))
        hi = max(lo, min(hi, max_bins))
        if hi <= lo:
            return None
        return lo, hi

    def _reduce_peak_bin(
        self,
        peak_bin_abs: int,
        *,
        base: int,
        factor: int,
        max_bins: int,
    ) -> int | None:
        if max_bins <= 0:
            return None
        if int(peak_bin_abs) < int(base):
            return None
        reduced = int(np.floor((int(peak_bin_abs) - int(base)) / int(factor)))
        return max(0, min(reduced, max_bins - 1))

    def _reduced_frequency_axis(self, num_channels: int) -> np.ndarray:
        if num_channels <= 0:
            return np.array([], dtype=float)
        freq_axis = np.asarray(self.freqs[: num_channels * self.freq_factor], dtype=float)
        if self.freq_factor > 1 and freq_axis.size:
            freq_axis = np.nanmean(freq_axis.reshape(num_channels, self.freq_factor), axis=1)
        return np.asarray(freq_axis, dtype=float)

    def _reduced_analysis_grid(
        self,
        data: np.ndarray | None = None,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> ReducedAnalysisGrid:
        masked = self.get_masked_crop(data)
        display = self.get_display_crop(data)
        reduced_masked = block_reduce_mean(masked, tfac=self.time_factor, ffac=self.freq_factor)
        reduced_display = block_reduce_mean(display, tfac=self.time_factor, ffac=self.freq_factor)

        reduced_time_bins = int(reduced_masked.shape[1])
        reduced_freq_bins = int(reduced_masked.shape[0])
        time_axis_ms = (
            self._bins_to_ms_array(self.crop_start + np.arange(reduced_time_bins, dtype=float) * self.time_factor)
            if reduced_time_bins
            else np.array([], dtype=float)
        )
        freqs_mhz = self._reduced_frequency_axis(reduced_freq_bins)

        event_start_abs, event_end_abs = (
            (self.event_start, self.event_end) if event_bounds_abs is None else sorted((int(event_bounds_abs[0]), int(event_bounds_abs[1])))
        )
        event_bounds = self._reduce_interval(
            event_start_abs,
            event_end_abs,
            base=self.crop_start,
            factor=self.time_factor,
            max_bins=reduced_time_bins,
            require_nonempty=True,
        )
        assert event_bounds is not None
        event_rel_start, event_rel_end = event_bounds

        spec_lo_abs, spec_hi_abs = self._selected_channel_bounds()
        spec_bounds = self._reduce_interval(
            spec_lo_abs,
            spec_hi_abs + 1,
            base=0,
            factor=self.freq_factor,
            max_bins=reduced_freq_bins,
            require_nonempty=True,
        )
        assert spec_bounds is not None
        spec_lo, spec_hi_exclusive = spec_bounds
        spec_hi = max(spec_lo, spec_hi_exclusive - 1)

        burst_regions_rel: list[tuple[int, int]] = []
        for start_abs, end_abs in self.burst_regions:
            mapped = self._reduce_interval(
                start_abs,
                end_abs,
                base=self.crop_start,
                factor=self.time_factor,
                max_bins=reduced_time_bins,
            )
            if mapped is not None:
                burst_regions_rel.append(mapped)

        offpulse_regions_rel: list[tuple[int, int]] = []
        for start_abs, end_abs in self.offpulse_regions:
            mapped = self._reduce_interval(
                start_abs,
                end_abs,
                base=self.crop_start,
                factor=self.time_factor,
                max_bins=reduced_time_bins,
            )
            if mapped is not None:
                offpulse_regions_rel.append(mapped)

        if self.manual_peaks and self.peak_positions:
            peak_bins = sorted(
                {
                    reduced_peak
                    for peak in self.peak_positions
                    if self.crop_start <= int(peak) < self.crop_end
                    for reduced_peak in [self._reduce_peak_bin(
                        int(peak),
                        base=self.crop_start,
                        factor=self.time_factor,
                        max_bins=reduced_time_bins,
                    )]
                    if reduced_peak is not None
                }
            )
        else:
            peak_bins = []
            if reduced_display.size:
                profile = np.nansum(reduced_display, axis=0)
                event_profile = np.asarray(profile[event_rel_start:event_rel_end], dtype=float)
                if event_profile.size and np.isfinite(event_profile).any():
                    peak_bins = [int(event_rel_start + int(np.nanargmax(event_profile)))]
                elif np.isfinite(profile).any():
                    peak_bins = [int(np.nanargmax(profile))]

        return ReducedAnalysisGrid(
            masked=np.asarray(reduced_masked, dtype=float),
            display=np.asarray(reduced_display, dtype=float),
            time_axis_ms=np.asarray(time_axis_ms, dtype=float),
            freqs_mhz=np.asarray(freqs_mhz, dtype=float),
            event_rel_start=int(event_rel_start),
            event_rel_end=int(event_rel_end),
            spec_lo=int(spec_lo),
            spec_hi=int(spec_hi),
            burst_regions_rel=burst_regions_rel,
            offpulse_regions_rel=offpulse_regions_rel,
            peak_bins=peak_bins,
            effective_tsamp_ms=float(self.tsamp_ms * self.time_factor),
            effective_freqres_mhz=float(abs(self.freqres) * self.freq_factor),
        )

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
    ) -> tuple[ReducedAnalysisGrid, MeasurementContext]:
        grid = self._reduced_analysis_grid(data, event_bounds_abs=event_bounds_abs)
        context = self._measurement_context_from_grid(grid)
        return grid, context

    def _measurement_context_from_grid(self, grid: ReducedAnalysisGrid) -> MeasurementContext:
        context = build_measurement_context(
            masked=grid.masked,
            time_axis_ms=grid.time_axis_ms,
            freqs_mhz=grid.freqs_mhz,
            event_rel_start=grid.event_rel_start,
            event_rel_end=grid.event_rel_end,
            spec_lo=grid.spec_lo,
            spec_hi=grid.spec_hi,
            freqres_mhz=grid.effective_freqres_mhz,
            offpulse_regions=grid.offpulse_regions_rel,
            noise_settings=self.noise_settings,
        )
        return context

    def _measurement_context_for_data(
        self,
        data: np.ndarray,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> MeasurementContext:
        _, context = self._build_measurement_context_for_data(data, event_bounds_abs=event_bounds_abs)
        return context

    def _dm_metric_input_for_data(
        self,
        data: np.ndarray,
        *,
        event_bounds_abs: tuple[int, int] | None = None,
    ) -> DMMetricInput:
        grid, context = self._build_measurement_context_for_data(data, event_bounds_abs=event_bounds_abs)
        selected_waterfall = np.asarray(grid.masked[context.spec_lo : context.spec_hi + 1, :], dtype=float)
        event_waterfall = np.asarray(
            selected_waterfall[:, context.event_rel_start : context.event_rel_end],
            dtype=float,
        )
        return DMMetricInput(
            context=context,
            waterfall=np.asarray(grid.masked, dtype=float),
            selected_waterfall=selected_waterfall,
            event_waterfall=event_waterfall,
            offpulse_bins=np.asarray(context.offpulse_bins, dtype=int),
            freqs_mhz=np.asarray(context.spectral_axis_mhz, dtype=float),
            tsamp_sec=float(grid.effective_tsamp_ms / 1000.0),
        )

    def _dm_metric_input_for_reduced_grid(
        self,
        reduced_data: np.ndarray,
        *,
        context: MeasurementContext,
        tsamp_sec: float,
    ) -> DMMetricInput:
        reduced = np.asarray(reduced_data, dtype=float)
        selected_waterfall = np.asarray(reduced[context.spec_lo : context.spec_hi + 1, :], dtype=float)
        event_waterfall = np.asarray(
            selected_waterfall[:, context.event_rel_start : context.event_rel_end],
            dtype=float,
        )
        return DMMetricInput(
            context=context,
            waterfall=reduced,
            selected_waterfall=selected_waterfall,
            event_waterfall=event_waterfall,
            offpulse_bins=np.asarray(context.offpulse_bins, dtype=int),
            freqs_mhz=np.asarray(context.spectral_axis_mhz, dtype=float),
            tsamp_sec=float(tsamp_sec),
        )

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
        grid, context = self._build_measurement_context_for_data(data, event_bounds_abs=event_bounds_abs)
        diagnostics = compute_subband_arrival_residuals(
            masked=grid.masked,
            time_axis_ms=context.time_axis_ms,
            freqs_mhz=grid.freqs_mhz,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
            spec_lo=context.spec_lo,
            spec_hi=context.spec_hi,
            freqres_mhz=grid.effective_freqres_mhz,
            offpulse_regions=grid.offpulse_regions_rel,
            noise_settings=self.noise_settings,
        )
        return (
            diagnostics.center_freqs_mhz,
            diagnostics.arrival_times_ms,
            diagnostics.residuals_ms,
            diagnostics.status,
        )

    def _fitburst_guess_payload(
        self,
        grid: ReducedAnalysisGrid,
        context: MeasurementContext,
    ) -> dict[str, Any]:
        time_axis_ms = np.asarray(context.time_axis_ms, dtype=float)
        selected_freqs = np.asarray(grid.freqs_mhz[context.spec_lo : context.spec_hi + 1], dtype=float)
        selected_band = np.asarray(grid.masked[context.spec_lo : context.spec_hi + 1, :], dtype=float)
        if (
            time_axis_ms.size == 0
            or selected_freqs.size == 0
            or context.event_rel_end <= context.event_rel_start
        ):
            return {
                "status": "unavailable",
                "message": "No selected event data are available for fitburst initial guesses.",
                "source": "unavailable",
                "component_count": 0,
                "component_guesses": [],
                "initial_parameters": {},
            }

        event_rel_start = int(context.event_rel_start)
        event_rel_end = int(context.event_rel_end)
        event_window_ms = self._current_event_window_ms(context, tsamp_ms=grid.effective_tsamp_ms)
        width_guess_ms = (
            float(self.results.width_ms_acf)
            if self.results is not None
            and self.results.width_ms_acf is not None
            and np.isfinite(self.results.width_ms_acf)
            and self.results.width_ms_acf > 0
            else None
        )

        def clipped_window(start_bin: int, end_bin: int) -> tuple[int, int] | None:
            start = max(event_rel_start, int(start_bin))
            end = min(event_rel_end, int(end_bin))
            if end - start < 2:
                return None
            return start, end

        def local_peak_bin(start_bin: int, end_bin: int) -> int:
            profile = np.asarray(context.selected_profile_sn[start_bin:end_bin], dtype=float)
            if profile.size and np.isfinite(profile).any():
                return int(start_bin + int(np.nanargmax(profile)))
            return int(start_bin + max(0, (end_bin - start_bin) // 2))

        def manual_peak_bin(start_bin: int, end_bin: int) -> int | None:
            if not self.manual_peaks:
                return None
            candidates = [int(peak) for peak in grid.peak_bins if start_bin <= int(peak) < end_bin]
            if not candidates:
                return None
            profile = np.asarray(context.selected_profile_sn, dtype=float)
            return max(
                candidates,
                key=lambda peak: float(profile[peak])
                if 0 <= int(peak) < profile.size and np.isfinite(profile[peak])
                else float("-inf"),
            )

        def component_guess(
            *,
            label: str,
            source: str,
            source_label: str,
            start_bin: int,
            end_bin: int,
            arrival_bin: int | None = None,
            prefer_global_width: bool = False,
        ) -> dict[str, Any] | None:
            window = clipped_window(start_bin, end_bin)
            if window is None:
                return None
            start, end = window
            peak = arrival_bin if arrival_bin is not None else manual_peak_bin(start, end)
            if peak is None or not (start <= int(peak) < end):
                peak = local_peak_bin(start, end)
            peak = max(start, min(int(peak), end - 1))

            start_ms = float(time_axis_ms[start])
            end_ms = float(time_axis_ms[end - 1] + grid.effective_tsamp_ms)
            duration_ms = max(float(grid.effective_tsamp_ms), end_ms - start_ms)
            if prefer_global_width and width_guess_ms is not None:
                width_ms = max(float(grid.effective_tsamp_ms), float(width_guess_ms))
            else:
                width_ms = max(float(grid.effective_tsamp_ms), duration_ms / 6.0)
            tau_ms = max(float(grid.effective_tsamp_ms), width_ms / 4.0)
            log_amplitude = self._fitburst_log_amplitude(
                selected_band=selected_band,
                offpulse_bins=context.offpulse_bins,
                start_bin=start,
                end_bin=end,
                fallback_profile=context.selected_profile_sn,
            )
            return {
                "label": label,
                "source": source,
                "source_label": source_label,
                "arrival_time_ms": float(time_axis_ms[peak]),
                "width_ms": float(width_ms),
                "tau_ms": float(tau_ms),
                "log_amplitude": float(log_amplitude),
                "component_window_ms": [start_ms, end_ms],
            }

        guesses: list[dict[str, Any]] = []
        source = "automatic"
        region_windows = sorted(grid.burst_regions_rel, key=lambda item: (item[0], item[1]))
        for index, (start, end) in enumerate(region_windows, start=1):
            guess = component_guess(
                label=f"Component {index}",
                source="component_regions",
                source_label=f"Region {index}",
                start_bin=start,
                end_bin=end,
            )
            if guess is not None:
                guesses.append(guess)
        if guesses:
            source = "component_regions"
        elif self.manual_peaks and grid.peak_bins:
            peaks = sorted(
                int(peak)
                for peak in grid.peak_bins
                if event_rel_start <= int(peak) < event_rel_end
            )
            if peaks:
                boundaries = [event_rel_start]
                boundaries.extend(int(round((left + right) / 2.0)) for left, right in zip(peaks[:-1], peaks[1:]))
                boundaries.append(event_rel_end)
                for index, peak in enumerate(peaks, start=1):
                    guess = component_guess(
                        label=f"Component {index}",
                        source="manual_peaks",
                        source_label=f"Peak {index}",
                        start_bin=boundaries[index - 1],
                        end_bin=boundaries[index],
                        arrival_bin=peak,
                    )
                    if guess is not None:
                        guesses.append(guess)
                if guesses:
                    source = "manual_peaks"

        if not guesses:
            peak = _primary_peak_bin(
                peak_bins_abs=grid.peak_bins,
                profile_sn=context.selected_profile_sn,
                crop_start_bin=0,
                event_rel_start=event_rel_start,
                event_rel_end=event_rel_end,
            )
            guesses = [
                component_guess(
                    label="Component 1",
                    source="automatic",
                    source_label="Auto",
                    start_bin=event_rel_start,
                    end_bin=event_rel_end,
                    arrival_bin=peak,
                    prefer_global_width=True,
                )
            ]
            guesses = [guess for guess in guesses if guess is not None]

        return {
            "status": "ok" if guesses else "unavailable",
            "message": self._fitburst_guess_message(source, len(guesses)),
            "source": source,
            "component_count": len(guesses),
            "component_guesses": guesses,
            "initial_parameters": self._fitburst_initial_parameters_from_component_guesses(
                guesses,
                time_axis_ms=time_axis_ms,
                freqs_mhz=selected_freqs,
            )
            if guesses
            else {},
            "event_window_ms": [float(event_window_ms[0]), float(event_window_ms[1])],
        }

    @staticmethod
    def _fitburst_guess_message(source: str, component_count: int) -> str:
        if source == "component_regions":
            return f"{component_count} component guess{'es' if component_count != 1 else ''} from component regions."
        if source == "manual_peaks":
            return f"{component_count} component guess{'es' if component_count != 1 else ''} from manual peaks."
        return "Automatic single-component guess from the strongest event peak."

    @staticmethod
    def _fitburst_log_amplitude(
        *,
        selected_band: np.ndarray,
        offpulse_bins: np.ndarray,
        start_bin: int,
        end_bin: int,
        fallback_profile: np.ndarray,
    ) -> float:
        peak_value = float("-inf")
        offpulse_bins = np.asarray(offpulse_bins, dtype=int)
        for row in np.asarray(selected_band, dtype=float):
            finite_row = row[np.isfinite(row)]
            if finite_row.size == 0:
                continue
            reference = row[offpulse_bins] if offpulse_bins.size else row
            finite_reference = reference[np.isfinite(reference)]
            if finite_reference.size == 0:
                finite_reference = finite_row
            baseline = float(np.nanmean(finite_reference))
            sigma = float(np.nanstd(finite_reference))
            if not np.isfinite(sigma) or sigma <= 0:
                continue
            window = row[start_bin:end_bin]
            finite_window = window[np.isfinite(window)]
            if finite_window.size == 0:
                continue
            peak_value = max(peak_value, float((np.nanmax(finite_window) - baseline) / sigma))

        if not np.isfinite(peak_value):
            profile_window = np.asarray(fallback_profile[start_bin:end_bin], dtype=float)
            peak_value = float(np.nanmax(profile_window)) if profile_window.size and np.isfinite(profile_window).any() else 1.0
        return float(np.log10(max(peak_value, 1e-2)))

    def _fitburst_initial_parameters_from_component_guesses(
        self,
        component_guesses: list[dict[str, Any]],
        *,
        time_axis_ms: np.ndarray,
        freqs_mhz: np.ndarray,
        validate: bool = False,
        event_window_ms: tuple[float, float] | None = None,
    ) -> dict[str, list[float]]:
        time_axis_ms = np.asarray(time_axis_ms, dtype=float)
        freqs_mhz = np.asarray(freqs_mhz, dtype=float)
        if time_axis_ms.size == 0:
            if validate:
                raise ValueError("Cannot build fitburst guesses without a time axis.")
            return {}
        if freqs_mhz.size == 0:
            if validate:
                raise ValueError("Cannot build fitburst guesses without selected frequencies.")
            return {}
        ref_freq = float(np.min(freqs_mhz))
        base_time_ms = float(time_axis_ms[0])

        rows: list[tuple[float, float, float, float]] = []
        for index, guess in enumerate(component_guesses, start=1):
            try:
                arrival_ms = float(guess.get("arrival_time_ms"))
                width_ms = float(guess.get("width_ms"))
                tau_ms = float(guess.get("tau_ms"))
                log_amplitude = float(guess.get("log_amplitude"))
            except (TypeError, ValueError, AttributeError) as exc:
                if validate:
                    raise ValueError(f"Component {index} has a non-numeric initial guess.") from exc
                continue

            values = (arrival_ms, width_ms, tau_ms, log_amplitude)
            if not all(np.isfinite(value) for value in values):
                if validate:
                    raise ValueError(f"Component {index} has a non-finite initial guess.")
                continue
            if width_ms <= 0 or tau_ms <= 0:
                if validate:
                    raise ValueError(f"Component {index} width and tau guesses must be positive.")
                continue
            if event_window_ms is not None and not (event_window_ms[0] <= arrival_ms <= event_window_ms[1]):
                if validate:
                    raise ValueError(f"Component {index} arrival guess is outside the selected event window.")
                continue
            rows.append((arrival_ms, width_ms, tau_ms, log_amplitude))

        if validate and not rows:
            raise ValueError("At least one component initial guess is required.")

        return {
            "amplitude": [float(row[3]) for row in rows],
            "arrival_time": [float((row[0] - base_time_ms) / 1e3) for row in rows],
            "burst_width": [float(row[1] / 1e3) for row in rows],
            "dm": [0.0] * len(rows),
            "dm_index": [-2.0] * len(rows),
            "ref_freq": [ref_freq] * len(rows),
            "scattering_timescale": [float(row[2] / 1e3) for row in rows],
            "scattering_index": [-4.0] * len(rows),
            "spectral_index": [0.0] * len(rows),
            "spectral_running": [0.0] * len(rows),
        }

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
        grid = self._reduced_analysis_grid()
        context = self._measurement_context_from_grid(grid)
        freq_lo_mhz, freq_hi_mhz = self._frequency_range_mhz()
        spec_lo_mhz, spec_hi_mhz = self._selected_frequency_bounds_mhz()
        time_profile = np.nansum(grid.display, axis=0) if grid.display.size else np.array([], dtype=float)
        spectrum = np.nansum(grid.display, axis=1) if grid.display.size else np.array([], dtype=float)
        peak_positions_ms = [
            float(grid.time_axis_ms[peak])
            for peak in grid.peak_bins
            if 0 <= int(peak) < grid.time_axis_ms.size
        ]
        zmin, zmax = robust_color_limits(grid.display)
        source_ra_deg, source_dec_deg, source_position_basis = self._source_position()
        observatory = self._observatory_location()

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
                "npol_override": self.config.npol_override,
                "auto_mask_profile": self.config.auto_mask_profile,
                "auto_mask_profile_label": get_auto_mask_profile(self.config.auto_mask_profile).label,
                "tsamp_us": self.tsamp * 1e6,
                "freqres_mhz": self.freqres,
                "npol": self.npol,
                "header_npol": self.header_npol,
                "polarization_order": self.polarization_order,
                "distance_mpc": self.config.distance_mpc,
                "redshift": self.config.redshift,
                "sefd_fractional_uncertainty": self.config.sefd_fractional_uncertainty,
                "distance_fractional_uncertainty": self.config.distance_fractional_uncertainty,
                "source_ra_deg": source_ra_deg,
                "source_dec_deg": source_dec_deg,
                "source_position_frame": self.metadata.source_position_frame,
                "source_position_basis": source_position_basis,
                "time_scale": str(self.config.time_scale or self.metadata.time_scale or "utc").lower(),
                "time_reference_frame": self.metadata.time_reference_frame,
                "barycentric_header_flag": self.metadata.barycentric_header_flag,
                "pulsarcentric_header_flag": self.metadata.pulsarcentric_header_flag,
                "dedispersion_reference_frequency_mhz": self.metadata.dedispersion_reference_frequency_mhz,
                "dedispersion_reference_basis": self.metadata.dedispersion_reference_basis,
                "observatory_name": None if observatory is None else observatory.name,
                "observatory_longitude_deg": None if observatory is None else observatory.longitude_deg,
                "observatory_latitude_deg": None if observatory is None else observatory.latitude_deg,
                "observatory_height_m": None if observatory is None else observatory.height_m,
                "observatory_location_basis": None if observatory is None else observatory.basis,
                "shape": [self.total_channels, self.total_time_bins],
                "view_shape": [int(grid.display.shape[0]), int(grid.display.shape[1])],
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
                "peak_ms": peak_positions_ms,
                "manual_peaks": self.manual_peaks,
                "spectral_extent_mhz": [spec_lo_mhz, spec_hi_mhz],
                "masked_channels": np.flatnonzero(self.channel_mask).astype(int).tolist(),
                "last_auto_mask": self.last_auto_mask.to_dict() if self.last_auto_mask is not None else None,
                "notes": self.notes,
            },
            "plot": {
                "heatmap": {
                    "x_ms": _jsonable_array(grid.time_axis_ms, digits=4),
                    "y_mhz": _jsonable_array(grid.freqs_mhz, digits=4),
                    "z": _jsonable_array(grid.display, digits=3),
                    "zmin": zmin,
                    "zmax": zmax,
                },
                "time_profile": {
                    "x_ms": _jsonable_array(grid.time_axis_ms, digits=4),
                    "y": _jsonable_array(time_profile, digits=4),
                },
                "spectrum": {
                    "x": _jsonable_array(spectrum, digits=4),
                    "y_mhz": _jsonable_array(grid.freqs_mhz, digits=4),
                },
            },
            "fitburst_guess": self._fitburst_guess_payload(grid, context),
            "results": self.results.to_dict() if self.results is not None else None,
            "width_analysis": self.width_analysis.to_dict() if self.width_analysis is not None else None,
            "dm_optimization": self.dm_optimization.to_dict() if self.dm_optimization is not None else None,
            "spectral_analysis": (
                None if self.spectral_analysis is None else self.spectral_analysis.to_dict()
            ),
            "temporal_structure": (
                None if self.temporal_structure is None else self.temporal_structure.to_dict()
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
        next_factor = max(1, min(int(factor), self.crop_end - self.crop_start))
        if next_factor != self.time_factor:
            self.time_factor = next_factor
            self.invalidate_analysis_state()

    def set_freq_factor(self, factor: int) -> None:
        next_factor = max(1, min(int(factor), self.total_channels))
        if next_factor != self.freq_factor:
            self.freq_factor = next_factor
            self.invalidate_analysis_state()

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

    def set_timing_metadata(
        self,
        *,
        source_ra_deg: float | None = None,
        source_dec_deg: float | None = None,
        time_scale: str | None = None,
        observatory_longitude_deg: float | None = None,
        observatory_latitude_deg: float | None = None,
        observatory_height_m: float | None = None,
    ) -> None:
        self.config = replace(
            self.config,
            source_ra_deg=None if source_ra_deg is None else float(source_ra_deg),
            source_dec_deg=None if source_dec_deg is None else float(source_dec_deg),
            time_scale=None if time_scale is None else str(time_scale).lower(),
            observatory_longitude_deg=(
                None if observatory_longitude_deg is None else float(observatory_longitude_deg)
            ),
            observatory_latitude_deg=(
                None if observatory_latitude_deg is None else float(observatory_latitude_deg)
            ),
            observatory_height_m=None if observatory_height_m is None else float(observatory_height_m),
        )
        self.invalidate_results()

    def _current_event_window_ms(
        self,
        context: MeasurementContext | None = None,
        *,
        tsamp_ms: float | None = None,
    ) -> tuple[float, float]:
        spacing_ms = float(self.tsamp_ms if tsamp_ms is None else tsamp_ms)
        if context is not None and context.time_axis_ms.size and context.event_rel_end > context.event_rel_start:
            return (
                float(context.time_axis_ms[context.event_rel_start]),
                float(context.time_axis_ms[context.event_rel_end - 1] + spacing_ms),
            )
        return (self.bin_to_ms(self.event_start), self.bin_to_ms(self.event_end))

    def auto_mask_jess(self, profile: str | None = None) -> None:
        if jess.channel_masks.channel_masker is None:
            if _jess_import_error is not None:
                message = f"{type(_jess_import_error).__name__}: {_jess_import_error}"
                raise RuntimeError(f"Jess is not available in the active environment: {message}") from _jess_import_error
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
        self.metadata = replace(
            self.metadata,
            dedispersion_reference_frequency_mhz=float(np.max(self.freqs)) if abs(new_dm) > 0.0 else None,
            dedispersion_reference_basis=(
                "flits_integer_bin_dedispersion_max_frequency"
                if abs(new_dm) > 0.0
                else "dm_zero_assumed_infinite_frequency"
            ),
        )
        self.invalidate_analysis_state()

    def _dm_provenance(
        self,
        context: MeasurementContext,
        *,
        tsamp_ms: float,
        freqres_mhz: float,
    ) -> DmOptimizationProvenance:
        return DmOptimizationProvenance(
            event_window_ms=[
                float(context.time_axis_ms[context.event_rel_start]) if context.time_axis_ms.size else 0.0,
                float(context.time_axis_ms[context.event_rel_end - 1] + tsamp_ms)
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
                tsamp_ms=tsamp_ms,
            ),
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
            effective_bandwidth_mhz=float(context.effective_bandwidth_mhz),
            tsamp_ms=float(tsamp_ms),
            freqres_mhz=float(abs(freqres_mhz)),
            algorithm_name="dm_trial_sweep",
            warning_flags=list(context.noise_summary.warning_flags),
        )

    def compute_widths(self) -> WidthAnalysisSummary:
        grid, context = self._build_measurement_context_for_data()
        existing_method = None
        if self.width_analysis is not None and self.width_analysis.accepted_width is not None:
            existing_method = self.width_analysis.accepted_width.method
        self.width_analysis = compute_width_analysis(
            selected_profile=context.selected_profile_raw,
            time_axis_ms=context.time_axis_ms,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
            tsamp_ms=grid.effective_tsamp_ms,
            noise_summary=context.noise_summary,
            offpulse_bins=context.offpulse_bins,
            settings=self.width_settings,
            event_window_ms=[
                float(context.time_axis_ms[context.event_rel_start]) if context.time_axis_ms.size else 0.0,
                float(context.time_axis_ms[context.event_rel_end - 1] + grid.effective_tsamp_ms)
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
                tsamp_ms=grid.effective_tsamp_ms,
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
        grid, context = self._build_measurement_context_for_data(self.data)
        reduced_metric_data = np.asarray(grid.masked, dtype=float)
        reduced_metric_freqs = np.asarray(grid.freqs_mhz, dtype=float)
        reduced_metric_tsamp_sec = float(grid.effective_tsamp_ms / 1000.0)
        optimization = optimize_dm_trials(
            data=self.data,
            current_dm=float(self.dm),
            freqs_mhz=self.freqs,
            tsamp_sec=self.tsamp,
            score_data=reduced_metric_data if metric == "dm_phase" else None,
            score_current_dm=float(self.dm) if metric == "dm_phase" else None,
            score_freqs_mhz=reduced_metric_freqs if metric == "dm_phase" else None,
            score_tsamp_sec=reduced_metric_tsamp_sec if metric == "dm_phase" else None,
            center_dm=float(center_dm),
            half_range=float(half_range),
            step=float(step),
            metric_input_builder=(
                (lambda data: self._dm_metric_input_for_reduced_grid(data, context=context, tsamp_sec=reduced_metric_tsamp_sec))
                if metric == "dm_phase"
                else (lambda data: self._dm_metric_input_for_data(data))
            ),
            residuals=lambda data: self._subband_residuals_for_data(data),
            provenance=self._dm_provenance(
                context,
                tsamp_ms=grid.effective_tsamp_ms,
                freqres_mhz=grid.effective_freqres_mhz,
            ),
            metric=metric,
        )
        component_results: list[DmComponentOptimizationResult] = []
        for index, (label, event_bounds_abs) in enumerate(self._dm_component_windows(), start=1):
            component_grid, component_context = self._build_measurement_context_for_data(
                self.data,
                event_bounds_abs=event_bounds_abs,
            )
            component_reduced_metric_data = np.asarray(component_grid.masked, dtype=float)
            component_reduced_metric_freqs = np.asarray(component_grid.freqs_mhz, dtype=float)
            component_reduced_metric_tsamp_sec = float(component_grid.effective_tsamp_ms / 1000.0)
            component_optimization = optimize_dm_trials(
                data=self.data,
                current_dm=float(self.dm),
                freqs_mhz=self.freqs,
                tsamp_sec=self.tsamp,
                score_data=component_reduced_metric_data if metric == "dm_phase" else None,
                score_current_dm=float(self.dm) if metric == "dm_phase" else None,
                score_freqs_mhz=component_reduced_metric_freqs if metric == "dm_phase" else None,
                score_tsamp_sec=component_reduced_metric_tsamp_sec if metric == "dm_phase" else None,
                center_dm=float(center_dm),
                half_range=float(half_range),
                step=float(step),
                metric_input_builder=(
                    (
                        lambda data, ctx=component_context, ts=component_reduced_metric_tsamp_sec: self._dm_metric_input_for_reduced_grid(
                            data,
                            context=ctx,
                            tsamp_sec=ts,
                        )
                    )
                    if metric == "dm_phase"
                    else (
                        lambda data, bounds=event_bounds_abs: self._dm_metric_input_for_data(
                            data,
                            event_bounds_abs=bounds,
                        )
                    )
                ),
                residuals=lambda data, bounds=event_bounds_abs: self._subband_residuals_for_data(
                    data,
                    event_bounds_abs=bounds,
                ),
                provenance=self._dm_provenance(
                    component_context,
                    tsamp_ms=component_grid.effective_tsamp_ms,
                    freqres_mhz=component_grid.effective_freqres_mhz,
                ),
                metric=metric,
            )
            component_results.append(
                DmComponentOptimizationResult(
                    component_id=f"component_{index}",
                    label=label,
                    event_window_ms=list(
                        self._current_event_window_ms(
                            component_context,
                            tsamp_ms=component_grid.effective_tsamp_ms,
                        )
                    ),
                    trial_dms=np.asarray(component_optimization.trial_dms, dtype=float),
                    metric_values=np.asarray(component_optimization.snr, dtype=float),
                    metric=metric,
                    sampled_best_dm=float(component_optimization.sampled_best_dm),
                    sampled_best_value=float(component_optimization.sampled_best_sn),
                    best_dm=float(component_optimization.best_dm),
                    best_dm_uncertainty=component_optimization.best_dm_uncertainty,
                    best_value=float(component_optimization.best_sn),
                    fit_status=component_optimization.fit_status,
                    uncertainty_details=dict(component_optimization.uncertainty_details),
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
        grid = self._reduced_analysis_grid()
        measurements = compute_burst_measurements(
            burst_name=Path(self.burst_file).stem,
            dm=self.dm,
            start_mjd=self.start_mjd,
            read_start_sec=0.0,
            crop_start_bin=0,
            tsamp_ms=grid.effective_tsamp_ms,
            freqres_mhz=grid.effective_freqres_mhz,
            freqs_mhz=grid.freqs_mhz,
            masked=grid.masked,
            event_rel_start=grid.event_rel_start,
            event_rel_end=grid.event_rel_end,
            spec_lo=grid.spec_lo,
            spec_hi=grid.spec_hi,
            peak_bins_abs=grid.peak_bins,
            burst_regions_abs=tuple(grid.burst_regions_rel),
            manual_selection=bool(self.manual_peaks or self.burst_regions),
            manual_peak_selection=bool(self.manual_peaks),
            sefd_jy=self.sefd,
            npol=self.npol,
            distance_mpc=self.config.distance_mpc,
            redshift=self.config.redshift,
            sefd_fractional_uncertainty=self.config.sefd_fractional_uncertainty,
            distance_fractional_uncertainty=self.config.distance_fractional_uncertainty,
            masked_channels=np.flatnonzero(self.channel_mask).astype(int).tolist(),
            offpulse_regions_rel=grid.offpulse_regions_rel,
            noise_settings=self.noise_settings,
            width_results=[] if self.width_analysis is None else list(self.width_analysis.results),
            accepted_width=None if self.width_analysis is None else self.width_analysis.accepted_width,
            time_axis_ms=grid.time_axis_ms,
            timing_context=self._timing_context(),
        )
        if previous_results is not None and previous_results.diagnostics.scattering_fit is not None:
            fit_uncertainty_details = {
                key: value
                for key, value in previous_results.uncertainty_details.items()
                if key in {"width_ms_model", "tau_sc_ms"}
            }
            updated_flags = list(measurements.measurement_flags)
            if "fit" not in updated_flags:
                updated_flags.append("fit")
            measurements = replace(
                measurements,
                width_ms_model=previous_results.width_ms_model,
                tau_sc_ms=previous_results.tau_sc_ms,
                measurement_flags=updated_flags,
                uncertainties=replace(
                    measurements.uncertainties,
                    width_ms_model=(
                        compatible_scalar_uncertainty(fit_uncertainty_details.get("width_ms_model"))
                        if "width_ms_model" in fit_uncertainty_details
                        else previous_results.uncertainties.width_ms_model
                    ),
                    tau_sc_ms=(
                        compatible_scalar_uncertainty(fit_uncertainty_details.get("tau_sc_ms"))
                        if "tau_sc_ms" in fit_uncertainty_details
                        else previous_results.uncertainties.tau_sc_ms
                    ),
                ),
                uncertainty_details={**measurements.uncertainty_details, **fit_uncertainty_details},
                diagnostics=replace(
                    measurements.diagnostics,
                    scattering_fit=previous_results.diagnostics.scattering_fit,
                ),
            )
        self.results = measurements
        self._apply_width_analysis_to_results()
        return self.results

    def fit_scattering(self, config_data: dict[str, Any] | None = None) -> BurstMeasurements:
        if self.results is None:
            self.compute_properties()
        assert self.results is not None

        grid, context = self._build_measurement_context_for_data()
        selected_band = np.asarray(grid.masked[context.spec_lo : context.spec_hi + 1, :], dtype=float)
        selected_freqs = np.asarray(grid.freqs_mhz[context.spec_lo : context.spec_hi + 1], dtype=float)
        config_payload = dict(config_data or {})
        component_guesses = config_payload.get("component_guesses")
        if component_guesses is not None:
            if not isinstance(component_guesses, list):
                raise ValueError("component_guesses must be a list of per-component guess objects.")
            expected_components = int(config_payload.get("num_components", len(component_guesses)) or len(component_guesses))
            if expected_components != len(component_guesses):
                raise ValueError("Fit component count does not match the submitted initial guesses.")
            event_window_ms = self._current_event_window_ms(context, tsamp_ms=grid.effective_tsamp_ms)
            config_payload["initial_parameters"] = self._fitburst_initial_parameters_from_component_guesses(
                component_guesses,
                time_axis_ms=context.time_axis_ms,
                freqs_mhz=selected_freqs,
                validate=True,
                event_window_ms=event_window_ms,
            )
            config_payload["num_components"] = len(component_guesses)
        config = FitburstRequestConfig.from_dict(config_payload) if config_payload else None

        peak_rel_bin = _primary_peak_bin(
            peak_bins_abs=grid.peak_bins,
            profile_sn=context.selected_profile_sn,
            crop_start_bin=0,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
        )

        fit_result = fit_scattering_selected_band(
            selected_band=selected_band,
            freqs_mhz=selected_freqs,
            time_axis_ms=context.time_axis_ms,
            event_rel_start=context.event_rel_start,
            event_rel_end=context.event_rel_end,
            offpulse_bins=context.offpulse_bins,
            tsamp_ms=grid.effective_tsamp_ms,
            peak_rel_bin=None if peak_rel_bin is None else int(peak_rel_bin),
            width_guess_ms=self.results.width_ms_acf,
            config=config,
        )

        updated_flags = list(self.results.measurement_flags)
        if fit_result.status == "ok" and "fit" not in updated_flags:
            updated_flags.append("fit")
        updated_uncertainty_details = dict(self.results.uncertainty_details)
        if fit_result.status == "ok":
            updated_uncertainty_details.pop("width_ms_model", None)
            updated_uncertainty_details.pop("tau_sc_ms", None)
            updated_uncertainty_details.update(
                {
                    key: value
                    for key, value in fit_result.diagnostics.uncertainty_details.items()
                    if key in {"width_ms_model", "tau_sc_ms"}
                }
            )

        self.results = replace(
            self.results,
            width_ms_model=(
                fit_result.width_ms_model
                if fit_result.status == "ok"
                else self.results.width_ms_model
            ),
            tau_sc_ms=fit_result.tau_sc_ms if fit_result.status == "ok" else self.results.tau_sc_ms,
            measurement_flags=updated_flags,
            uncertainties=replace(
                self.results.uncertainties,
                width_ms_model=(
                    compatible_scalar_uncertainty(updated_uncertainty_details.get("width_ms_model"))
                    if fit_result.status == "ok"
                    else self.results.uncertainties.width_ms_model
                ),
                tau_sc_ms=(
                    compatible_scalar_uncertainty(updated_uncertainty_details.get("tau_sc_ms"))
                    if fit_result.status == "ok"
                    else self.results.uncertainties.tau_sc_ms
                ),
            ),
            uncertainty_details=updated_uncertainty_details,
            diagnostics=replace(
                self.results.diagnostics,
                scattering_fit=fit_result.diagnostics,
            ),
        )
        if self.temporal_structure is not None and fit_result.status == "ok":
            updated_widths_ms = self._fitburst_component_widths_ms()
            finite_widths = updated_widths_ms[np.isfinite(updated_widths_ms) & (updated_widths_ms > 0)]
            self.temporal_structure = replace(
                self.temporal_structure,
                fitburst_min_component_ms=(
                    None if finite_widths.size == 0 else float(np.min(finite_widths))
                ),
            )
        self._apply_width_analysis_to_results()
        return self.results

    def _fitburst_component_widths_ms(self) -> np.ndarray:
        if self.results is None or self.results.diagnostics.scattering_fit is None:
            return np.array([], dtype=float)
        bestfit = self.results.diagnostics.scattering_fit.bestfit_parameters or {}
        widths_sec = np.asarray(bestfit.get("burst_width", []), dtype=float)
        widths_sec = widths_sec[np.isfinite(widths_sec) & (widths_sec > 0)]
        return widths_sec * 1e3

    @staticmethod
    def _contiguous_profile_runs(profile: np.ndarray, bins: np.ndarray) -> list[np.ndarray]:
        values = np.asarray(profile, dtype=float)
        indices = np.asarray(bins, dtype=int)
        if values.size == 0 or indices.size == 0:
            return []
        indices = np.unique(indices[(indices >= 0) & (indices < values.size)])
        if indices.size == 0:
            return []
        split_after = np.flatnonzero(np.diff(indices) > 1) + 1
        return [np.asarray(values[run], dtype=float) for run in np.split(indices, split_after) if run.size]

    def run_temporal_structure_analysis(self, segment_length_ms: float) -> TemporalStructureResult:
        grid, context = self._build_measurement_context_for_data()
        event_series = np.asarray(
            context.selected_profile_baselined[context.event_rel_start:context.event_rel_end],
            dtype=float,
        )
        self.temporal_structure = run_temporal_structure_analysis(
            event_series=event_series,
            tsamp_ms=grid.effective_tsamp_ms,
            segment_length_ms=float(segment_length_ms),
            noise_sigma=float(context.noise_summary.sigma),
            event_window_ms=self._current_event_window_ms(context, tsamp_ms=grid.effective_tsamp_ms),
            spectral_extent_mhz=[
                float(np.min(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
                float(np.max(context.spectral_axis_mhz)) if context.spectral_axis_mhz.size else 0.0,
            ],
            fitburst_widths_ms=self._fitburst_component_widths_ms(),
            offpulse_series_runs=self._contiguous_profile_runs(
                context.selected_profile_baselined,
                context.offpulse_bins,
            ),
        )
        self.spectral_analysis = temporal_to_spectral_result(self.temporal_structure)
        return self.temporal_structure

    def run_spectral_analysis(self, segment_length_ms: float) -> SpectralAnalysisResult:
        self.run_temporal_structure_analysis(segment_length_ms)
        return self.spectral_analysis

    def _build_source_ref(self) -> SessionSourceRef:
        source_path = Path(self.burst_file).expanduser().resolve()
        content_hash_sha256: str | None = None
        content_hash_algorithm: str | None = None
        try:
            stat = source_path.stat()
            file_size = int(stat.st_size)
            mtime_unix = float(stat.st_mtime)
            content_hash_sha256 = _sha256_file(source_path)
            content_hash_algorithm = SOURCE_HASH_ALGORITHM
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
            header_npol=int(self.header_npol),
            polarization_order=self.polarization_order,
            freq_range_mhz=[float(freq_lo), float(freq_hi)],
            file_name=source_path.name,
            data_dir_relative_path=_data_dir_relative_path(source_path),
            content_hash_algorithm=content_hash_algorithm,
            content_hash_sha256=content_hash_sha256,
        )

    def _validate_snapshot_source(self, source: SessionSourceRef) -> None:
        current = self._build_source_ref()
        if current.file_size_bytes != int(source.file_size_bytes):
            raise ValueError("Session source file size does not match the saved snapshot.")
        if current.shape != list(source.shape):
            raise ValueError("Session source shape does not match the saved snapshot.")
        expected_hash = _snapshot_expected_sha256(source)
        if expected_hash is not None:
            if current.content_hash_sha256 is None:
                raise ValueError("Session source SHA-256 hash is unavailable.")
            if current.content_hash_sha256.lower() != expected_hash:
                raise ValueError("Session source SHA-256 hash does not match the saved snapshot.")
        comparisons = [
            ("tsamp", current.tsamp, float(source.tsamp)),
            ("freqres", current.freqres, float(source.freqres)),
            ("start_mjd", current.start_mjd, float(source.start_mjd)),
            ("npol", float(current.npol), float(source.npol)),
            ("freq_range_mhz[0]", current.freq_range_mhz[0], float(source.freq_range_mhz[0])),
            ("freq_range_mhz[1]", current.freq_range_mhz[1], float(source.freq_range_mhz[1])),
        ]
        if source.header_npol is not None:
            comparisons.append(("header_npol", float(current.header_npol or 0), float(source.header_npol)))
        for label, current_value, saved_value in comparisons:
            if not np.isclose(current_value, saved_value):
                raise ValueError(f"Session source metadata mismatch for {label}.")
        if source.polarization_order is not None:
            current_order = "" if current.polarization_order is None else str(current.polarization_order).upper()
            saved_order = str(source.polarization_order).upper()
            if current_order != saved_order:
                raise ValueError("Session source metadata mismatch for polarization_order.")

    def to_snapshot(self) -> AnalysisSessionSnapshot:
        return AnalysisSessionSnapshot(
            schema_version=SESSION_SNAPSHOT_SCHEMA_VERSION,
            source=self._build_source_ref(),
            dm=float(self.dm),
            preset_key=self.config.preset_key,
            sefd_jy=self.config.sefd_jy,
            npol_override=self.config.npol_override,
            read_start_sec=float(self.config.read_start_sec),
            read_end_sec=self.config.read_end_sec,
            auto_mask_profile=self.config.auto_mask_profile,
            distance_mpc=self.config.distance_mpc,
            redshift=self.config.redshift,
            sefd_fractional_uncertainty=self.config.sefd_fractional_uncertainty,
            distance_fractional_uncertainty=self.config.distance_fractional_uncertainty,
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
            temporal_structure=self.temporal_structure,
            source_ra_deg=self.config.source_ra_deg,
            source_dec_deg=self.config.source_dec_deg,
            time_scale=self.config.time_scale,
            observatory_longitude_deg=self.config.observatory_longitude_deg,
            observatory_latitude_deg=self.config.observatory_latitude_deg,
            observatory_height_m=self.config.observatory_height_m,
        )

    def snapshot_dict(self) -> dict[str, Any]:
        return self.to_snapshot().to_dict()

    def export_results(
        self,
        *,
        session_id: str,
        include: list[str] | tuple[str, ...] | None = None,
        plot_formats: list[str] | tuple[str, ...] | None = None,
        window_formats: list[str] | tuple[str, ...] | None = None,
        window_resolutions: list[str] | tuple[str, ...] | None = None,
    ) -> ExportManifest:
        snapshot = create_export_snapshot(
            self,
            session_id=session_id,
            include=include,
            plot_formats=plot_formats,
            window_formats=window_formats,
            window_resolutions=window_resolutions,
        )
        self.export_snapshots[snapshot.manifest.export_id] = snapshot
        self.export_order.append(snapshot.manifest.export_id)
        while len(self.export_order) > MAX_EXPORT_SNAPSHOTS:
            evicted_id = self.export_order.pop(0)
            self.export_snapshots.pop(evicted_id, None)
        return snapshot.manifest

    def preview_export_results(
        self,
        *,
        include: list[str] | tuple[str, ...] | None = None,
        plot_formats: list[str] | tuple[str, ...] | None = None,
        window_formats: list[str] | tuple[str, ...] | None = None,
        window_resolutions: list[str] | tuple[str, ...] | None = None,
    ) -> ExportPreview:
        return preview_export(
            self,
            include=include,
            plot_formats=plot_formats,
            window_formats=window_formats,
            window_resolutions=window_resolutions,
        )

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
