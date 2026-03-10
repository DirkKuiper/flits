from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import your

from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig, detect_preset, resolve_default_sefd_jy
from flits.signal import dedisperse, normalize


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
    reader = your.Your(str(source_path))
    try:
        return _inspect_reader(reader, source_path)
    finally:
        file_handle = getattr(reader, "fp", None)
        if file_handle is not None and not file_handle.closed:
            file_handle.close()


def load_filterbank_data(
    path: str | Path,
    config: ObservationConfig,
    inspection: FilterbankInspection | None = None,
) -> tuple[np.ndarray, FilterbankMetadata]:
    source_path = Path(path).expanduser().resolve()
    reader = your.Your(str(source_path))
    try:
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

        raw = reader.get_data(nstart, nread, npoln=header_npol)
        stokes_i, effective_npol = _build_stokes_i(raw)
        stokes_i = dedisperse(stokes_i, config.dm, freqs_mhz, tsamp)

        if config.initial_crop_sec is not None:
            crop_end = min(stokes_i.shape[1], max(1, int(config.initial_crop_sec / tsamp)))
            stokes_i = stokes_i[:, :crop_end]

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
    finally:
        file_handle = getattr(reader, "fp", None)
        if file_handle is not None and not file_handle.closed:
            file_handle.close()
