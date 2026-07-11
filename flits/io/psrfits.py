from __future__ import annotations

from pathlib import Path

import numpy as np

from flits.io.errors import CorruptedDataError, FormatDetectionError, MetadataMissingError
from flits.io.reader import FilterbankInspection
from flits.io.validation import validate_metadata
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig, detect_preset, resolve_default_sefd_jy
from flits.signal import dedisperse, normalize


_PSRFITS_FOLD_MODES = frozenset({"PSR", "FOLD"})


def _normalise_polarization_order(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.upper() if text else None


def _build_stokes_i(raw: np.ndarray, polarization_order: str | None = None) -> tuple[np.ndarray, int]:
    normalized_order = _normalise_polarization_order(polarization_order)
    if raw.ndim == 2:
        effective_npol = 2 if normalized_order == "IQUV" else 1
        return raw.T, effective_npol
    aa = raw[:, 0, :].T
    if normalized_order == "IQUV" and raw.shape[1] >= 4:
        return aa, 2
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


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _safe_bool_flag(value: object) -> bool | None:
    if value is None:
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        if text in {"true", "t", "yes", "y"}:
            return True
        if text in {"false", "f", "no", "n"}:
            return False
        return None


def _first_present(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


def _packed_sexagesimal_to_deg(value: object, *, is_ra: bool) -> float | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    sign = -1.0 if numeric < 0 else 1.0
    current = abs(numeric)
    first = int(current // 10000)
    minutes = int((current - first * 10000) // 100)
    seconds = current - first * 10000 - minutes * 100
    if minutes >= 60 or seconds >= 60:
        return None
    degrees = first + minutes / 60.0 + seconds / 3600.0
    if is_ra:
        return float(degrees * 15.0)
    return float(sign * degrees)


def _coerce_coordinate_deg(value: object, *, is_ra: bool) -> float | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            from astropy import units as u
            from astropy.coordinates import Angle

            unit = u.hourangle if is_ra else u.deg
            return float(Angle(text, unit=unit).deg)
        except Exception:
            return _safe_float(text)
    numeric = _safe_float(value)
    if numeric is None:
        return None
    if abs(numeric) > 360.0:
        return _packed_sexagesimal_to_deg(numeric, is_ra=is_ra)
    return numeric


def _reader_timing_metadata(reader: object) -> dict[str, object]:
    header = getattr(reader, "your_header", None)
    metadata: dict[str, object] = {}
    for obj in (reader, header):
        if obj is None:
            continue
        if "source_ra_deg" not in metadata:
            ra = _coerce_coordinate_deg(
                _first_present(
                    getattr(obj, "ra_deg", None),
                    getattr(obj, "src_raj", None),
                    getattr(obj, "RAJ", None),
                ),
                is_ra=True,
            )
            if ra is not None:
                metadata["source_ra_deg"] = ra
        if "source_dec_deg" not in metadata:
            dec = _coerce_coordinate_deg(
                _first_present(
                    getattr(obj, "dec_deg", None),
                    getattr(obj, "src_dej", None),
                    getattr(obj, "DECJ", None),
                ),
                is_ra=False,
            )
            if dec is not None:
                metadata["source_dec_deg"] = dec
        if "barycentric_header_flag" not in metadata:
            bary = _safe_bool_flag(getattr(obj, "barycentric", None))
            if bary is not None:
                metadata["barycentric_header_flag"] = bary
        if "pulsarcentric_header_flag" not in metadata:
            pulsar = _safe_bool_flag(getattr(obj, "pulsarcentric", None))
            if pulsar is not None:
                metadata["pulsarcentric_header_flag"] = pulsar
    if "source_ra_deg" in metadata and "source_dec_deg" in metadata:
        metadata["source_position_basis"] = "reader_header"
    return metadata


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


def _is_psrfits_fold_mode(path: Path) -> tuple[bool, str | None]:
    try:
        from astropy.io import fits
    except Exception:
        return False, None

    try:
        with fits.open(path, memmap=False) as hdul:
            obs_mode = str(hdul[0].header.get("OBS_MODE", "")).strip().upper()
    except Exception:
        return False, None

    return obs_mode in _PSRFITS_FOLD_MODES, obs_mode or None


def _find_psrfits_subint(hdul: object) -> object | None:
    return next(
        (
            hdu
            for hdu in hdul[1:]
            if str(hdu.header.get("EXTNAME", "")).strip().upper() == "SUBINT"
        ),
        None,
    )


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
            subint = _find_psrfits_subint(hdul)
            if subint is None:
                return False

            subint_header = subint.header
            subint_keywords_present = all(
                key in subint_header for key in ("TBIN", "NCHAN", "NPOL")
            )
            primary_looks_psrfits = (
                "PSRFITS" in fitstype or "STT_IMJD" in primary or "STT_SMJD" in primary
            )
            mode_supported = (
                (not obs_mode)
                or obs_mode.startswith("SEARCH")
                or obs_mode in _PSRFITS_FOLD_MODES
            )
            return mode_supported and (primary_looks_psrfits or subint_keywords_present)
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
            ra = None
            for candidate in (primary.get("RAJ"), primary.get("RA"), primary.get("OBJCTRA")):
                ra = _coerce_coordinate_deg(candidate, is_ra=True)
                if ra is not None:
                    break
            dec = None
            for candidate in (primary.get("DECJ"), primary.get("DEC"), primary.get("OBJCTDEC")):
                dec = _coerce_coordinate_deg(candidate, is_ra=False)
                if dec is not None:
                    break
            if ra is not None and dec is not None:
                out["source_ra_deg"] = float(ra)
                out["source_dec_deg"] = float(dec)
                out["source_position_basis"] = "psrfits_primary_header"
            bary = _safe_bool_flag(_first_present(primary.get("BARYCENT"), primary.get("BARYCORR")))
            if bary is not None:
                out["barycentric_header_flag"] = bary
            pulsar = _safe_bool_flag(_first_present(primary.get("PULSAREN"), primary.get("PULSARC")))
            if pulsar is not None:
                out["pulsarcentric_header_flag"] = pulsar
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


def _folded_psrfits_dimensions(subint: object, path: Path) -> tuple[int, int, int]:
    header = subint.header
    missing: list[str] = []
    nbin = _safe_int(header.get("NBIN"))
    nchan = _safe_int(header.get("NCHAN"))
    npol = _safe_int(header.get("NPOL"))
    if nbin is None or nbin <= 0:
        missing.append("SUBINT.NBIN")
    if nchan is None or nchan <= 0:
        missing.append("SUBINT.NCHAN")
    if npol is None or npol <= 0:
        missing.append("SUBINT.NPOL")
    if missing:
        raise MetadataMissingError(
            f"Folded PSRFITS SUBINT header missing required dimensions: {', '.join(missing)}",
            path=path,
            fields=tuple(missing),
        )
    return int(nbin), int(nchan), int(npol)


def _psrparam_float(hdul: object, *names: str) -> float | None:
    wanted = {name.upper() for name in names}
    try:
        table = hdul["PSRPARAM"].data
    except Exception:
        return None
    for row in table:
        try:
            text = row["PARAM"]
        except Exception:
            continue
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        parts = str(text).strip().split()
        if len(parts) < 2 or parts[0].upper() not in wanted:
            continue
        value = _safe_float(parts[1])
        if value is not None:
            return value
    return None


def _folded_psrfits_period_sec(hdul: object, subint: object, path: Path) -> float:
    column_names = set(getattr(subint, "columns", ()).names or ())
    if "PERIOD" in column_names:
        for row in subint.data:
            period = _safe_float(row["PERIOD"])
            if period is not None and period > 0.0:
                return period

    p0 = _psrparam_float(hdul, "P0")
    if p0 is not None and p0 > 0.0:
        return p0

    f0 = _psrparam_float(hdul, "F0")
    if f0 is not None and f0 > 0.0:
        return 1.0 / f0

    nbin, _, _ = _folded_psrfits_dimensions(subint, path)
    tbin = _safe_float(subint.header.get("TBIN"))
    if tbin is not None and tbin > 0.0:
        return tbin * nbin

    raise MetadataMissingError(
        "Folded PSRFITS requires a positive PERIOD, P0, F0, or TBIN to define pseudo-time bins",
        path=path,
        fields=("SUBINT.PERIOD", "PSRPARAM.P0", "PSRPARAM.F0", "SUBINT.TBIN"),
    )


def _folded_psrfits_freqs_mhz(hdul: object, subint: object, path: Path) -> np.ndarray:
    _, nchan, _ = _folded_psrfits_dimensions(subint, path)
    column_names = set(getattr(subint, "columns", ()).names or ())
    if "DAT_FREQ" in column_names and len(subint.data) > 0:
        freqs = np.asarray(subint.data[0]["DAT_FREQ"], dtype=float).reshape(-1)
        if freqs.size == nchan and np.all(np.isfinite(freqs)):
            return freqs

    primary = hdul[0].header
    obsfreq = _safe_float(primary.get("OBSFREQ"))
    chan_bw = _safe_float(subint.header.get("CHAN_BW"))
    if chan_bw is None:
        obsbw = _safe_float(primary.get("OBSBW"))
        chan_bw = None if obsbw is None else obsbw / nchan
    if obsfreq is None or chan_bw is None or chan_bw == 0.0:
        raise MetadataMissingError(
            "Folded PSRFITS requires DAT_FREQ or OBSFREQ plus CHAN_BW/OBSBW",
            path=path,
            fields=("SUBINT.DAT_FREQ", "OBSFREQ", "SUBINT.CHAN_BW"),
        )
    first = obsfreq - chan_bw * ((nchan - 1) / 2.0)
    return first + chan_bw * np.arange(nchan, dtype=float)


def _folded_psrfits_scale(
    value: object,
    *,
    npol: int,
    nchan: int,
    default: float,
    path: Path,
    field_name: str,
) -> np.ndarray:
    if value is None:
        return np.full((npol, nchan), default, dtype=np.float32)
    arr = np.asarray(value, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return np.full((npol, nchan), default, dtype=np.float32)
    if arr.size == 1:
        return np.full((npol, nchan), float(arr[0]), dtype=np.float32)
    if arr.size == nchan:
        return np.broadcast_to(arr.reshape(1, nchan), (npol, nchan)).astype(np.float32, copy=False)
    if arr.size == npol * nchan:
        return arr.reshape(npol, nchan)
    raise CorruptedDataError(
        f"Folded PSRFITS {field_name} has {arr.size} values; expected 1, {nchan}, or {npol * nchan}",
        path=path,
    )


def _folded_psrfits_raw_data(row: object, *, nbin: int, nchan: int, npol: int, path: Path) -> np.ndarray:
    try:
        raw = np.asarray(row["DATA"], dtype=np.float32)
    except Exception as exc:
        raise MetadataMissingError(
            "Folded PSRFITS SUBINT row is missing DATA",
            path=path,
            fields=("SUBINT.DATA",),
        ) from exc

    expected = npol * nchan * nbin
    if raw.size != expected:
        raise CorruptedDataError(
            f"Folded PSRFITS DATA has {raw.size} values; expected {expected}",
            path=path,
        )
    if raw.shape == (npol, nchan, nbin):
        return raw
    if npol == 1 and raw.shape == (nchan, nbin):
        return raw.reshape(1, nchan, nbin)
    return raw.reshape(npol, nchan, nbin)


def _build_folded_stokes_i(raw: np.ndarray, polarization_order: str | None) -> tuple[np.ndarray, int]:
    normalized_order = _normalise_polarization_order(polarization_order)
    if raw.shape[0] == 1:
        return raw[0, :, :], 1
    if normalized_order == "IQUV":
        return raw[0, :, :], 2
    return raw[0, :, :] + raw[1, :, :], 2


def _folded_psrfits_waterfall(subint: object, path: Path) -> tuple[np.ndarray, int]:
    nbin, nchan, npol = _folded_psrfits_dimensions(subint, path)
    polarization_order = _normalise_polarization_order(str(subint.header.get("POL_TYPE", "")).strip())
    column_names = set(getattr(subint, "columns", ()).names or ())
    rows: list[np.ndarray] = []
    effective_npol = 1
    for row in subint.data:
        raw = _folded_psrfits_raw_data(row, nbin=nbin, nchan=nchan, npol=npol, path=path)
        scl = _folded_psrfits_scale(
            row["DAT_SCL"] if "DAT_SCL" in column_names else None,
            npol=npol,
            nchan=nchan,
            default=1.0,
            path=path,
            field_name="DAT_SCL",
        )
        offs = _folded_psrfits_scale(
            row["DAT_OFFS"] if "DAT_OFFS" in column_names else None,
            npol=npol,
            nchan=nchan,
            default=0.0,
            path=path,
            field_name="DAT_OFFS",
        )
        scaled = raw * scl[:, :, np.newaxis] + offs[:, :, np.newaxis]
        stokes_i, effective_npol = _build_folded_stokes_i(scaled, polarization_order)
        rows.append(stokes_i)

    if not rows:
        raise CorruptedDataError("Folded PSRFITS SUBINT table contains no rows", path=path)
    if len(rows) == 1:
        return rows[0].astype(np.float32, copy=False), effective_npol
    return np.mean(np.stack(rows, axis=0), axis=0).astype(np.float32, copy=False), effective_npol


def _inspect_folded_psrfits(path: Path) -> FilterbankInspection:
    try:
        from astropy.io import fits
    except Exception as exc:
        raise RuntimeError("The 'astropy' package is required for folded PSRFITS files.") from exc

    with fits.open(path, memmap=False) as hdul:
        subint = _find_psrfits_subint(hdul)
        if subint is None:
            raise FormatDetectionError("Folded PSRFITS file is missing a SUBINT table.", path=path)
        freqs_mhz = _folded_psrfits_freqs_mhz(hdul, subint, path)
        primary = hdul[0].header
        source_name = _decode_source_name(primary.get("SRC_NAME"))
        telescope_name = _decode_telescope_name(primary.get("TELESCOP"))

    timing_metadata = _psrfits_primary_fallback(path)
    detected_preset_key, detection_basis = detect_preset(
        None,
        None,
        telescope_name=telescope_name,
        schema_version="psrfits_fold",
        freq_lo_mhz=float(np.min(freqs_mhz)),
        freq_hi_mhz=float(np.max(freqs_mhz)),
    )
    return FilterbankInspection(
        source_path=path,
        source_name=source_name,
        telescope_id=None,
        machine_id=None,
        detected_preset_key=detected_preset_key,
        detection_basis=detection_basis,
        telescope_name=telescope_name,
        schema_version="psrfits_fold",
        freq_lo_mhz=float(np.min(freqs_mhz)),
        freq_hi_mhz=float(np.max(freqs_mhz)),
        source_ra_deg=_safe_float(timing_metadata.get("source_ra_deg")),
        source_dec_deg=_safe_float(timing_metadata.get("source_dec_deg")),
        source_position_basis=timing_metadata.get("source_position_basis"),  # type: ignore[arg-type]
        time_scale="utc",
        time_reference_frame="topocentric",
        barycentric_header_flag=_safe_bool_flag(timing_metadata.get("barycentric_header_flag")),
        pulsarcentric_header_flag=_safe_bool_flag(timing_metadata.get("pulsarcentric_header_flag")),
    )


def _load_folded_psrfits(
    path: Path,
    config: ObservationConfig,
    inspection: FilterbankInspection | None = None,
) -> tuple[np.ndarray, FilterbankMetadata]:
    try:
        from astropy.io import fits
    except Exception as exc:
        raise RuntimeError("The 'astropy' package is required for folded PSRFITS files.") from exc

    with fits.open(path, memmap=False) as hdul:
        subint = _find_psrfits_subint(hdul)
        if subint is None:
            raise FormatDetectionError("Folded PSRFITS file is missing a SUBINT table.", path=path)
        nbin, _, header_npol = _folded_psrfits_dimensions(subint, path)
        period_sec = _folded_psrfits_period_sec(hdul, subint, path)
        tsamp = period_sec / nbin
        freqs_mhz = _folded_psrfits_freqs_mhz(hdul, subint, path)
        polarization_order = _normalise_polarization_order(str(subint.header.get("POL_TYPE", "")).strip())
        chan_bw = _safe_float(subint.header.get("CHAN_BW"))
        stokes_i, effective_npol = _folded_psrfits_waterfall(subint, path)

    filterbank_inspection = inspection or _inspect_folded_psrfits(path)
    timing_metadata = _psrfits_primary_fallback(path)
    start_mjd = _safe_float(timing_metadata.get("tstart"))
    if start_mjd is None:
        raise MetadataMissingError(
            "Folded PSRFITS requires STT_IMJD and STT_SMJD to define start_mjd",
            path=path,
            fields=("STT_IMJD", "STT_SMJD"),
        )

    read_start_sec = config.read_start_for_file(path.name)
    nstart = min(max(int(read_start_sec / tsamp), 0), max(stokes_i.shape[1] - 1, 0))
    nread = stokes_i.shape[1] - nstart
    if config.read_end_sec is not None:
        nend = max(nstart + 1, int(config.read_end_sec / tsamp))
        nread = min(nread, nend - nstart)
    stokes_i = stokes_i[:, nstart : nstart + nread]

    effective_npol = (
        max(1, int(config.npol_override)) if config.npol_override is not None else effective_npol
    )
    if abs(float(config.dm)) > 0.0:
        stokes_i = dedisperse(stokes_i, config.dm, freqs_mhz, tsamp)

    tail_fraction = float(np.clip(config.normalization_tail_fraction, 0.05, 0.95))
    offpulse_start = min(stokes_i.shape[1] - 1, int((1 - tail_fraction) * stokes_i.shape[1]))
    offpulse = stokes_i[:, offpulse_start:]
    stokes_i = normalize(stokes_i, offpulse).astype(np.float32, copy=False)

    diffs = np.diff(freqs_mhz.astype(float))
    freqres = (
        float(abs(chan_bw))
        if chan_bw is not None and np.isfinite(chan_bw) and chan_bw != 0.0
        else (float(abs(np.median(diffs))) if diffs.size else 0.0)
    )
    bandwidth_mhz = freqres * int(freqs_mhz.size)
    sefd_jy = config.sefd_jy
    if sefd_jy is None:
        sefd_jy = resolve_default_sefd_jy(
            config.preset_key,
            float(np.min(freqs_mhz)),
            float(np.max(freqs_mhz)),
        )

    metadata = FilterbankMetadata(
        source_path=path,
        source_name=filterbank_inspection.source_name,
        tsamp=tsamp,
        freqres=freqres,
        start_mjd=start_mjd,
        read_start_sec=read_start_sec,
        sefd_jy=sefd_jy,
        bandwidth_mhz=bandwidth_mhz,
        npol=effective_npol,
        freqs_mhz=freqs_mhz,
        header_npol=header_npol,
        polarization_order=polarization_order,
        telescope_id=filterbank_inspection.telescope_id,
        machine_id=filterbank_inspection.machine_id,
        detected_preset_key=filterbank_inspection.detected_preset_key,
        detection_basis=filterbank_inspection.detection_basis,
        source_ra_deg=filterbank_inspection.source_ra_deg,
        source_dec_deg=filterbank_inspection.source_dec_deg,
        source_position_basis=filterbank_inspection.source_position_basis,
        time_scale=filterbank_inspection.time_scale or "utc",
        time_reference_frame=filterbank_inspection.time_reference_frame or "topocentric",
        barycentric_header_flag=filterbank_inspection.barycentric_header_flag,
        pulsarcentric_header_flag=filterbank_inspection.pulsarcentric_header_flag,
        dedispersion_reference_frequency_mhz=(
            float(np.max(freqs_mhz)) if abs(float(config.dm)) > 0.0 else None
        ),
        dedispersion_reference_basis=(
            "flits_integer_bin_dedispersion_max_frequency" if abs(float(config.dm)) > 0.0 else None
        ),
    )
    return stokes_i, validate_metadata(metadata)



