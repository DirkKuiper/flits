"""Reader for SIGPROC filterbank and PSRFITS data.

The module keeps its historical name: it used to wrap the ``your`` package,
and ``YourFilterbankReader`` is the entry point registered for these formats.
Search-mode files are now read by :mod:`flits.io.search_data`.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import ClassVar

import numpy as np

from flits.io.errors import (
    CorruptedDataError,
    FormatDetectionError,
    MetadataMissingError,
    PolarizationUnavailableError,
)
from flits.io.psrfits import (
    _build_stokes_i,
    _decode_source_name,
    _decode_telescope_name,
    _dedisperse_any,
    _effective_preset_key,
    _inspect_folded_psrfits,
    _is_psrfits_fold_mode,
    _is_psrfits_search_mode,
    _load_folded_psrfits,
    _looks_like_psrfits,
    _normalise_polarization_order,
    _peek_bytes,
    _peek_header_freq_range,
    _psrfits_feed_polarization,
    _psrfits_primary_fallback,
    _reader_timing_metadata,
    _safe_bool_flag,
    _safe_float,
    _safe_int,
    require_stokes_basis,
    resolve_stokes_basis,
)
from flits.io.reader import FilterbankInspection
from flits.io.search_data import open_search_file
from flits.io.validation import validate_metadata
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig, detect_preset, resolve_default_sefd_jy
from flits.signal import normalize, normalize_stokes
from flits.stokes import basis_from_psrfits, stokes_from_products

_SIGPROC_MAGIC = struct.pack("i", 12) + b"HEADER_START"
_FITS_MAGIC = b"SIMPLE  ="
_PSRFITS_FOLD_MODES = frozenset({"PSR", "FOLD"})


def _coerce_header_field(
    header: object,
    name: str,
    *,
    fallback: object | None = None,
    required: bool = True,
    path: Path | None = None,
) -> object:
    value = getattr(header, name, None)
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        if fallback is not None:
            return fallback
        if required:
            raise MetadataMissingError(
                f"Header field {name!r} missing and no fallback available",
                path=path,
                fields=(name,),
            )
    return value


def _header_polarization_basis(
    path: Path,
    *,
    is_fits: bool,
    polarization_order: str | None,
) -> str | None:
    """Return the polarization basis the file's own header establishes.

    SIGPROC has no field that records which four products a ``nifs=4`` file
    holds -- the reader reports ``IQUV`` for every such filterbank regardless of
    what is in it -- so a filterbank header establishes nothing and returns
    None. PSRFITS does record it, in ``POL_TYPE`` plus ``FD_POLN``.
    """
    if not is_fits:
        return None
    return basis_from_psrfits(polarization_order, _psrfits_feed_polarization(path))


class YourFilterbankReader:
    """Reader for SIGPROC filterbank and search-mode or folded PSRFITS data.

    Folded PSRFITS files are exposed as pseudo-time waterfalls by treating phase
    bins as time bins and averaging subintegrations.
    """

    format_id: ClassVar[str] = "sigproc"
    format_id_aliases: ClassVar[tuple[str, ...]] = ("psrfits", "your", "fil", "fits", "ar")
    extensions: ClassVar[tuple[str, ...]] = (".fil", ".fits", ".sf", ".ar")

    def sniff(self, path: Path) -> bool:
        head = _peek_bytes(path, 16)
        if head.startswith(_SIGPROC_MAGIC):
            return True
        if head.startswith(_FITS_MAGIC):
            return _looks_like_psrfits(path)
        return False

    def inspect(self, path: Path) -> FilterbankInspection:
        source_path = Path(path).expanduser().resolve()
        is_fits = source_path.suffix.lower() in {".fits", ".sf", ".ar"}

        if is_fits:
            is_search, obs_mode = _is_psrfits_search_mode(source_path)
            is_fold, _ = _is_psrfits_fold_mode(source_path)
            if is_fold:
                return _inspect_folded_psrfits(source_path)
            if not is_search:
                raise FormatDetectionError(
                    f"PSRFITS file is in {obs_mode!r} mode; FLITS supports SEARCH and PSR fold-mode data.",
                    path=source_path,
                )

        fits_fallback = _psrfits_primary_fallback(source_path) if is_fits else {}

        header = open_search_file(source_path, is_fits=is_fits).header
        telescope_id = _safe_int(header.telescope_id)
        machine_id = _safe_int(header.machine_id)
        source_name = _decode_source_name(header.source_name)
        telescope_name = _decode_telescope_name(header.telescope_name)
        freq_lo, freq_hi = _peek_header_freq_range(header)
        timing_metadata = _reader_timing_metadata(header)
        product_count = max(1, header.npol)
        header_basis = _header_polarization_basis(
            source_path,
            is_fits=is_fits,
            polarization_order=_normalise_polarization_order(_decode_source_name(header.poln_order)),
        )

        if is_fits and source_name is None:
            source_name = fits_fallback.get("source_name")  # type: ignore[assignment]
        if telescope_name is None:
            telescope_name = fits_fallback.get("telescope_name")  # type: ignore[assignment]
        for key in (
            "source_ra_deg",
            "source_dec_deg",
            "source_position_basis",
            "barycentric_header_flag",
            "pulsarcentric_header_flag",
        ):
            if key not in timing_metadata and key in fits_fallback:
                timing_metadata[key] = fits_fallback[key]

        schema_version = "psrfits_search" if is_fits else "sigproc_search"

        detected_preset_key, detection_basis = detect_preset(
            telescope_id,
            machine_id,
            telescope_name=telescope_name,
            schema_version=schema_version,
            freq_lo_mhz=freq_lo,
            freq_hi_mhz=freq_hi,
        )
        stokes_basis, stokes_basis_source = resolve_stokes_basis(
            config=None,
            preset_key=detected_preset_key,
            header_basis=header_basis,
        )
        return FilterbankInspection(
            source_path=source_path,
            source_name=source_name,
            telescope_id=telescope_id,
            machine_id=machine_id,
            polarization_products=product_count,
            polarization_basis=stokes_basis,
            polarization_basis_source=None if stokes_basis is None else stokes_basis_source,
            detected_preset_key=detected_preset_key,
            detection_basis=detection_basis,
            telescope_name=telescope_name,
            schema_version=schema_version,
            freq_lo_mhz=freq_lo,
            freq_hi_mhz=freq_hi,
            source_ra_deg=_safe_float(timing_metadata.get("source_ra_deg")),
            source_dec_deg=_safe_float(timing_metadata.get("source_dec_deg")),
            source_position_basis=timing_metadata.get("source_position_basis"),  # type: ignore[arg-type]
            time_scale="utc",
            time_reference_frame="topocentric",
            barycentric_header_flag=_safe_bool_flag(timing_metadata.get("barycentric_header_flag")),
            pulsarcentric_header_flag=_safe_bool_flag(timing_metadata.get("pulsarcentric_header_flag")),
        )

    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        return self._load_arrays(path, config, inspection=inspection, want_stokes=False)

    def load_stokes(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        """Load a (4, channels, time) Stokes I/Q/U/V cube.

        Shares every axis-defining step with `load`, so the cube lines up bin
        for bin and channel for channel with the Stokes I array.
        """
        return self._load_arrays(path, config, inspection=inspection, want_stokes=True)

    def _load_arrays(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
        *,
        want_stokes: bool,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        source_path = Path(path).expanduser().resolve()
        is_fits = source_path.suffix.lower() in {".fits", ".sf", ".ar"}

        if is_fits:
            is_search, obs_mode = _is_psrfits_search_mode(source_path)
            is_fold, _ = _is_psrfits_fold_mode(source_path)
            if is_fold:
                return _load_folded_psrfits(
                    source_path,
                    config,
                    inspection=inspection,
                    want_stokes=want_stokes,
                )
            if not is_search:
                raise FormatDetectionError(
                    f"PSRFITS file is in {obs_mode!r} mode; FLITS supports SEARCH and PSR fold-mode data.",
                    path=source_path,
                )
            fits_fallback = _psrfits_primary_fallback(source_path)
        else:
            fits_fallback = {}

        filterbank_inspection = inspection
        data_file = open_search_file(source_path, is_fits=is_fits)
        header = data_file.header
        timing_metadata = _reader_timing_metadata(header)
        for key in (
            "source_ra_deg",
            "source_dec_deg",
            "source_position_basis",
            "barycentric_header_flag",
            "pulsarcentric_header_flag",
        ):
            if key not in timing_metadata and key in fits_fallback:
                timing_metadata[key] = fits_fallback[key]
        if filterbank_inspection is None:
            telescope_id = _safe_int(header.telescope_id)
            machine_id = _safe_int(header.machine_id)
            source_name = _decode_source_name(header.source_name)
            telescope_name = _decode_telescope_name(header.telescope_name)
            if is_fits and source_name is None:
                source_name = fits_fallback.get("source_name")  # type: ignore[assignment]
            if telescope_name is None:
                telescope_name = fits_fallback.get("telescope_name")  # type: ignore[assignment]
            freq_lo_hint, freq_hi_hint = _peek_header_freq_range(header)
            schema_version = "psrfits_search" if is_fits else "sigproc_search"
            detected_preset_key, detection_basis = detect_preset(
                telescope_id,
                machine_id,
                telescope_name=telescope_name,
                schema_version=schema_version,
                freq_lo_mhz=freq_lo_hint,
                freq_hi_mhz=freq_hi_hint,
            )
            filterbank_inspection = FilterbankInspection(
                source_path=source_path,
                source_name=source_name,
                telescope_id=telescope_id,
                machine_id=machine_id,
                detected_preset_key=detected_preset_key,
                detection_basis=detection_basis,
                telescope_name=telescope_name,
                schema_version=schema_version,
                freq_lo_mhz=freq_lo_hint,
                freq_hi_mhz=freq_hi_hint,
                source_ra_deg=_safe_float(timing_metadata.get("source_ra_deg")),
                source_dec_deg=_safe_float(timing_metadata.get("source_dec_deg")),
                source_position_basis=timing_metadata.get("source_position_basis"),  # type: ignore[arg-type]
                time_scale="utc",
                time_reference_frame="topocentric",
                barycentric_header_flag=_safe_bool_flag(timing_metadata.get("barycentric_header_flag")),
                pulsarcentric_header_flag=_safe_bool_flag(timing_metadata.get("pulsarcentric_header_flag")),
            )

        tsamp = float(
            _coerce_header_field(
                header,
                "tsamp",
                fallback=fits_fallback.get("tsamp") if is_fits else None,
                path=source_path,
            )
        )
        foff_raw = _coerce_header_field(header, "foff", path=source_path)
        freqres = float(abs(float(foff_raw)))
        start_mjd_raw = _coerce_header_field(
            header,
            "tstart",
            fallback=fits_fallback.get("tstart") if is_fits else None,
            path=source_path,
        )
        start_mjd = float(start_mjd_raw)
        bw = float(abs(float(_coerce_header_field(header, "bw", path=source_path))))
        header_npol = max(1, int(_coerce_header_field(header, "npol", fallback=1, path=source_path)))
        polarization_order = _normalise_polarization_order(_decode_source_name(getattr(header, "poln_order", None)))
        fch1 = float(_coerce_header_field(header, "fch1", path=source_path))
        nchans = int(_coerce_header_field(header, "nchans", path=source_path))
        nspectra = int(_coerce_header_field(header, "nspectra", path=source_path))

        freqs_mhz = fch1 + (float(foff_raw) * np.arange(nchans, dtype=float))
        freq_lo = float(np.min(freqs_mhz))
        freq_hi = float(np.max(freqs_mhz))
        sefd_jy = config.sefd_jy
        if sefd_jy is None:
            sefd_jy = resolve_default_sefd_jy(config.preset_key, freq_lo, freq_hi)

        read_start_sec = config.read_start_for_file(source_path.name)
        nstart = min(max(int(read_start_sec / tsamp), 0), max(nspectra - 1, 0))
        nread = max(1, int(nspectra - nstart))

        if config.read_end_sec is not None:
            nend = max(nstart + 1, int(config.read_end_sec / tsamp))
            requested_nread = nend - nstart
            nread = min(nread, requested_nread)

        raw = data_file.read(nstart, nread)
        stokes_basis: str | None = None
        stokes_basis_source: str | None = None
        if want_stokes:
            stokes_basis, stokes_basis_source = require_stokes_basis(
                config=config,
                preset_key=_effective_preset_key(config, filterbank_inspection),
                header_basis=_header_polarization_basis(
                    source_path,
                    is_fits=is_fits,
                    polarization_order=polarization_order,
                ),
                product_count=header_npol,
                path=source_path,
            )
            products = np.asarray(raw)
            if products.ndim != 3 or products.shape[1] < 4:
                raise PolarizationUnavailableError(
                    f"Expected four polarization products from the file, got shape {products.shape}.",
                    path=source_path,
                    reason="insufficient_products",
                )
            # (time, product, channel) -> (product, channel, time)
            values = stokes_from_products(products.transpose(1, 2, 0), stokes_basis)
            effective_npol = 2
        else:
            values, effective_npol = _build_stokes_i(
                raw,
                polarization_order=polarization_order,
                preset_key=_effective_preset_key(config, filterbank_inspection),
            )
        effective_npol = max(1, int(config.npol_override)) if config.npol_override is not None else effective_npol

        if values.shape[-2] != nchans:
            raise CorruptedDataError(
                f"Data channel count {values.shape[-2]} does not match header nchans={nchans}",
                path=source_path,
            )

        values = _dedisperse_any(values, config.dm, freqs_mhz, tsamp)

        tail_fraction = float(np.clip(config.normalization_tail_fraction, 0.05, 0.95))
        offpulse_start = min(values.shape[-1] - 1, int((1 - tail_fraction) * values.shape[-1]))
        offpulse = values[..., offpulse_start:]
        values = (normalize_stokes(values, offpulse) if want_stokes else normalize(values, offpulse)).astype(
            np.float32, copy=False
        )

        metadata = FilterbankMetadata(
            source_path=source_path,
            source_name=filterbank_inspection.source_name,
            tsamp=tsamp,
            freqres=freqres,
            start_mjd=start_mjd,
            read_start_sec=read_start_sec,
            sefd_jy=sefd_jy,
            bandwidth_mhz=bw,
            npol=effective_npol,
            freqs_mhz=freqs_mhz,
            header_npol=header_npol,
            polarization_order=polarization_order,
            telescope_id=filterbank_inspection.telescope_id,
            machine_id=filterbank_inspection.machine_id,
            detected_preset_key=filterbank_inspection.detected_preset_key,
            detection_basis=filterbank_inspection.detection_basis,
            source_ra_deg=(
                filterbank_inspection.source_ra_deg
                if filterbank_inspection.source_ra_deg is not None
                else _safe_float(timing_metadata.get("source_ra_deg"))
            ),
            source_dec_deg=(
                filterbank_inspection.source_dec_deg
                if filterbank_inspection.source_dec_deg is not None
                else _safe_float(timing_metadata.get("source_dec_deg"))
            ),
            source_position_basis=(
                filterbank_inspection.source_position_basis or timing_metadata.get("source_position_basis")  # type: ignore[arg-type]
            ),
            time_scale=filterbank_inspection.time_scale or "utc",
            time_reference_frame=filterbank_inspection.time_reference_frame or "topocentric",
            barycentric_header_flag=(
                filterbank_inspection.barycentric_header_flag
                if filterbank_inspection.barycentric_header_flag is not None
                else _safe_bool_flag(timing_metadata.get("barycentric_header_flag"))
            ),
            pulsarcentric_header_flag=(
                filterbank_inspection.pulsarcentric_header_flag
                if filterbank_inspection.pulsarcentric_header_flag is not None
                else _safe_bool_flag(timing_metadata.get("pulsarcentric_header_flag"))
            ),
            dedispersion_reference_frequency_mhz=(float(np.max(freqs_mhz)) if abs(float(config.dm)) > 0.0 else None),
            dedispersion_reference_basis=(
                "flits_integer_bin_dedispersion_max_frequency" if abs(float(config.dm)) > 0.0 else None
            ),
            polarization_basis=stokes_basis,
            polarization_basis_source=stokes_basis_source,
        )
        return values, validate_metadata(metadata)


__all__ = ["YourFilterbankReader"]
