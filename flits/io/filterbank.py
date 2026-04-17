from __future__ import annotations

import errno
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import numpy as np

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
def _open_reader(source_path: Path) -> Iterator["your.Your"]:
    """Open a your.Your reader, falling back to a local copy if the source mount rejects mmap.

    Why: some network/FUSE mounts (sshfs, SMB, iCloud, etc.) return EDEADLK from mmap(),
    which breaks pysigproc's reader. Copying the file to a local tempdir sidesteps this
    without changing the read path.
    """
    if your.Your is None:
        raise RuntimeError("The 'your' package is unavailable in the active environment.") from _YOUR_IMPORT_ERROR

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


@dataclass(frozen=True)
class FilterbankInspection:
    source_path: Path
    source_name: str | None
    telescope_id: int | None
    machine_id: int | None
    detected_preset_key: str
    detection_basis: str


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


def _inspect_reader(reader: your.Your, source_path: Path) -> FilterbankInspection:
    telescope_id = _safe_int(getattr(reader, "telescope_id", None))
    machine_id = _safe_int(getattr(reader, "machine_id", None))
    detected_preset_key, detection_basis = detect_preset(telescope_id, machine_id)
    return FilterbankInspection(
        source_path=source_path,
        source_name=_decode_source_name(getattr(reader, "source_name", None)),
        telescope_id=telescope_id,
        machine_id=machine_id,
        detected_preset_key=detected_preset_key,
        detection_basis=detection_basis,
    )


def inspect_filterbank(path: str | Path) -> FilterbankInspection:
    source_path = Path(path).expanduser().resolve()
    with _open_reader(source_path) as reader:
        return _inspect_reader(reader, source_path)


def load_filterbank_data(
    path: str | Path,
    config: ObservationConfig,
    inspection: FilterbankInspection | None = None,
) -> tuple[np.ndarray, FilterbankMetadata]:
    source_path = Path(path).expanduser().resolve()
    with _open_reader(source_path) as reader:
        header = reader.your_header
        filterbank_inspection = inspection or _inspect_reader(reader, source_path)

        tsamp = float(header.tsamp)
        freqres = float(abs(header.foff))
        start_mjd = float(header.tstart)
        bw = float(abs(header.bw))
        header_npol = max(1, int(header.npol))

        freqs_mhz = float(header.fch1) + (float(header.foff) * np.arange(header.nchans, dtype=float))
        freq_lo = float(np.min(freqs_mhz))
        freq_hi = float(np.max(freqs_mhz))
        sefd_jy = config.sefd_jy
        if sefd_jy is None:
            sefd_jy = resolve_default_sefd_jy(config.preset_key, freq_lo, freq_hi)

        read_start_sec = config.read_start_for_file(source_path.name)
        nstart = min(max(int(read_start_sec / tsamp), 0), max(header.nspectra - 1, 0))
        nread = max(1, int(header.nspectra - nstart))
        
        if config.read_end_sec is not None:
            # We want to read up to the absolute read_end_sec bound.
            # To do this, we calculate the absolute bin index of the end,
            # and subtract our starting bin index.
            nend = max(nstart + 1, int(config.read_end_sec / tsamp))
            requested_nread = nend - nstart
            nread = min(nread, requested_nread)

        raw = reader.get_data(nstart, nread, npoln=header_npol)
        stokes_i, effective_npol = _build_stokes_i(raw)
        effective_npol = max(1, int(config.npol_override)) if config.npol_override is not None else effective_npol
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
        return stokes_i, metadata
