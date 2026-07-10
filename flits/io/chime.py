from __future__ import annotations

import datetime
import re
from pathlib import Path

import numpy as np

from flits.io.errors import CorruptedDataError, MetadataMissingError
from flits.signal import normalize

try:
    import h5py as _h5py
except Exception:  # pragma: no cover - depends on optional runtime stack
    _h5py = None  # type: ignore[assignment]



_HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"

_WATERFALL_PATHS: tuple[str, ...] = (
    "/wfall",
    "/frb/wfall",
    "/intensity",
    "/data",
)

_CHIME_CATALOG_SCHEMA = "chime_frb_catalog_v1"
_CHIME_BBDATA_BEAMFORMED_SCHEMA = "chime_bbdata_beamformed_v1"
_BBDATA_MEMH5_SUBCLASS = "baseband_analysis.core.bbdata.BBData"

_SUPPORTED_SCHEMAS: frozenset[str] = frozenset(
    {
        "flits_chime_v1",
        "chime_frb_v1",
        "chime_frb_intensity_v1",
        _CHIME_CATALOG_SCHEMA,
        _CHIME_BBDATA_BEAMFORMED_SCHEMA,
    }
)

_UTC = datetime.timezone.utc
_MJD_EPOCH_DATE = datetime.date(1858, 11, 17)
_MJD_EPOCH = datetime.datetime(1858, 11, 17, tzinfo=_UTC)
_DM_CONSTANT = 1 / (2.41 * 10**-4)


def _safe_lookup(root: "_h5py.Group", candidate: str) -> object | None:
    try:
        return root[candidate]
    except (KeyError, ValueError):
        return None


def _is_chime_catalog(root: "_h5py.Group") -> bool:
    """Detect CHIME/FRB public catalog layout (https://www.chime-frb.ca/catalog)."""
    if "frb" not in root:
        return False
    frb = root["frb"]
    if not isinstance(frb, _h5py.Group):  # type: ignore[arg-type]
        return False
    has_extent = "extent" in frb and isinstance(frb["extent"], _h5py.Dataset)  # type: ignore[arg-type]
    has_plot_freq = "plot_freq" in frb and isinstance(frb["plot_freq"], _h5py.Dataset)  # type: ignore[arg-type]
    has_wfall = any(name in frb for name in ("calibrated_wfall", "wfall"))
    return has_extent and has_plot_freq and has_wfall


def _is_bbdata_beamformed(root: "_h5py.Group") -> bool:
    subclass = _decode_attr(root.attrs.get("__memh5_subclass"))
    if subclass != _BBDATA_MEMH5_SUBCLASS:
        return False
    if "delta_time" not in root.attrs:
        return False

    power = _safe_lookup(root, "tiedbeam_power")
    freq = _safe_lookup(root, "index_map/freq")
    time0 = _safe_lookup(root, "time0")
    return (
        isinstance(power, _h5py.Dataset)  # type: ignore[arg-type]
        and power.ndim == 3
        and isinstance(freq, _h5py.Dataset)  # type: ignore[arg-type]
        and isinstance(time0, _h5py.Dataset)  # type: ignore[arg-type]
    )


def _looks_like_chime_intensity(root: "_h5py.Group") -> bool:
    schema = _read_attr(root, "schema_version", "format_version")
    if schema is not None:
        return True

    try:
        dataset = _resolve_waterfall_dataset(root)
    except MetadataMissingError:
        return False

    metadata_hint = _read_attr(
        root,
        "tsamp_s",
        "t_sample",
        "dt",
        "fch1_mhz",
        "fch1",
        "foff_mhz",
        "foff",
        "tstart_mjd",
        "mjd_start",
        "tstart",
        "telescope_name",
        "telescope",
        "TELESCOP",
    )
    return dataset.ndim == 2 and metadata_hint is not None


def _parse_tns_mjd(tns_name: object) -> float | None:
    """Parse a TNS name like 'FRB20180729A' → MJD at 00:00 UT of that date."""
    if tns_name is None:
        return None
    match = re.search(r"(\d{8})", str(tns_name))
    if not match:
        return None
    digits = match.group(1)
    try:
        date = datetime.date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None
    return float((date - _MJD_EPOCH_DATE).days)


def _parse_event_mjd(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("T ", "T")
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_UTC)
    else:
        parsed = parsed.astimezone(_UTC)
    return float((parsed - _MJD_EPOCH).total_seconds() / 86400.0)


def _unix_to_mjd(timestamp_sec: float) -> float:
    return float((float(timestamp_sec) - _MJD_EPOCH.timestamp()) / 86400.0)


def _decode_attr(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray) and value.shape == ():
        return _decode_attr(value.item())
    return value


def _read_attr(group: "_h5py.Group", *names: str) -> object | None:
    """Return the first matching attr found across the given candidate names."""
    search_groups: list[object] = [group]
    frb = group.get("frb") if "frb" in group else None
    if frb is not None and isinstance(frb, _h5py.Group):  # type: ignore[arg-type]
        search_groups.append(frb)

    for candidate in names:
        for scope in search_groups:
            if candidate in scope.attrs:
                return _decode_attr(scope.attrs[candidate])
    return None


def _read_source_position_attrs(root: "_h5py.Group", path: Path) -> tuple[float | None, float | None, str | None]:
    ra = _coerce_optional_float(
        _read_attr(root, "source_ra_deg", "ra_deg", "ra", "RA"),
        field="source_ra_deg",
        path=path,
    )
    dec = _coerce_optional_float(
        _read_attr(root, "source_dec_deg", "dec_deg", "dec", "DEC"),
        field="source_dec_deg",
        path=path,
    )
    basis = "hdf5_attrs" if ra is not None and dec is not None else None
    return ra, dec, basis


def _read_bbdata_source_position(root: "_h5py.Group", path: Path) -> tuple[float | None, float | None, str | None]:
    locations = _safe_lookup(root, "tiedbeam_locations")
    if not isinstance(locations, _h5py.Dataset):  # type: ignore[arg-type]
        return None, None, None
    values = locations[:]
    fields = getattr(values.dtype, "fields", None) or {}
    if "ra" not in fields or "dec" not in fields or values.size == 0:
        return None, None, None
    try:
        ra_values = np.asarray(values["ra"], dtype=float)
        dec_values = np.asarray(values["dec"], dtype=float)
    except Exception as exc:
        raise CorruptedDataError("Unable to parse tiedbeam_locations ra/dec.", path=path) from exc
    if not (np.isfinite(ra_values).all() and np.isfinite(dec_values).all()):
        return None, None, None
    if not (np.allclose(ra_values, float(ra_values[0])) and np.allclose(dec_values, float(dec_values[0]))):
        return None, None, None
    return float(ra_values[0]), float(dec_values[0]), "bbdata_tiedbeam_locations"


def _resolve_waterfall_dataset(root: "_h5py.Group") -> "_h5py.Dataset":
    for candidate in _WATERFALL_PATHS:
        obj = _safe_lookup(root, candidate)
        if isinstance(obj, _h5py.Dataset):  # type: ignore[arg-type]
            return obj
    raise MetadataMissingError(
        "No waterfall dataset found at any of: " + ", ".join(_WATERFALL_PATHS),
        fields=tuple(_WATERFALL_PATHS),
    )


def _coerce_float(value: object, *, field: str, path: Path) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CorruptedDataError(
            f"Attribute {field!r} is not a float: {value!r}", path=path
        ) from exc
    return out


def _coerce_int(value: object, *, field: str, path: Path) -> int:
    try:
        out = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CorruptedDataError(
            f"Attribute {field!r} is not an int: {value!r}", path=path
        ) from exc
    return out


def _coerce_optional_float(value: object | None, *, field: str, path: Path) -> float | None:
    if value is None:
        return None
    return _coerce_float(value, field=field, path=path)


def _coerce_optional_int(value: object | None, *, field: str, path: Path) -> int | None:
    if value is None:
        return None
    return _coerce_int(value, field=field, path=path)


def _guess_source_name(path: Path) -> str | None:
    match = re.search(r"(FRB\d{8}[A-Z]?)", path.stem, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return path.stem or None


def _read_bbdata_freqs(root: "_h5py.Group", path: Path) -> np.ndarray:
    freq_obj = _safe_lookup(root, "index_map/freq")
    if not isinstance(freq_obj, _h5py.Dataset):  # type: ignore[arg-type]
        raise MetadataMissingError(
            "Beamformed BBData is missing index_map/freq.",
            path=path,
            fields=("index_map/freq",),
        )

    dtype_fields = getattr(freq_obj.dtype, "fields", None) or {}
    if "centre" not in dtype_fields:
        raise CorruptedDataError(
            "Beamformed BBData index_map/freq is missing a 'centre' field.",
            path=path,
        )

    freqs = np.asarray(freq_obj["centre"], dtype=float)
    if freqs.ndim != 1 or freqs.size < 2:
        raise CorruptedDataError(
            f"Beamformed BBData frequency axis must be 1-D with at least 2 channels; got {freqs.shape}.",
            path=path,
        )
    if not np.all(np.isfinite(freqs)):
        raise CorruptedDataError("Beamformed BBData frequency axis contains non-finite values.", path=path)
    return freqs


def _read_bbdata_channel_starts(root: "_h5py.Group", path: Path) -> np.ndarray:
    time0_obj = _safe_lookup(root, "time0")
    if not isinstance(time0_obj, _h5py.Dataset):  # type: ignore[arg-type]
        raise MetadataMissingError(
            "Beamformed BBData is missing time0.",
            path=path,
            fields=("time0",),
        )

    dtype_fields = getattr(time0_obj.dtype, "fields", None) or {}
    if "ctime" not in dtype_fields or "ctime_offset" not in dtype_fields:
        raise CorruptedDataError(
            "Beamformed BBData time0 must expose 'ctime' and 'ctime_offset' fields.",
            path=path,
        )

    starts = np.asarray(time0_obj["ctime"], dtype=float) + np.asarray(time0_obj["ctime_offset"], dtype=float)
    if starts.ndim != 1:
        raise CorruptedDataError(f"Beamformed BBData time0 must be 1-D; got {starts.shape}.", path=path)
    if not np.all(np.isfinite(starts)):
        raise CorruptedDataError("Beamformed BBData time0 contains non-finite values.", path=path)
    return starts


def _read_bbdata_coherent_dm(
    root: "_h5py.Group",
    path: Path,
    *,
    required: bool,
) -> float | None:
    power = _safe_lookup(root, "tiedbeam_power")
    if not isinstance(power, _h5py.Dataset):  # type: ignore[arg-type]
        if required:
            raise MetadataMissingError(
                "Beamformed BBData is missing tiedbeam_power.",
                path=path,
                fields=("tiedbeam_power",),
            )
        return None

    raw_value = _decode_attr(power.attrs.get("DM_coherent"))
    if raw_value is None:
        if required:
            raise MetadataMissingError(
                "Beamformed BBData tiedbeam_power is missing DM_coherent.",
                path=path,
                fields=("tiedbeam_power.attrs.DM_coherent",),
            )
        return None
    return _coerce_float(raw_value, field="DM_coherent", path=path)


def _estimate_freqres(freqs_mhz: np.ndarray, path: Path) -> float:
    diffs = np.abs(np.diff(np.asarray(freqs_mhz, dtype=float)))
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        raise CorruptedDataError("Unable to estimate a positive frequency resolution.", path=path)
    return float(np.median(diffs))


def _shift_1d_with_nan(values: np.ndarray, shift_bins: int) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=np.float32)
    if values.size == 0:
        return out
    if shift_bins == 0:
        out[:] = values
        return out
    if abs(shift_bins) >= values.size:
        return out
    if shift_bins > 0:
        out[shift_bins:] = values[:-shift_bins]
    else:
        out[:shift_bins] = values[-shift_bins:]
    return out


def _shift_rows_with_nan(values: np.ndarray, shift_bins: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=np.float32)
    for idx, current_shift in enumerate(np.asarray(shift_bins, dtype=int)):
        out[idx, :] = _shift_1d_with_nan(np.asarray(values[idx, :], dtype=np.float32), int(current_shift))
    return out


def _dm_delay_seconds(dm: float, freqs_mhz: np.ndarray) -> np.ndarray:
    freqs = np.asarray(freqs_mhz, dtype=float)
    freq_ref = float(np.max(freqs))
    return _DM_CONSTANT * float(dm) * (freqs**-2.0 - freq_ref**-2.0)


def _normalize_waterfall(stokes_i: np.ndarray, tail_fraction: float) -> np.ndarray:
    tail_fraction = float(np.clip(tail_fraction, 0.05, 0.95))
    offpulse_start = min(stokes_i.shape[1] - 1, int((1 - tail_fraction) * stokes_i.shape[1]))
    offpulse = stokes_i[:, offpulse_start:]
    return normalize(stokes_i, offpulse).astype(np.float32, copy=False)



