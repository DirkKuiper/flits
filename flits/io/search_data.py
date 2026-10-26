"""Native readers for SIGPROC filterbank and PSRFITS search-mode files.

Both are written from the format definitions: the SIGPROC header keywords
(SIGPROC user guide) and the PSRFITS standard (Hotan, van Straten & Manchester
2004, PASA 21, 302). PSRFITS samples narrower than a byte are packed with the
first sample in the most significant bits, and every sample is calibrated as
``(raw - ZERO_OFF) * DAT_SCL + DAT_OFFS`` and multiplied by ``DAT_WTS``.

A file is opened with :func:`open_search_file`, whose result has a ``header``
(:class:`SearchHeader`) and a ``read(nstart, nsamp)`` method returning
``(time, channel)`` for one polarization product, or ``(time, product,
channel)`` for several, in the file's channel order.
"""

from __future__ import annotations

import errno
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from flits.io.errors import CorruptedDataError, FormatDetectionError, MetadataMissingError

_SIGPROC_STRING_KEYS = frozenset({"rawdatafile", "source_name"})
_SIGPROC_INT_KEYS = frozenset(
    {
        "telescope_id",
        "machine_id",
        "data_type",
        "barycentric",
        "pulsarcentric",
        "nbits",
        "nsamples",
        "nchans",
        "nifs",
        "nbeams",
        "ibeam",
        "nbins",
    }
)
_SIGPROC_DOUBLE_KEYS = frozenset(
    {"tstart", "tsamp", "fch1", "foff", "refdm", "az_start", "za_start", "src_raj", "src_dej", "period"}
)
_SIGPROC_DTYPES: dict[int, np.dtype[Any]] = {8: np.dtype(np.uint8), 16: np.dtype("<u2"), 32: np.dtype("<f4")}
# SIGPROC does not record which products a multi-IF file holds.
_SIGPROC_POLN_ORDER = {1: "I", 2: "AABB", 4: "IQUV"}
_MAX_HEADER_STRING = 4096


@dataclass(frozen=True)
class SearchHeader:
    """The header fields FLITS reads from a SIGPROC or PSRFITS search-mode file.

    Frequencies are in MHz and follow the file's channel order: channel ``i``
    is at ``fch1 + i * foff``. Times are in seconds, ``tstart`` is an MJD.
    """

    nchans: int
    nspectra: int
    npol: int
    nbits: int
    tsamp: float | None
    fch1: float | None
    foff: float | None
    tstart: float | None
    poln_order: str | None = None
    source_name: str | None = None
    telescope_name: str | None = None
    telescope_id: int | None = None
    machine_id: int | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    src_raj: float | None = None
    src_dej: float | None = None
    barycentric: int | None = None
    pulsarcentric: int | None = None

    @property
    def bw(self) -> float | None:
        """Total bandwidth in MHz, negative when frequency falls with channel index."""
        return None if self.foff is None else self.nchans * self.foff


def _sexagesimal_to_deg(sign: float, whole: int, minutes: int, seconds: float, *, hours: bool) -> float:
    degrees = sign * (whole * 3600 + minutes * 60 + seconds) / 3600
    return degrees * 15 if hours else degrees


def packed_sexagesimal_to_deg(value: float, *, hours: bool) -> float:
    """Convert a SIGPROC ``hhmmss.s`` or ``ddmmss.s`` coordinate to degrees."""
    sign = -1.0 if value < 0 else 1.0
    magnitude = abs(float(value))
    whole = int(magnitude // 10000)
    minutes = int((magnitude - whole * 10000) // 100)
    seconds = magnitude - whole * 10000 - minutes * 100
    return _sexagesimal_to_deg(sign, whole, minutes, seconds, hours=hours)


def text_sexagesimal_to_deg(text: str, *, hours: bool) -> float | None:
    """Convert ``hh:mm:ss.s`` or ``dd:mm:ss.s`` text to degrees, or None if it does not parse."""
    stripped = text.strip()
    sign = -1.0 if stripped.startswith("-") else 1.0
    parts = stripped.lstrip("+-").split(":")
    if len(parts) != 3:
        return None
    try:
        return _sexagesimal_to_deg(sign, int(parts[0]), int(parts[1]), float(parts[2]), hours=hours)
    except ValueError:
        return None


def _read_sigproc_string(handle: BinaryIO, path: Path) -> str:
    raw_length = handle.read(4)
    if len(raw_length) < 4:
        raise CorruptedDataError("SIGPROC header ends before HEADER_END", path=path)
    (length,) = struct.unpack("<i", raw_length)
    if not 0 < length <= _MAX_HEADER_STRING:
        raise CorruptedDataError(f"SIGPROC header holds a string of invalid length {length}", path=path)
    raw = handle.read(length)
    if len(raw) < length:
        raise CorruptedDataError("SIGPROC header ends inside a string", path=path)
    return raw.decode("utf-8", errors="replace")


def _read_sigproc_value(handle: BinaryIO, fmt: str, path: Path) -> Any:
    size = struct.calcsize(fmt)
    raw = handle.read(size)
    if len(raw) < size:
        raise CorruptedDataError("SIGPROC header ends inside a value", path=path)
    return struct.unpack(fmt, raw)[0]


def _parse_sigproc_header(handle: BinaryIO, path: Path) -> dict[str, Any]:
    if _read_sigproc_string(handle, path) != "HEADER_START":
        raise FormatDetectionError("File does not start with a SIGPROC header", path=path)
    fields: dict[str, Any] = {}
    while True:
        key = _read_sigproc_string(handle, path)
        if key == "HEADER_END":
            fields["_header_size"] = handle.tell()
            return fields
        if key in _SIGPROC_STRING_KEYS:
            fields[key] = _read_sigproc_string(handle, path)
        elif key in _SIGPROC_INT_KEYS:
            fields[key] = int(_read_sigproc_value(handle, "<i", path))
        elif key in _SIGPROC_DOUBLE_KEYS:
            fields[key] = float(_read_sigproc_value(handle, "<d", path))
        elif key == "signed":
            fields[key] = int(_read_sigproc_value(handle, "<b", path))
        elif key == "FREQUENCY_START":
            raise FormatDetectionError("SIGPROC files with a channel frequency table are not supported", path=path)
        else:
            raise CorruptedDataError(f"Unknown SIGPROC header keyword {key!r}", path=path)


@dataclass(frozen=True)
class SigprocFile:
    """A SIGPROC filterbank: samples stored ``[time][IF][channel]`` after the header."""

    path: Path
    header: SearchHeader
    header_size: int
    dtype: np.dtype[Any]

    @classmethod
    def open(cls, path: Path) -> SigprocFile:
        with open(path, "rb") as handle:
            fields = _parse_sigproc_header(handle, path)
        missing = [name for name in ("nchans", "nbits") if name not in fields]
        if missing:
            raise MetadataMissingError(f"SIGPROC header lacks {', '.join(missing)}", path=path, fields=tuple(missing))
        nchans, nbits, nifs = int(fields["nchans"]), int(fields["nbits"]), int(fields.get("nifs", 1))
        if nbits not in _SIGPROC_DTYPES:
            raise FormatDetectionError(f"SIGPROC files with nbits={nbits} are not supported", path=path)
        if nifs not in _SIGPROC_POLN_ORDER or nchans <= 0:
            raise CorruptedDataError(f"SIGPROC header has nifs={nifs}, nchans={nchans}", path=path)
        header_size = int(fields["_header_size"])
        bytes_per_spectrum = nchans * nifs * nbits // 8
        nspectra = (path.stat().st_size - header_size) // bytes_per_spectrum
        src_raj, src_dej = fields.get("src_raj"), fields.get("src_dej")
        source_name = str(fields["source_name"]).strip() if "source_name" in fields else None
        header = SearchHeader(
            nchans=nchans,
            nspectra=int(nspectra),
            npol=nifs,
            nbits=nbits,
            tsamp=fields.get("tsamp"),
            fch1=fields.get("fch1"),
            foff=fields.get("foff"),
            tstart=fields.get("tstart"),
            poln_order=_SIGPROC_POLN_ORDER[nifs],
            source_name=source_name or None,
            telescope_id=fields.get("telescope_id"),
            machine_id=fields.get("machine_id"),
            src_raj=src_raj,
            src_dej=src_dej,
            ra_deg=None if src_raj is None else packed_sexagesimal_to_deg(src_raj, hours=True),
            dec_deg=None if src_dej is None else packed_sexagesimal_to_deg(src_dej, hours=False),
            barycentric=fields.get("barycentric"),
            pulsarcentric=fields.get("pulsarcentric"),
        )
        return cls(path=path, header=header, header_size=header_size, dtype=_SIGPROC_DTYPES[nbits])

    def read(self, nstart: int, nsamp: int) -> np.ndarray:
        """Read up to `nsamp` spectra from spectrum `nstart`, stopping at the end of the file."""
        header = self.header
        nstart = max(0, int(nstart))
        count = max(0, min(int(nsamp), header.nspectra - nstart))
        values_per_spectrum = header.nchans * header.npol
        offset = self.header_size + nstart * values_per_spectrum * self.dtype.itemsize
        data = np.fromfile(self.path, dtype=self.dtype, count=count * values_per_spectrum, offset=offset)
        if data.size != count * values_per_spectrum:
            raise CorruptedDataError("SIGPROC data block is shorter than its header implies", path=self.path)
        data = data.reshape(count, header.npol, header.nchans)
        return data[:, 0, :] if header.npol == 1 else data


@contextmanager
def _open_fits(path: Path) -> Iterator[Any]:
    """Open a FITS file memory-mapped, or read it in full where the filesystem refuses mmap."""
    from astropy.io import fits

    try:
        hdul = fits.open(path, memmap=True)
    except OSError as exc:
        # Some network and FUSE mounts (sshfs, SMB, iCloud) refuse mmap with EDEADLK.
        if exc.errno != errno.EDEADLK:
            raise
        hdul = fits.open(path, memmap=False)
    try:
        yield hdul
    finally:
        hdul.close()


def _header_number(header: Any, key: str, default: float | None = None) -> float | None:
    value = header.get(key, default)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _header_text(header: Any, key: str) -> str | None:
    value = header.get(key)
    text = None if value is None else str(value).strip()
    return text or None


def _unpack_msb_first(raw: np.ndarray, nbits: int) -> np.ndarray:
    """Unpack sub-byte samples, the first sample in each byte's most significant bits."""
    shifts = np.arange(8 - nbits, -1, -nbits, dtype=np.uint8)
    mask = np.uint8((1 << nbits) - 1)
    return ((raw[..., np.newaxis] >> shifts) & mask).reshape(raw.shape[0], -1)


@dataclass(frozen=True)
class PsrfitsSearchFile:
    """A PSRFITS search-mode file: ``NSBLK`` spectra per row of the SUBINT table."""

    path: Path
    header: SearchHeader
    nsblk: int
    nrows: int
    zero_off: float
    signed: bool

    @classmethod
    def open(cls, path: Path) -> PsrfitsSearchFile:
        with _open_fits(path) as hdul:
            primary = hdul[0].header
            try:
                subint = hdul["SUBINT"]
            except KeyError as exc:
                raise FormatDetectionError("PSRFITS file has no SUBINT table", path=path) from exc
            sub = subint.header
            required = ("NCHAN", "NPOL", "NBITS", "NSBLK", "TBIN")
            missing = [key for key in required if sub.get(key) is None]
            if missing:
                raise MetadataMissingError(
                    f"PSRFITS SUBINT header lacks {', '.join(missing)}", path=path, fields=tuple(missing)
                )
            nchan, npol, nbits, nsblk = (int(sub[key]) for key in ("NCHAN", "NPOL", "NBITS", "NSBLK"))
            if nbits not in (1, 2, 4, 8, 16, 32):
                raise FormatDetectionError(f"PSRFITS files with NBITS={nbits} are not supported", path=path)
            tbin = float(sub["TBIN"])
            nrows = int(sub.get("NAXIS2", 0))
            dat_freq = np.asarray(subint.data["DAT_FREQ"][0], dtype=float) if nrows else np.empty(0)
            chan_bw = _header_number(sub, "CHAN_BW")
            if chan_bw is None and dat_freq.size > 1:
                chan_bw = float(dat_freq[1] - dat_freq[0])

        imjd, smjd = _header_number(primary, "STT_IMJD"), _header_number(primary, "STT_SMJD")
        tstart = None
        if imjd is not None and smjd is not None:
            offs = _header_number(primary, "STT_OFFS", 0.0) or 0.0
            nsuboffs = _header_number(sub, "NSUBOFFS", 0.0) or 0.0
            tstart = imjd + (smjd + offs + nsuboffs * nsblk * tbin) / 86400
        ra_text, dec_text = _header_text(primary, "RA"), _header_text(primary, "DEC")
        header = SearchHeader(
            nchans=nchan,
            nspectra=nrows * nsblk,
            npol=npol,
            nbits=nbits,
            tsamp=tbin,
            fch1=float(dat_freq[0]) if dat_freq.size else None,
            foff=chan_bw,
            tstart=tstart,
            poln_order=_header_text(sub, "POL_TYPE"),
            source_name=_header_text(primary, "SRC_NAME"),
            telescope_name=_header_text(primary, "TELESCOP"),
            ra_deg=None if ra_text is None else text_sexagesimal_to_deg(ra_text, hours=True),
            dec_deg=None if dec_text is None else text_sexagesimal_to_deg(dec_text, hours=False),
        )
        return cls(
            path=path,
            header=header,
            nsblk=nsblk,
            nrows=nrows,
            zero_off=_header_number(sub, "ZERO_OFF", 0.0) or 0.0,
            signed=int(_header_number(sub, "SIGNINT", 0) or 0) == 1,
        )

    def read(self, nstart: int, nsamp: int) -> np.ndarray:
        """Read up to `nsamp` calibrated spectra from spectrum `nstart`, as float32."""
        header = self.header
        nstart = max(0, int(nstart))
        stop = min(header.nspectra, nstart + max(0, int(nsamp)))
        shape: tuple[int, ...] = (header.npol, header.nchans) if header.npol > 1 else (header.nchans,)
        if stop <= nstart:
            return np.empty((0, *shape), dtype=np.float32)
        row_lo, row_hi = nstart // self.nsblk, -(-stop // self.nsblk)
        with _open_fits(self.path) as hdul:
            table = hdul["SUBINT"].data
            raw = np.asarray(table["DATA"][row_lo:row_hi])
            scales = np.asarray(table["DAT_SCL"][row_lo:row_hi], dtype=np.float32)
            offsets = np.asarray(table["DAT_OFFS"][row_lo:row_hi], dtype=np.float32)
            weights = np.asarray(table["DAT_WTS"][row_lo:row_hi], dtype=np.float32)
        rows = row_hi - row_lo
        raw = raw.reshape(rows, -1)
        if header.nbits < 8:
            raw = _unpack_msb_first(raw.astype(np.uint8, copy=False), header.nbits)
        elif header.nbits == 8 and self.signed:
            raw = raw.astype(np.uint8, copy=False).view(np.int8)
        expected = self.nsblk * header.npol * header.nchans
        if raw.shape[1] != expected:
            raise CorruptedDataError(
                f"PSRFITS row holds {raw.shape[1]} samples, expected NSBLK*NPOL*NCHAN = {expected}", path=self.path
            )
        values = raw.reshape(rows, self.nsblk, header.npol, header.nchans).astype(np.float32)
        if self.zero_off:
            values -= np.float32(self.zero_off)
        values *= scales.reshape(rows, 1, header.npol, header.nchans)
        values += offsets.reshape(rows, 1, header.npol, header.nchans)
        values *= weights.reshape(rows, 1, 1, header.nchans)
        start = nstart - row_lo * self.nsblk
        spectra = values.reshape(rows * self.nsblk, header.npol, header.nchans)[start : start + stop - nstart]
        return spectra[:, 0, :] if header.npol == 1 else spectra


def open_search_file(path: Path, *, is_fits: bool) -> SigprocFile | PsrfitsSearchFile:
    """Open a SIGPROC filterbank, or a PSRFITS search-mode file when `is_fits`."""
    return PsrfitsSearchFile.open(path) if is_fits else SigprocFile.open(path)


__all__ = [
    "PsrfitsSearchFile",
    "SearchHeader",
    "SigprocFile",
    "open_search_file",
    "packed_sexagesimal_to_deg",
    "text_sexagesimal_to_deg",
]
