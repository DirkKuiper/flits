from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import ClassVar

import numpy as np

from flits.io.errors import (
    CorruptedDataError,
    MetadataMissingError,
    UnsupportedSchemaError,
)
from flits.io.reader import FilterbankInspection
from flits.io.validation import require_fields, validate_metadata
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig, detect_preset, resolve_default_sefd_jy
from flits.signal import dedisperse, normalize

try:
    import h5py as _h5py

    _H5PY_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on optional runtime stack
    _h5py = None  # type: ignore[assignment]
    _H5PY_IMPORT_ERROR = exc


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


class ChimeHdf5Reader:
    """Reader for CHIME/FRB intensity HDF5 and beamformed BBData files.

    Supported variants:

    - ``flits_chime_v1`` style waterfall HDF5 with ``/wfall``-like datasets.
    - Public CHIME/FRB catalog HDF5 under ``/frb``.
    - Beamformed ``BBData`` containers carrying ``tiedbeam_power`` plus
      ``index_map/freq`` and ``time0`` metadata. Only the power product is
      supported in this iteration; complex-voltage ``tiedbeam_baseband`` is not.
    """

    format_id: ClassVar[str] = "chime_hdf5"
    extensions: ClassVar[tuple[str, ...]] = (".h5", ".hdf5")

    def __init__(self) -> None:
        if _h5py is None:
            raise RuntimeError(
                "h5py is required for ChimeHdf5Reader but is not installed."
            ) from _H5PY_IMPORT_ERROR

    def sniff(self, path: Path) -> bool:
        try:
            with open(path, "rb") as handle:
                if not handle.read(8).startswith(_HDF5_MAGIC):
                    return False
        except OSError:
            return False

        try:
            with self._open(path) as fh:
                root = fh["/"]
                if _is_bbdata_beamformed(root) or _is_chime_catalog(root):
                    return True
                if _read_attr(root, "schema_version", "format_version") is not None:
                    return True
                return _looks_like_chime_intensity(root)
        except CorruptedDataError:
            return True
        except Exception:
            return False

    def _open(self, path: Path) -> "_h5py.File":
        try:
            return _h5py.File(path, mode="r")
        except (OSError, ValueError) as exc:
            raise CorruptedDataError(
                f"Failed to open HDF5 file: {exc}", path=path
            ) from exc

    def _check_schema(self, root: "_h5py.Group", path: Path) -> str:
        if _is_bbdata_beamformed(root):
            return _CHIME_BBDATA_BEAMFORMED_SCHEMA

        schema = _read_attr(root, "schema_version", "format_version")
        if schema is None:
            if _is_chime_catalog(root):
                return _CHIME_CATALOG_SCHEMA
            if _looks_like_chime_intensity(root):
                return "unknown"
            raise UnsupportedSchemaError(
                "HDF5 layout is not recognized as supported CHIME/FRB intensity, catalog, or beamformed BBData.",
                path=path,
            )

        schema_str = str(schema).strip().lower()
        if schema_str not in _SUPPORTED_SCHEMAS:
            raise UnsupportedSchemaError(
                f"HDF5 schema {schema!r} is not recognized. Supported: {sorted(_SUPPORTED_SCHEMAS)}",
                path=path,
                detected_schema=schema_str,
            )
        return schema_str

    def _read_inspection(self, path: Path) -> tuple[FilterbankInspection, dict[str, object]]:
        with self._open(path) as fh:
            root = fh["/"]
            schema = self._check_schema(root, path)

            source_name = _read_attr(root, "source_name", "src_name", "SRC_NAME", "tns_name")
            if source_name is None and schema in {_CHIME_CATALOG_SCHEMA, _CHIME_BBDATA_BEAMFORMED_SCHEMA}:
                source_name = _guess_source_name(path)

            telescope_id = _coerce_optional_int(
                _read_attr(root, "telescope_id"),
                field="telescope_id",
                path=path,
            )
            machine_id = _coerce_optional_int(
                _read_attr(root, "machine_id"),
                field="machine_id",
                path=path,
            )
            telescope_name = _read_attr(root, "telescope_name", "telescope", "TELESCOP")
            if telescope_name is None and schema in {
                _CHIME_CATALOG_SCHEMA,
                _CHIME_BBDATA_BEAMFORMED_SCHEMA,
            }:
                telescope_name = "CHIME/FRB"

            coherent_dm = None
            if schema == _CHIME_BBDATA_BEAMFORMED_SCHEMA:
                coherent_dm = _read_bbdata_coherent_dm(root, path, required=False)

            source_ra_deg, source_dec_deg, source_position_basis = _read_source_position_attrs(root, path)
            if schema == _CHIME_BBDATA_BEAMFORMED_SCHEMA:
                bb_ra, bb_dec, bb_basis = _read_bbdata_source_position(root, path)
                if bb_ra is not None and bb_dec is not None:
                    source_ra_deg, source_dec_deg, source_position_basis = bb_ra, bb_dec, bb_basis

            freq_lo, freq_hi = self._peek_freq_range(root, schema, path)

            attrs: dict[str, object] = {
                "schema": schema,
                "source_name": str(source_name) if source_name is not None else None,
                "telescope_id": telescope_id,
                "machine_id": machine_id,
                "telescope_name": str(telescope_name) if telescope_name is not None else None,
                "freq_lo_mhz": freq_lo,
                "freq_hi_mhz": freq_hi,
                "coherent_dm": coherent_dm,
                "source_ra_deg": source_ra_deg,
                "source_dec_deg": source_dec_deg,
                "source_position_basis": source_position_basis,
            }

        schema_hint = schema if schema and schema != "unknown" else None
        detected_preset_key, detection_basis = detect_preset(
            attrs["telescope_id"],  # type: ignore[arg-type]
            attrs["machine_id"],  # type: ignore[arg-type]
            telescope_name=attrs["telescope_name"],  # type: ignore[arg-type]
            schema_version=schema_hint,
            freq_lo_mhz=freq_lo,
            freq_hi_mhz=freq_hi,
        )

        inspection = FilterbankInspection(
            source_path=path,
            source_name=attrs["source_name"],  # type: ignore[arg-type]
            telescope_id=attrs["telescope_id"],  # type: ignore[arg-type]
            machine_id=attrs["machine_id"],  # type: ignore[arg-type]
            detected_preset_key=detected_preset_key,
            detection_basis=detection_basis,
            telescope_name=attrs["telescope_name"],  # type: ignore[arg-type]
            schema_version=schema_hint,
            freq_lo_mhz=freq_lo,
            freq_hi_mhz=freq_hi,
            coherent_dm=attrs["coherent_dm"],  # type: ignore[arg-type]
            source_ra_deg=attrs["source_ra_deg"],  # type: ignore[arg-type]
            source_dec_deg=attrs["source_dec_deg"],  # type: ignore[arg-type]
            source_position_basis=attrs["source_position_basis"],  # type: ignore[arg-type]
            time_scale="utc",
            time_reference_frame="topocentric",
        )
        return inspection, attrs

    @staticmethod
    def _peek_freq_range(
        root: "_h5py.Group",
        schema: str,
        path: Path,
    ) -> tuple[float | None, float | None]:
        if schema == _CHIME_BBDATA_BEAMFORMED_SCHEMA:
            freqs = _read_bbdata_freqs(root, path)
            return (float(np.min(freqs)), float(np.max(freqs)))

        if schema == _CHIME_CATALOG_SCHEMA and "frb" in root:
            frb = root["frb"]
            if isinstance(frb, _h5py.Group) and "plot_freq" in frb:  # type: ignore[arg-type]
                pf = frb["plot_freq"]
                if isinstance(pf, _h5py.Dataset) and pf.shape and pf.shape[0] >= 1:  # type: ignore[arg-type]
                    lo = float(pf[0])
                    hi = float(pf[-1])
                    return (min(lo, hi), max(lo, hi))

        fch1_attr = _read_attr(root, "fch1_mhz", "fch1")
        foff_attr = _read_attr(root, "foff_mhz", "foff")
        nchan_attr = _read_attr(root, "nchan", "nchans")
        if fch1_attr is None or foff_attr is None or nchan_attr is None:
            return (None, None)
        try:
            fch1 = float(fch1_attr)  # type: ignore[arg-type]
            foff = float(foff_attr)  # type: ignore[arg-type]
            nchan = int(nchan_attr)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return (None, None)
        if nchan <= 0:
            return (None, None)
        lo = fch1
        hi = fch1 + foff * (nchan - 1)
        return (min(lo, hi), max(lo, hi))

    def inspect(self, path: Path) -> FilterbankInspection:
        resolved = Path(path).expanduser().resolve()
        inspection, _ = self._read_inspection(resolved)
        return inspection

    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        resolved = Path(path).expanduser().resolve()
        with self._open(resolved) as fh:
            root = fh["/"]
            schema = self._check_schema(root, resolved)
            if schema == _CHIME_CATALOG_SCHEMA:
                return self._load_catalog(root, resolved, config, inspection)
            if schema == _CHIME_BBDATA_BEAMFORMED_SCHEMA:
                return self._load_bbdata_beamformed(root, resolved, config, inspection)
            return self._load_flits_v1(root, resolved, config, inspection)

    def _load_flits_v1(
        self,
        root: "_h5py.Group",
        resolved: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        missing: list[str] = []
        tsamp_attr = _read_attr(root, "tsamp_s", "t_sample", "dt")
        fch1_attr = _read_attr(root, "fch1_mhz", "fch1")
        foff_attr = _read_attr(root, "foff_mhz", "foff")
        mjd_attr = _read_attr(root, "tstart_mjd", "mjd_start", "tstart")

        if tsamp_attr is None:
            missing.append("tsamp_s")
        if fch1_attr is None:
            missing.append("fch1_mhz")
        if foff_attr is None:
            missing.append("foff_mhz")
        if mjd_attr is None:
            missing.append("tstart_mjd")
        require_fields(resolved, tuple(missing), context="CHIME HDF5 root attributes")

        tsamp = _coerce_float(tsamp_attr, field="tsamp_s", path=resolved)
        fch1 = _coerce_float(fch1_attr, field="fch1_mhz", path=resolved)
        foff = _coerce_float(foff_attr, field="foff_mhz", path=resolved)
        start_mjd = _coerce_float(mjd_attr, field="tstart_mjd", path=resolved)

        header_npol_attr = _read_attr(root, "npol", "nifs")
        header_npol = (
            max(1, _coerce_int(header_npol_attr, field="npol", path=resolved))
            if header_npol_attr is not None
            else 1
        )

        dataset = _resolve_waterfall_dataset(root)
        if dataset.ndim != 2:
            raise CorruptedDataError(
                f"Waterfall dataset has {dataset.ndim} dimensions; expected 2.",
                path=resolved,
            )
        nchan, ntime = dataset.shape
        nchan = int(nchan)
        ntime = int(ntime)

        nchan_attr = _read_attr(root, "nchan", "nchans")
        if nchan_attr is not None:
            nchan_declared = _coerce_int(nchan_attr, field="nchan", path=resolved)
            if nchan_declared != nchan:
                raise CorruptedDataError(
                    f"Dataset nchan={nchan} disagrees with declared nchan={nchan_declared}",
                    path=resolved,
                )

        freqs_mhz = fch1 + foff * np.arange(nchan, dtype=float)
        freq_lo = float(np.min(freqs_mhz))
        freq_hi = float(np.max(freqs_mhz))
        sefd_attr = _read_attr(root, "sefd_jy")
        sefd_jy = config.sefd_jy
        if sefd_jy is None:
            sefd_jy = (
                _coerce_float(sefd_attr, field="sefd_jy", path=resolved)
                if sefd_attr is not None
                else None
            )
        if sefd_jy is None:
            sefd_jy = resolve_default_sefd_jy(config.preset_key, freq_lo, freq_hi)

        read_start_sec = config.read_start_for_file(resolved.name)
        nstart = min(max(int(read_start_sec / tsamp), 0), max(ntime - 1, 0))
        nread = max(1, ntime - nstart)
        if config.read_end_sec is not None:
            nend = max(nstart + 1, int(config.read_end_sec / tsamp))
            nread = min(nread, nend - nstart)

        stokes_i = np.asarray(dataset[:, nstart : nstart + nread], dtype=np.float32)

        if inspection is None:
            filterbank_inspection, _ = self._read_inspection(resolved)
        else:
            filterbank_inspection = inspection

        if stokes_i.shape[0] != nchan:
            raise CorruptedDataError(
                f"Read channel count {stokes_i.shape[0]} does not match nchan={nchan}",
                path=resolved,
            )

        effective_npol = (
            max(1, int(config.npol_override)) if config.npol_override is not None else header_npol
        )

        stokes_i = dedisperse(stokes_i, config.dm, freqs_mhz, tsamp)
        stokes_i = _normalize_waterfall(stokes_i, config.normalization_tail_fraction)

        metadata = FilterbankMetadata(
            source_path=resolved,
            source_name=filterbank_inspection.source_name,
            tsamp=tsamp,
            freqres=float(abs(foff)),
            start_mjd=start_mjd,
            read_start_sec=read_start_sec,
            sefd_jy=sefd_jy,
            bandwidth_mhz=float(abs(foff) * nchan),
            npol=effective_npol,
            freqs_mhz=freqs_mhz,
            header_npol=header_npol,
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

    def _load_catalog(
        self,
        root: "_h5py.Group",
        resolved: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        frb = root["frb"]
        if not isinstance(frb, _h5py.Group):  # type: ignore[arg-type]
            raise CorruptedDataError(
                "CHIME catalog layout expected /frb to be a Group.",
                path=resolved,
            )

        if "calibrated_wfall" in frb:
            dataset = frb["calibrated_wfall"]
        elif "wfall" in frb:
            dataset = frb["wfall"]
        else:
            raise MetadataMissingError(
                "CHIME catalog file has neither /frb/calibrated_wfall nor /frb/wfall.",
                path=resolved,
                fields=("calibrated_wfall", "wfall"),
            )
        if not isinstance(dataset, _h5py.Dataset) or dataset.ndim != 2:  # type: ignore[arg-type]
            raise CorruptedDataError(
                f"CHIME catalog waterfall must be a 2-D dataset; got {dataset}.",
                path=resolved,
            )
        nchan, ntime = dataset.shape
        nchan = int(nchan)
        ntime = int(ntime)

        extent = np.asarray(frb["extent"][:])
        if extent.shape != (4,):
            raise CorruptedDataError(
                f"/frb/extent must have shape (4,); got {extent.shape}.",
                path=resolved,
            )
        plot_freq = np.asarray(frb["plot_freq"][:], dtype=float)
        if plot_freq.shape != (nchan,):
            raise CorruptedDataError(
                f"/frb/plot_freq length {plot_freq.shape[0]} does not match nchan={nchan}.",
                path=resolved,
            )

        time_span = float(extent[1] - extent[0])
        if time_span <= 0 or ntime <= 0:
            raise CorruptedDataError(
                f"Non-positive time span in /frb/extent: {extent[:2]} with ntime={ntime}.",
                path=resolved,
            )
        tsamp = time_span / ntime
        freqres = _estimate_freqres(plot_freq, resolved)

        tns_name = _read_attr(root, "tns_name")
        start_mjd = _parse_tns_mjd(tns_name)
        if start_mjd is None:
            raise MetadataMissingError(
                "CHIME catalog file lacks a parseable tns_name for tstart_mjd. Expected an attribute like 'FRB20180729A' under /frb.",
                path=resolved,
                fields=("tns_name",),
            )

        freq_lo = float(np.min(plot_freq))
        freq_hi = float(np.max(plot_freq))
        sefd_jy = config.sefd_jy
        if sefd_jy is None:
            sefd_jy = resolve_default_sefd_jy(config.preset_key, freq_lo, freq_hi)

        read_start_sec = config.read_start_for_file(resolved.name)
        nstart = min(max(int(read_start_sec / tsamp), 0), max(ntime - 1, 0))
        nread = max(1, ntime - nstart)
        if config.read_end_sec is not None:
            nend = max(nstart + 1, int(config.read_end_sec / tsamp))
            nread = min(nread, nend - nstart)

        stokes_i = np.asarray(dataset[:, nstart : nstart + nread], dtype=np.float32)
        non_finite = ~np.isfinite(stokes_i)
        if non_finite.any():
            stokes_i[non_finite] = 0.0

        if inspection is None:
            filterbank_inspection, _ = self._read_inspection(resolved)
        else:
            filterbank_inspection = inspection

        header_npol = 1
        effective_npol = (
            max(1, int(config.npol_override)) if config.npol_override is not None else header_npol
        )

        stokes_i = _normalize_waterfall(stokes_i, config.normalization_tail_fraction)

        metadata = FilterbankMetadata(
            source_path=resolved,
            source_name=filterbank_inspection.source_name,
            tsamp=tsamp,
            freqres=freqres,
            start_mjd=start_mjd,
            read_start_sec=read_start_sec,
            sefd_jy=sefd_jy,
            bandwidth_mhz=float(abs(plot_freq[-1] - plot_freq[0])) + freqres,
            npol=effective_npol,
            freqs_mhz=plot_freq,
            header_npol=header_npol,
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
            dedispersion_reference_frequency_mhz=None,
            dedispersion_reference_basis="unknown_public_chime_catalog_reference",
        )
        return stokes_i, validate_metadata(metadata)

    def _load_bbdata_beamformed(
        self,
        root: "_h5py.Group",
        resolved: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        power = _safe_lookup(root, "tiedbeam_power")
        if not isinstance(power, _h5py.Dataset):  # type: ignore[arg-type]
            raise MetadataMissingError(
                "Beamformed BBData is missing tiedbeam_power.",
                path=resolved,
                fields=("tiedbeam_power",),
            )
        if power.ndim != 3:
            raise CorruptedDataError(
                f"Beamformed BBData tiedbeam_power must be 3-D (freq, beam/pol, time); got {power.shape}.",
                path=resolved,
            )

        tsamp_attr = _decode_attr(root.attrs.get("delta_time"))
        if tsamp_attr is None:
            raise MetadataMissingError(
                "Beamformed BBData is missing delta_time.",
                path=resolved,
                fields=("delta_time",),
            )
        tsamp = _coerce_float(tsamp_attr, field="delta_time", path=resolved)

        freqs_mhz = _read_bbdata_freqs(root, resolved)
        channel_start_sec = _read_bbdata_channel_starts(root, resolved)
        coherent_dm = _read_bbdata_coherent_dm(root, resolved, required=True)

        nchan, nbeam, ntime = (int(dim) for dim in power.shape)
        if freqs_mhz.shape[0] != nchan:
            raise CorruptedDataError(
                f"Beamformed BBData frequency table length {freqs_mhz.shape[0]} does not match tiedbeam_power nchan={nchan}.",
                path=resolved,
            )
        if channel_start_sec.shape[0] != nchan:
            raise CorruptedDataError(
                f"Beamformed BBData time0 length {channel_start_sec.shape[0]} does not match tiedbeam_power nchan={nchan}.",
                path=resolved,
            )

        header_npol = 1
        if nbeam == 1:
            raw = np.asarray(power[:, 0, :], dtype=np.float32)
        else:
            locations = _safe_lookup(root, "tiedbeam_locations")
            if not isinstance(locations, _h5py.Dataset):  # type: ignore[arg-type]
                raise CorruptedDataError(
                    "Beamformed BBData has multiple beam-axis entries but no tiedbeam_locations metadata to prove they are polarizations.",
                    path=resolved,
                )
            if locations.shape != (nbeam,):
                raise CorruptedDataError(
                    f"Beamformed BBData tiedbeam_locations shape {locations.shape} does not match beam axis length {nbeam}.",
                    path=resolved,
                )

            loc_values = locations[:]
            loc_fields = getattr(loc_values.dtype, "fields", None) or {}
            required_fields = {"ra", "dec", "x_400MHz", "y_400MHz", "pol"}
            if not required_fields.issubset(loc_fields):
                raise CorruptedDataError(
                    "Beamformed BBData tiedbeam_locations must expose ra/dec/x_400MHz/y_400MHz/pol fields.",
                    path=resolved,
                )

            if not (
                np.allclose(np.asarray(loc_values["ra"], dtype=float), float(loc_values["ra"][0]))
                and np.allclose(np.asarray(loc_values["dec"], dtype=float), float(loc_values["dec"][0]))
                and np.allclose(
                    np.asarray(loc_values["x_400MHz"], dtype=float),
                    float(loc_values["x_400MHz"][0]),
                )
                and np.allclose(
                    np.asarray(loc_values["y_400MHz"], dtype=float),
                    float(loc_values["y_400MHz"][0]),
                )
            ):
                raise CorruptedDataError(
                    "Beamformed BBData currently supports only one tied beam split into polarizations; multi-beam layouts are not yet supported.",
                    path=resolved,
                )

            pol_labels = []
            for value in loc_values["pol"]:
                decoded = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
                pol_labels.append(decoded.strip().upper())
            if any(not label for label in pol_labels) or len(set(pol_labels)) != nbeam:
                raise CorruptedDataError(
                    "Beamformed BBData beam axis does not look like a polarization split (missing or duplicate polarization labels).",
                    path=resolved,
                )

            header_npol = nbeam
            power_values = np.asarray(power[:], dtype=np.float32)
            finite_counts = np.sum(np.isfinite(power_values), axis=1, dtype=np.int32)
            raw = np.zeros((nchan, ntime), dtype=np.float32)
            np.divide(
                np.nansum(power_values, axis=1, dtype=np.float32),
                finite_counts,
                out=raw,
                where=finite_counts > 0,
            )

        start_mjd = _unix_to_mjd(float(np.min(channel_start_sec)))
        event_mjd = _parse_event_mjd(_decode_attr(root.attrs.get("event_date")))
        if not np.isfinite(start_mjd):
            if event_mjd is None:
                raise MetadataMissingError(
                    "Beamformed BBData lacks usable absolute timing in time0 or event_date.",
                    path=resolved,
                    fields=("time0", "event_date"),
                )
            start_mjd = event_mjd

        channel_offset_sec = channel_start_sec - float(np.min(channel_start_sec))
        base_alignment_sec = channel_offset_sec - _dm_delay_seconds(coherent_dm, freqs_mhz)
        base_alignment_bins = np.round(base_alignment_sec / tsamp).astype(int)
        stokes_i = _shift_rows_with_nan(raw, base_alignment_bins)

        read_start_sec = config.read_start_for_file(resolved.name)
        nstart = min(max(int(read_start_sec / tsamp), 0), max(ntime - 1, 0))
        nread = max(1, ntime - nstart)
        if config.read_end_sec is not None:
            nend = max(nstart + 1, int(config.read_end_sec / tsamp))
            nread = min(nread, nend - nstart)
        stokes_i = stokes_i[:, nstart : nstart + nread]
        non_finite = ~np.isfinite(stokes_i)
        if non_finite.any():
            stokes_i[non_finite] = 0.0

        if inspection is None:
            filterbank_inspection, _ = self._read_inspection(resolved)
        else:
            filterbank_inspection = inspection

        residual_dm = float(config.dm) - float(coherent_dm)
        stokes_i = dedisperse(stokes_i, residual_dm, freqs_mhz, tsamp)
        stokes_i = _normalize_waterfall(stokes_i, config.normalization_tail_fraction)

        freq_lo = float(np.min(freqs_mhz))
        freq_hi = float(np.max(freqs_mhz))
        freqres = _estimate_freqres(freqs_mhz, resolved)
        sefd_jy = config.sefd_jy
        if sefd_jy is None:
            sefd_jy = resolve_default_sefd_jy(config.preset_key, freq_lo, freq_hi)

        effective_npol = (
            max(1, int(config.npol_override)) if config.npol_override is not None else header_npol
        )

        metadata = FilterbankMetadata(
            source_path=resolved,
            source_name=filterbank_inspection.source_name,
            tsamp=tsamp,
            freqres=freqres,
            start_mjd=start_mjd,
            read_start_sec=read_start_sec,
            sefd_jy=sefd_jy,
            bandwidth_mhz=float(abs(freqs_mhz[-1] - freqs_mhz[0])) + freqres,
            npol=effective_npol,
            freqs_mhz=freqs_mhz,
            header_npol=header_npol,
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
                float(np.max(freqs_mhz)) if abs(float(config.dm) - float(coherent_dm)) > 0.0 else None
            ),
            dedispersion_reference_basis=(
                "flits_residual_integer_bin_dedispersion_max_frequency"
                if abs(float(config.dm) - float(coherent_dm)) > 0.0
                else "unknown_chime_coherent_reference"
            ),
        )
        return stokes_i, validate_metadata(metadata)


__all__ = ["ChimeHdf5Reader"]
