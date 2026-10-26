"""Native SIGPROC and PSRFITS search-mode readers.

The format tests compute the expected samples by hand from the format
definitions. The equivalence tests compare with the ``your`` package, which
FLITS used to read these files, on the cases ``your`` reads correctly; it
mishandles sub-byte PSRFITS packing, per-polarization calibration, ZERO_OFF,
SIGNINT, and scaled 8-bit values above 255.
"""

from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

from flits.io.errors import CorruptedDataError, FormatDetectionError, MetadataMissingError
from flits.io.search_data import (
    PsrfitsSearchFile,
    SigprocFile,
    open_search_file,
    packed_sexagesimal_to_deg,
    text_sexagesimal_to_deg,
)
from flits.io.sigproc import _FIELD_TYPES, SigprocFilterbankHeader, _write_sigproc_field, _write_sigproc_string


def _write_sigproc(path: Path, payload: bytes, *, nbits: int, nchans: int = 8, nifs: int = 1, **fields: object) -> Path:
    header = SigprocFilterbankHeader(
        rawdatafile="x.fil",
        source_name="SRC",
        nchans=nchans,
        foff=-1.0,
        fch1=1500.0,
        tsamp=1e-3,
        tstart=60000.5,
        telescope_id=4,
        src_raj=123456.78,
        src_dej=-123456.78,
        nbits=nbits,
        nifs=nifs,
    )
    buffer = BytesIO()
    _write_sigproc_string(buffer, "HEADER_START")
    for name, field_type in _FIELD_TYPES:
        _write_sigproc_field(buffer, name, fields.get(name, getattr(header, name)), field_type)
    _write_sigproc_string(buffer, "HEADER_END")
    path.write_bytes(buffer.getvalue() + payload)
    return path


def _write_psrfits(
    path: Path,
    raw: np.ndarray,
    *,
    nbits: int = 8,
    npol: int = 1,
    nchan: int = 4,
    nsblk: int = 2,
    scales: np.ndarray | float = 1.0,
    offsets: np.ndarray | float = 0.0,
    weights: np.ndarray | float = 1.0,
    ascending: bool = False,
    pol_type: str = "AA+BB",
    **subint_keys: object,
) -> Path:
    """A minimal search-mode PSRFITS file; `raw` holds one row of packed bytes per subintegration."""
    from astropy.io import fits

    nsub = raw.shape[0]
    freqs = 1493.0 + np.arange(nchan) if ascending else 1500.0 - np.arange(nchan)
    primary = fits.PrimaryHDU()
    for key, value in {
        "FITSTYPE": "PSRFITS",
        "OBS_MODE": "SEARCH",
        "TELESCOP": "GBT",
        "SRC_NAME": "PSRTEST",
        "STT_IMJD": 60000,
        "STT_SMJD": 43200,
        "STT_OFFS": 0.25,
        "RA": "12:34:56.7800",
        "DEC": "-12:34:56.7800",
    }.items():
        primary.header[key] = value

    def per_row(value: np.ndarray | float, width: int) -> np.ndarray:
        return np.broadcast_to(np.asarray(value, dtype=np.float32), (nsub, width)).copy()

    columns = [
        fits.Column(name="DAT_FREQ", format=f"{nchan}D", array=np.tile(freqs, (nsub, 1))),
        fits.Column(name="DAT_WTS", format=f"{nchan}E", array=per_row(weights, nchan)),
        fits.Column(name="DAT_OFFS", format=f"{nchan * npol}E", array=per_row(offsets, nchan * npol)),
        fits.Column(name="DAT_SCL", format=f"{nchan * npol}E", array=per_row(scales, nchan * npol)),
        fits.Column(name="DATA", format=f"{raw.shape[1]}B", array=raw),
    ]
    subint = fits.BinTableHDU.from_columns(columns, name="SUBINT")
    for key, value in {
        "NBITS": nbits,
        "NSBLK": nsblk,
        "NCHAN": nchan,
        "NPOL": npol,
        "TBIN": 1e-3,
        "CHAN_BW": 1.0 if ascending else -1.0,
        "POL_TYPE": pol_type,
        "NSUBOFFS": 0,
        **subint_keys,
    }.items():
        subint.header[key] = value
    fits.HDUList([primary, subint]).writeto(path, overwrite=True)
    return path


# --- SIGPROC --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("nbits", "dtype"), [(8, np.uint8), (16, np.dtype("<u2")), (32, np.dtype("<f4"))])
def test_sigproc_reads_samples_time_major_in_file_dtype(tmp_path, nbits, dtype) -> None:
    values = np.arange(5 * 8).astype(dtype)
    file = SigprocFile.open(_write_sigproc(tmp_path / "x.fil", values.tobytes(), nbits=nbits))

    assert file.header.nspectra == 5 and file.header.npol == 1 and file.header.poln_order == "I"
    data = file.read(1, 2)
    assert data.dtype == dtype
    np.testing.assert_array_equal(data, values.reshape(5, 8)[1:3])


def test_sigproc_keeps_four_ifs_as_time_product_channel(tmp_path) -> None:
    values = np.arange(3 * 4 * 8, dtype=np.float32)
    file = SigprocFile.open(_write_sigproc(tmp_path / "x.fil", values.tobytes(), nbits=32, nifs=4))

    assert file.header.npol == 4 and file.header.poln_order == "IQUV"
    np.testing.assert_array_equal(file.read(0, 3), values.reshape(3, 4, 8))


def test_sigproc_header_fields(tmp_path) -> None:
    header = SigprocFile.open(_write_sigproc(tmp_path / "x.fil", bytes(16), nbits=8)).header

    assert (header.nchans, header.nspectra, header.tsamp, header.fch1, header.foff) == (8, 2, 1e-3, 1500.0, -1.0)
    assert header.bw == -8.0
    assert header.tstart == 60000.5
    assert header.source_name == "SRC" and header.telescope_id == 4
    assert header.ra_deg == 188.73658333333333
    assert header.dec_deg == -12.58243888888889


def test_sigproc_read_stops_at_the_end_of_the_file(tmp_path) -> None:
    file = SigprocFile.open(_write_sigproc(tmp_path / "x.fil", bytes(4 * 8), nbits=8))
    assert file.read(3, 10).shape == (1, 8)
    assert file.read(4, 10).shape == (0, 8)


def test_sigproc_rejects_what_it_cannot_read(tmp_path) -> None:
    with pytest.raises(FormatDetectionError):
        SigprocFile.open(_write_sigproc(tmp_path / "a.fil", bytes(8), nbits=4))
    with pytest.raises(MetadataMissingError):
        SigprocFile.open(_write_sigproc(tmp_path / "b.fil", bytes(8), nbits=8, nchans=None))
    not_sigproc = tmp_path / "c.fil"
    not_sigproc.write_bytes(struct.pack("<i", 4) + b"JUNK")
    with pytest.raises(FormatDetectionError):
        SigprocFile.open(not_sigproc)
    unknown_key = tmp_path / "d.fil"
    buffer = BytesIO()
    _write_sigproc_string(buffer, "HEADER_START")
    _write_sigproc_string(buffer, "mystery")
    unknown_key.write_bytes(buffer.getvalue())
    with pytest.raises(CorruptedDataError):
        SigprocFile.open(unknown_key)


def test_coordinates_convert_from_packed_and_text_sexagesimal() -> None:
    assert packed_sexagesimal_to_deg(212739.864, hours=True) == 321.91610000000003
    assert packed_sexagesimal_to_deg(41946.0, hours=False) == 4.329444444444444
    assert text_sexagesimal_to_deg("12:34:56.78", hours=True) == 188.73658333333333
    assert text_sexagesimal_to_deg("-12:34:56.78", hours=False) == -12.58243888888889
    assert text_sexagesimal_to_deg("not an angle", hours=True) is None


# --- PSRFITS --------------------------------------------------------------------------------------


def test_psrfits_calibrates_each_polarization_and_applies_weights(tmp_path) -> None:
    nchan, npol = 4, 2
    raw = np.full((1, 2 * npol * nchan), 200, dtype=np.uint8)
    scales = np.arange(1, nchan * npol + 1, dtype=np.float32)
    offsets = 100.0 * np.arange(nchan * npol, dtype=np.float32)
    weights = np.array([1.0, 0.0, 1.0, 0.5], dtype=np.float32)
    path = _write_psrfits(
        tmp_path / "x.fits", raw, npol=npol, nchan=nchan, scales=scales, offsets=offsets, weights=weights, ZERO_OFF=8.0
    )

    data = PsrfitsSearchFile.open(path).read(0, 1)

    # Scales and offsets are polarization-major; nothing wraps at 255.
    expected = ((200.0 - 8.0) * scales + offsets).reshape(npol, nchan) * weights
    assert data.dtype == np.float32
    np.testing.assert_allclose(data[0], expected, rtol=1e-6)


@pytest.mark.parametrize("nbits", [1, 2, 4])
def test_psrfits_unpacks_the_first_sample_from_the_most_significant_bits(tmp_path, nbits) -> None:
    nchan, nsblk = 8, 2
    raw = np.random.default_rng(nbits).integers(0, 256, size=(1, nsblk * nchan * nbits // 8), dtype=np.uint8)
    path = _write_psrfits(tmp_path / "x.fits", raw, nbits=nbits, nchan=nchan, nsblk=nsblk)

    data = PsrfitsSearchFile.open(path).read(0, nsblk)

    bits = np.unpackbits(raw[0]).reshape(-1, nbits)
    expected = (bits * (1 << np.arange(nbits - 1, -1, -1))).sum(axis=1).reshape(nsblk, nchan)
    np.testing.assert_array_equal(data, expected)


def test_psrfits_reads_signed_bytes_when_signint_is_set(tmp_path) -> None:
    raw = np.array([[0, 1, 127, 128, 255, 200, 2, 3]], dtype=np.uint8)
    path = _write_psrfits(tmp_path / "x.fits", raw, SIGNINT=1)
    np.testing.assert_array_equal(PsrfitsSearchFile.open(path).read(0, 2).ravel(), raw.view(np.int8).ravel())


def test_psrfits_reads_across_rows_and_stops_at_the_end(tmp_path) -> None:
    raw = np.arange(3 * 2 * 4, dtype=np.uint8).reshape(3, 8)
    file = PsrfitsSearchFile.open(_write_psrfits(tmp_path / "x.fits", raw))

    assert file.header.nspectra == 6
    np.testing.assert_array_equal(file.read(1, 3), raw.reshape(6, 4)[1:4])
    assert file.read(5, 10).shape == (1, 4)
    assert file.read(6, 10).shape == (0, 4)


def test_psrfits_header_fields(tmp_path) -> None:
    raw = np.zeros((2, 8), dtype=np.uint8)
    header = PsrfitsSearchFile.open(_write_psrfits(tmp_path / "x.fits", raw, ascending=True, NSUBOFFS=3)).header

    assert (header.nchans, header.nspectra, header.npol, header.tsamp) == (4, 4, 1, 1e-3)
    assert (header.fch1, header.foff) == (1493.0, 1.0)
    assert header.tstart == 60000 + (43200 + 0.25 + 3 * 2 * 1e-3) / 86400
    assert (header.source_name, header.telescope_name, header.poln_order) == ("PSRTEST", "GBT", "AA+BB")
    assert header.dec_deg == -12.58243888888889


# --- Equivalence with `your` ---------------------------------------------------------------------


def _assert_matches_your(path: Path, *, is_fits: bool) -> None:
    your = pytest.importorskip("your")
    reference = your.Your(str(path))
    native = open_search_file(path, is_fits=is_fits)
    for name in ("nchans", "nspectra", "npol", "tsamp", "fch1", "foff", "bw", "tstart", "poln_order"):
        assert getattr(native.header, name) == getattr(reference.your_header, name), name
    assert native.header.ra_deg == pytest.approx(reference.ra_deg, rel=1e-15)
    assert native.header.dec_deg == pytest.approx(reference.dec_deg, rel=1e-15)
    count = native.header.nspectra
    expected = np.asarray(reference.get_data(0, count, npoln=max(1, native.header.npol)))
    np.testing.assert_array_equal(native.read(0, count).astype(np.float64), expected.astype(np.float64))


@pytest.mark.parametrize(("nbits", "nifs"), [(8, 1), (16, 1), (32, 1), (32, 4)])
def test_sigproc_matches_your(tmp_path, nbits, nifs) -> None:
    dtype = {8: np.uint8, 16: np.dtype("<u2"), 32: np.dtype("<f4")}[nbits]
    values = (np.random.default_rng(nbits).random(6 * nifs * 8) * 250).astype(dtype)
    _assert_matches_your(_write_sigproc(tmp_path / "x.fil", values.tobytes(), nbits=nbits, nifs=nifs), is_fits=False)


@pytest.mark.parametrize("synthetic_waterfall", ["psrfits"], indirect=True)
def test_psrfits_written_by_your_matches_your(synthetic_waterfall) -> None:
    _assert_matches_your(synthetic_waterfall.path, is_fits=True)
