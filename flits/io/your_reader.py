from __future__ import annotations

import errno
import os
import shutil
import struct
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Iterator

import numpy as np

from flits.io.errors import (
    CorruptedDataError,
    FormatDetectionError,
    MetadataMissingError,
)
from flits.io.psrfits import (
    _build_stokes_i,
    _decode_source_name,
    _decode_telescope_name,
    _inspect_folded_psrfits,
    _is_psrfits_fold_mode,
    _is_psrfits_search_mode,
    _load_folded_psrfits,
    _looks_like_psrfits,
    _normalise_polarization_order,
    _peek_bytes,
    _peek_header_freq_range,
    _psrfits_primary_fallback,
    _reader_timing_metadata,
    _safe_bool_flag,
    _safe_float,
    _safe_int,
)
from flits.io.reader import FilterbankInspection
from flits.io.validation import validate_metadata
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig, detect_preset, resolve_default_sefd_jy
from flits.signal import dedisperse, normalize

try:
    import your as _your
    _YOUR_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on optional runtime stack
    _your = SimpleNamespace(Your=None)
    _YOUR_IMPORT_ERROR = exc

your = _your


_SIGPROC_MAGIC = struct.pack("i", 12) + b"HEADER_START"
_FITS_MAGIC = b"SIMPLE  ="
_PSRFITS_FOLD_MODES = frozenset({"PSR", "FOLD"})


def _is_mmap_deadlock(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.EDEADLK


def _close_reader(reader: object | None) -> None:
    if reader is None:
        return
    fp = getattr(reader, "fp", None)
    if fp is not None and not getattr(fp, "closed", True):
        try:
            fp.close()
        except OSError:
            pass


@contextmanager
def _open_your(source_path: Path) -> Iterator[object]:
    """Open a your.Your reader, falling back to a local copy on mmap EDEADLK.

    Some network/FUSE mounts (sshfs, SMB, iCloud) return EDEADLK from mmap();
    copying to a local tempdir sidesteps this without changing the read path.
    """
    if your.Your is None:
        raise RuntimeError(
            "The 'your' package is unavailable in the active environment."
        ) from _YOUR_IMPORT_ERROR

    temp_path: Path | None = None
    reader: object | None = None
    try:
        try:
            reader = your.Your(str(source_path))
        except OSError as exc:
            if not _is_mmap_deadlock(exc):
                raise
            tmp_dir = Path(tempfile.gettempdir())
            temp_path = tmp_dir / f"flits_fb_{os.getpid()}_{source_path.name}"
            shutil.copyfile(source_path, temp_path)
            reader = your.Your(str(temp_path))
        yield reader
    finally:
        _close_reader(reader)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


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


class YourFilterbankReader:
    """Reader backed by `your` for SIGPROC/search PSRFITS and Astropy for folded PSRFITS.

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

        with _open_your(source_path) as reader:
            telescope_id = _safe_int(getattr(reader, "telescope_id", None))
            machine_id = _safe_int(getattr(reader, "machine_id", None))
            source_name = _decode_source_name(getattr(reader, "source_name", None))
            telescope_name = _decode_telescope_name(
                getattr(reader, "telescope_name", None)
                or getattr(getattr(reader, "your_header", None), "telescope", None)
            )
            freq_lo, freq_hi = _peek_header_freq_range(getattr(reader, "your_header", None))
            timing_metadata = _reader_timing_metadata(reader)

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
        return FilterbankInspection(
            source_path=source_path,
            source_name=source_name,
            telescope_id=telescope_id,
            machine_id=machine_id,
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
        source_path = Path(path).expanduser().resolve()
        is_fits = source_path.suffix.lower() in {".fits", ".sf", ".ar"}

        if is_fits:
            is_search, obs_mode = _is_psrfits_search_mode(source_path)
            is_fold, _ = _is_psrfits_fold_mode(source_path)
            if is_fold:
                return _load_folded_psrfits(source_path, config, inspection=inspection)
            if not is_search:
                raise FormatDetectionError(
                    f"PSRFITS file is in {obs_mode!r} mode; FLITS supports SEARCH and PSR fold-mode data.",
                    path=source_path,
                )
            fits_fallback = _psrfits_primary_fallback(source_path)
        else:
            fits_fallback = {}

        filterbank_inspection = inspection
        with _open_your(source_path) as reader:
            header = reader.your_header
            timing_metadata = _reader_timing_metadata(reader)
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
                telescope_id = _safe_int(getattr(reader, "telescope_id", None))
                machine_id = _safe_int(getattr(reader, "machine_id", None))
                source_name = _decode_source_name(getattr(reader, "source_name", None))
                telescope_name = _decode_telescope_name(
                    getattr(reader, "telescope_name", None)
                    or getattr(header, "telescope", None)
                )
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

            tsamp = float(_coerce_header_field(
                header, "tsamp",
                fallback=fits_fallback.get("tsamp") if is_fits else None,
                path=source_path,
            ))
            foff_raw = _coerce_header_field(header, "foff", path=source_path)
            freqres = float(abs(float(foff_raw)))
            start_mjd_raw = _coerce_header_field(
                header, "tstart",
                fallback=fits_fallback.get("tstart") if is_fits else None,
                path=source_path,
            )
            start_mjd = float(start_mjd_raw)
            bw = float(abs(float(_coerce_header_field(header, "bw", path=source_path))))
            header_npol = max(1, int(_coerce_header_field(header, "npol", fallback=1, path=source_path)))
            polarization_order = _normalise_polarization_order(
                _decode_source_name(getattr(header, "poln_order", None))
            )
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

            raw = reader.get_data(nstart, nread, npoln=header_npol)
            stokes_i, effective_npol = _build_stokes_i(raw, polarization_order=polarization_order)
            effective_npol = (
                max(1, int(config.npol_override)) if config.npol_override is not None else effective_npol
            )

            if stokes_i.shape[0] != nchans:
                raise CorruptedDataError(
                    f"Data channel count {stokes_i.shape[0]} does not match header nchans={nchans}",
                    path=source_path,
                )

            stokes_i = dedisperse(stokes_i, config.dm, freqs_mhz, tsamp)

            tail_fraction = float(np.clip(config.normalization_tail_fraction, 0.05, 0.95))
            offpulse_start = min(stokes_i.shape[1] - 1, int((1 - tail_fraction) * stokes_i.shape[1]))
            offpulse = stokes_i[:, offpulse_start:]
            stokes_i = normalize(stokes_i, offpulse).astype(np.float32, copy=False)

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
                filterbank_inspection.source_position_basis
                or timing_metadata.get("source_position_basis")  # type: ignore[arg-type]
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
            dedispersion_reference_frequency_mhz=(
                float(np.max(freqs_mhz)) if abs(float(config.dm)) > 0.0 else None
            ),
            dedispersion_reference_basis=(
                "flits_integer_bin_dedispersion_max_frequency" if abs(float(config.dm)) > 0.0 else None
            ),
        )
        return stokes_i, validate_metadata(metadata)


__all__ = ["YourFilterbankReader"]
