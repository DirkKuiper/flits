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


def _build_stokes_i(raw: np.ndarray) -> tuple[np.ndarray, int]:
    if raw.ndim == 2:
        return raw.T, 1
    aa = raw[:, 0, :].T
    if raw.shape[1] >= 2:
        bb = raw[:, 1, :].T
        return aa + bb, 2
    return aa, 1


def _decode_source_name(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    text = str(value)
    return text or None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _peek_bytes(path: Path, n: int) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(n)
    except OSError:
        return b""


def _is_psrfits_search_mode(path: Path) -> tuple[bool, str | None]:
    """Inspect a FITS primary header to detect fold-mode PSRFITS.

    Returns (is_search_mode, obs_mode_value). If astropy isn't available, returns
    (True, None) optimistically — `your` will fail informatively on fold files.
    """
    try:
        from astropy.io import fits
    except Exception:
        return True, None

    try:
        with fits.open(path, memmap=False) as hdul:
            primary = hdul[0].header
            obs_mode = str(primary.get("OBS_MODE", "")).strip().upper()
    except Exception:
        return True, None

    if not obs_mode:
        return True, None
    return obs_mode.startswith("SEARCH"), obs_mode


def _looks_like_psrfits(path: Path) -> bool:
    """Return True only for FITS files that look structurally like PSRFITS."""
    try:
        from astropy.io import fits
    except Exception:
        return False

    try:
        with fits.open(path, memmap=False) as hdul:
            primary = hdul[0].header
            fitstype = str(primary.get("FITSTYPE", "")).strip().upper()
            obs_mode = str(primary.get("OBS_MODE", "")).strip().upper()
            subint = next(
                (
                    hdu
                    for hdu in hdul[1:]
                    if str(hdu.header.get("EXTNAME", "")).strip().upper() == "SUBINT"
                ),
                None,
            )
            if subint is None:
                return False

            subint_header = subint.header
            subint_keywords_present = all(
                key in subint_header for key in ("TBIN", "NCHAN", "NPOL")
            )
            primary_looks_psrfits = (
                "PSRFITS" in fitstype or "STT_IMJD" in primary or "STT_SMJD" in primary
            )
            mode_looks_search = (not obs_mode) or obs_mode.startswith("SEARCH")
            return mode_looks_search and (primary_looks_psrfits or subint_keywords_present)
    except Exception:
        return False


def _psrfits_primary_fallback(path: Path) -> dict[str, object]:
    """Extract commonly-missing PSRFITS metadata directly from the FITS headers.

    `your`'s PSRFITS handling occasionally leaves `tstart`, `telescope_id`, or
    `source_name` unset on non-canonical files (some MeerKAT/Parkes variants).
    This pulls them from the primary and SUBINT headers via astropy.
    """
    try:
        from astropy.io import fits
    except Exception:
        return {}

    out: dict[str, object] = {}
    try:
        with fits.open(path, memmap=False) as hdul:
            primary = hdul[0].header
            stt_imjd = primary.get("STT_IMJD")
            stt_smjd = primary.get("STT_SMJD")
            stt_offs = primary.get("STT_OFFS", 0.0) or 0.0
            if stt_imjd is not None and stt_smjd is not None:
                out["tstart"] = float(stt_imjd) + (float(stt_smjd) + float(stt_offs)) / 86400.0
            telescope = primary.get("TELESCOP")
            if telescope:
                out["telescope_name"] = str(telescope).strip()
            source = primary.get("SRC_NAME")
            if source:
                out["source_name"] = str(source).strip()
    except Exception:
        return out
    return out


def _decode_telescope_name(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace").strip()
    else:
        text = str(value).strip()
    return text or None


def _peek_header_freq_range(header: object) -> tuple[float | None, float | None]:
    """Return (lo, hi) MHz from a `your` header, or (None, None) if any field is missing."""
    fch1 = getattr(header, "fch1", None)
    foff = getattr(header, "foff", None)
    nchans = getattr(header, "nchans", None)
    try:
        if fch1 is None or foff is None or nchans is None:
            return (None, None)
        fch1_f = float(fch1)
        foff_f = float(foff)
        nchans_i = int(nchans)
    except (TypeError, ValueError):
        return (None, None)
    if nchans_i <= 0 or not np.isfinite(fch1_f) or not np.isfinite(foff_f):
        return (None, None)
    lo = fch1_f
    hi = fch1_f + foff_f * (nchans_i - 1)
    return (min(lo, hi), max(lo, hi))


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
    """Reader backed by the `your` library for SIGPROC .fil and PSRFITS search-mode.

    Registered for both `.fil` (SIGPROC) and `.fits` / `.sf` (PSRFITS). PSRFITS
    fold-mode files are rejected explicitly — FLITS is a burst tool, not a
    timing tool. Missing PSRFITS header fields are patched in from the FITS
    primary header via astropy where possible.
    """

    format_id: ClassVar[str] = "sigproc"
    format_id_aliases: ClassVar[tuple[str, ...]] = ("psrfits", "your", "fil", "fits")
    extensions: ClassVar[tuple[str, ...]] = (".fil", ".fits", ".sf")

    def sniff(self, path: Path) -> bool:
        head = _peek_bytes(path, 16)
        if head.startswith(_SIGPROC_MAGIC):
            return True
        if head.startswith(_FITS_MAGIC):
            return _looks_like_psrfits(path)
        return False

    def inspect(self, path: Path) -> FilterbankInspection:
        source_path = Path(path).expanduser().resolve()
        is_fits = source_path.suffix.lower() in {".fits", ".sf"}

        if is_fits:
            is_search, obs_mode = _is_psrfits_search_mode(source_path)
            if not is_search:
                raise FormatDetectionError(
                    f"PSRFITS file is in {obs_mode!r} mode; FLITS requires SEARCH-mode data.",
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

        if is_fits and source_name is None:
            source_name = fits_fallback.get("source_name")  # type: ignore[assignment]
        if telescope_name is None:
            telescope_name = fits_fallback.get("telescope_name")  # type: ignore[assignment]

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
        )

    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        source_path = Path(path).expanduser().resolve()
        is_fits = source_path.suffix.lower() in {".fits", ".sf"}

        if is_fits:
            is_search, obs_mode = _is_psrfits_search_mode(source_path)
            if not is_search:
                raise FormatDetectionError(
                    f"PSRFITS file is in {obs_mode!r} mode; FLITS requires SEARCH-mode data.",
                    path=source_path,
                )
            fits_fallback = _psrfits_primary_fallback(source_path)
        else:
            fits_fallback = {}

        filterbank_inspection = inspection
        with _open_your(source_path) as reader:
            header = reader.your_header
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
            stokes_i, effective_npol = _build_stokes_i(raw)
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
            telescope_id=filterbank_inspection.telescope_id,
            machine_id=filterbank_inspection.machine_id,
            detected_preset_key=filterbank_inspection.detected_preset_key,
            detection_basis=filterbank_inspection.detection_basis,
        )
        return stokes_i, validate_metadata(metadata)


__all__ = ["YourFilterbankReader"]
