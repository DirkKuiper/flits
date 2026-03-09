from __future__ import annotations

from pathlib import Path

import numpy as np
import your

from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig
from flits.signal import dedisperse, normalize


def _build_stokes_i(raw: np.ndarray) -> tuple[np.ndarray, int]:
    if raw.ndim == 2:
        return raw.T, 1

    aa = raw[:, 0, :].T
    if raw.shape[1] >= 2:
        bb = raw[:, 1, :].T
        return aa + bb, 2
    return aa, 1


def load_filterbank_data(path: str | Path, config: ObservationConfig) -> tuple[np.ndarray, FilterbankMetadata]:
    source_path = Path(path).expanduser().resolve()
    reader = your.Your(str(source_path))
    try:
        header = reader.your_header

        tsamp = float(header.tsamp)
        freqres = float(abs(header.foff))
        start_mjd = float(header.tstart)
        bw = float(abs(header.bw))
        header_npol = max(1, int(header.npol))

        freq_lo = float(header.fch1)
        freq_hi = float(header.fch1 + (header.foff * (header.nchans - 1)))
        freq_lo, freq_hi = min(freq_lo, freq_hi), max(freq_lo, freq_hi)
        freqs_mhz = np.linspace(freq_lo, freq_hi, header.nchans)

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
        stokes_i = normalize(stokes_i, offpulse)

        metadata = FilterbankMetadata(
            source_path=source_path,
            tsamp=tsamp,
            freqres=freqres,
            start_mjd=start_mjd,
            read_start_sec=read_start_sec,
            sefd_jy=config.sefd_jy,
            bandwidth_mhz=bw,
            npol=effective_npol,
            freqs_mhz=freqs_mhz,
            header_npol=header_npol,
        )
        return stokes_i, metadata
    finally:
        file_handle = getattr(reader, "fp", None)
        if file_handle is not None and not file_handle.closed:
            file_handle.close()
