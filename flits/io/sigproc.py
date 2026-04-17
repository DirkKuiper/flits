from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SigprocFilterbankHeader:
    rawdatafile: str
    source_name: str
    nchans: int
    foff: float
    fch1: float
    tsamp: float
    tstart: float
    machine_id: int = 0
    barycentric: int = 0
    pulsarcentric: int = 0
    telescope_id: int = 0
    src_raj: float = 0.0
    src_dej: float = 0.0
    az_start: float = -1.0
    za_start: float = -1.0
    data_type: int = 1
    nbeams: int = 1
    ibeam: int = 0
    nbits: int = 32
    nifs: int = 1


_FIELD_TYPES: tuple[tuple[str, str], ...] = (
    ("rawdatafile", "string"),
    ("source_name", "string"),
    ("machine_id", "int"),
    ("barycentric", "int"),
    ("pulsarcentric", "int"),
    ("telescope_id", "int"),
    ("src_raj", "double"),
    ("src_dej", "double"),
    ("az_start", "double"),
    ("za_start", "double"),
    ("data_type", "int"),
    ("fch1", "double"),
    ("foff", "double"),
    ("nchans", "int"),
    ("nbeams", "int"),
    ("ibeam", "int"),
    ("nbits", "int"),
    ("tstart", "double"),
    ("tsamp", "double"),
    ("nifs", "int"),
)


def _write_sigproc_string(handle: object, value: str) -> None:
    encoded = str(value).encode("utf-8")
    handle.write(struct.pack("i", len(encoded)))
    handle.write(encoded)


def _write_sigproc_field(handle: object, name: str, value: object, field_type: str) -> None:
    if value is None:
        return
    _write_sigproc_string(handle, name)
    if field_type == "string":
        _write_sigproc_string(handle, str(value))
    elif field_type == "int":
        handle.write(struct.pack("i", int(value)))
    elif field_type == "double":
        handle.write(struct.pack("d", float(value)))
    else:  # pragma: no cover - internal guard
        raise ValueError(f"Unsupported SIGPROC field type: {field_type}")


def build_sigproc_filterbank_bytes(data: np.ndarray, header: SigprocFilterbankHeader) -> bytes:
    spectra = np.asarray(data, dtype=np.float32)
    if spectra.ndim != 2:
        raise ValueError("SIGPROC export expects a 2D waterfall array.")
    if int(spectra.shape[0]) != int(header.nchans):
        raise ValueError("SIGPROC export channel count does not match header.nchans.")

    buffer = BytesIO()
    _write_sigproc_string(buffer, "HEADER_START")
    for name, field_type in _FIELD_TYPES:
        _write_sigproc_field(buffer, name, getattr(header, name), field_type)
    _write_sigproc_string(buffer, "HEADER_END")
    buffer.write(np.ascontiguousarray(spectra.T, dtype=np.float32).tobytes(order="C"))
    return buffer.getvalue()


def write_sigproc_filterbank(
    path: str | Path,
    data: np.ndarray,
    header: SigprocFilterbankHeader,
) -> bytes:
    content = build_sigproc_filterbank_bytes(data, header)
    output_path = Path(path)
    with output_path.open("wb") as handle:
        handle.write(content)
    return content


__all__ = ["SigprocFilterbankHeader", "build_sigproc_filterbank_bytes", "write_sigproc_filterbank"]
