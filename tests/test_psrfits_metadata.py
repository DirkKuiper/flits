"""PSRFITS metadata fallbacks: coordinates, header flags, fold period and channel frequencies.

Backends differ in which PSRFITS columns and keywords they write. These tests
remove one source of a quantity at a time and check that the reader recovers the
same number from the next one, or names what is missing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flits.io import inspect_filterbank, load_filterbank_data
from flits.io.errors import CorruptedDataError, MetadataMissingError
from flits.io.psrfits import (
    _build_folded_stokes_i,
    _coerce_coordinate_deg,
    _folded_psrfits_freqs_mhz,
    _folded_psrfits_period_sec,
    _folded_psrfits_raw_data,
    _folded_psrfits_scale,
    _packed_sexagesimal_to_deg,
    _psrfits_primary_fallback,
    _psrparam_float,
    _safe_bool_flag,
)
from flits.settings import ObservationConfig

fits = pytest.importorskip("astropy.io.fits")

NCHAN, NBIN = 16, 64
TBIN_S = 1e-3
OBSFREQ_MHZ, CHAN_BW_MHZ = 1400.0, -2.0
# Channel centres for a band centred on OBSFREQ, highest frequency first.
CHANNEL_FREQS_MHZ = OBSFREQ_MHZ - CHAN_BW_MHZ * (NCHAN - 1) / 2 + CHAN_BW_MHZ * np.arange(NCHAN)
RA_12H34M56_78S_DEG = (12 + 34 / 60 + 56.78 / 3600) * 15
DEC_12D34M56_78S_DEG = 12 + 34 / 60 + 56.78 / 3600


def _write_folded(
    path: Path,
    *,
    period_column: float | None = NBIN * TBIN_S,
    psrparam: tuple[str | bytes, ...] | None = (f"P0 {NBIN * TBIN_S:.12g}",),
    tbin: float | None = TBIN_S,
    dat_freq: np.ndarray | None = CHANNEL_FREQS_MHZ,
    chan_bw: float | None = CHAN_BW_MHZ,
    obsbw: float | None = CHAN_BW_MHZ * NCHAN,
    obsfreq: float | None = OBSFREQ_MHZ,
    primary_extra: dict[str, object] | None = None,
) -> Path:
    """Write a one-subintegration folded PSRFITS file with a Gaussian pulse."""
    rng = np.random.default_rng(7)
    phase = np.arange(NBIN)
    data = rng.normal(0.0, 1.0, size=(NCHAN, NBIN)) + 40.0 * np.exp(-((phase - NBIN // 2) ** 2) / 8.0)
    packed = np.rint(data).astype(">i2").reshape(1, 1, NCHAN, NBIN)

    primary = fits.PrimaryHDU()
    for key, value in {
        "FITSTYPE": "PSRFITS",
        "OBS_MODE": "PSR",
        "SRC_NAME": "FALLBACK",
        "TELESCOP": "SYNTH",
        "STT_IMJD": 60000,
        "STT_SMJD": 3600,
        "STT_OFFS": 0.25,
        "RAJ": "12:34:56.78",
        "DECJ": "-12:34:56.78",
        **(primary_extra or {}),
    }.items():
        primary.header[key] = value
    if obsfreq is not None:
        primary.header["OBSFREQ"] = obsfreq
    if obsbw is not None:
        primary.header["OBSBW"] = obsbw

    columns = []
    if period_column is not None:
        columns.append(fits.Column(name="PERIOD", format="D", array=np.array([period_column])))
    if dat_freq is not None:
        columns.append(fits.Column(name="DAT_FREQ", format=f"{dat_freq.size}D", array=dat_freq.reshape(1, -1)))
    columns += [
        fits.Column(name="DAT_OFFS", format=f"{NCHAN}E", array=np.zeros((1, NCHAN), dtype=">f4")),
        fits.Column(name="DAT_SCL", format=f"{NCHAN}E", array=np.ones((1, NCHAN), dtype=">f4")),
        fits.Column(name="DATA", format=f"{NCHAN * NBIN}I", dim=f"({NBIN},{NCHAN},1)", array=packed),
    ]
    subint = fits.BinTableHDU.from_columns(columns, name="SUBINT")
    subint.header["NBIN"] = NBIN
    subint.header["NCHAN"] = NCHAN
    subint.header["NPOL"] = 1
    subint.header["POL_TYPE"] = "INTEN"
    if tbin is not None:
        subint.header["TBIN"] = tbin
    if chan_bw is not None:
        subint.header["CHAN_BW"] = chan_bw

    hdus = [primary]
    if psrparam is not None:
        rows = np.array([row.encode() if isinstance(row, str) else row for row in psrparam], dtype="S96")
        hdus.append(
            fits.BinTableHDU.from_columns([fits.Column(name="PARAM", format="96A", array=rows)], name="PSRPARAM")
        )
    hdus.append(subint)
    fits.HDUList(hdus).writeto(path)
    return path


def _period_and_freqs(path: Path) -> tuple[float, np.ndarray]:
    with fits.open(path, memmap=False) as hdul:
        subint = hdul["SUBINT"]
        return _folded_psrfits_period_sec(hdul, subint, path), _folded_psrfits_freqs_mhz(hdul, subint, path)


# Coordinates and header flags ------------------------------------------------


@pytest.mark.parametrize(
    ("packed", "is_ra", "expected"),
    [
        (123456.78, True, RA_12H34M56_78S_DEG),
        (-123456.78, False, -DEC_12D34M56_78S_DEG),
        (230.0, False, 2.5 / 60),
        (126000.0, True, None),  # 60 minutes is not a valid packed value
        (125960.0, False, None),  # nor is 60 seconds
        ("not a number", True, None),
    ],
)
def test_packed_sexagesimal_coordinates(packed, is_ra, expected):
    result = _packed_sexagesimal_to_deg(packed, is_ra=is_ra)
    assert result == (None if expected is None else pytest.approx(expected, abs=1e-9))


@pytest.mark.parametrize(
    ("value", "is_ra", "expected"),
    [
        ("12:34:56.78", True, RA_12H34M56_78S_DEG),
        (b"-12:34:56.78", False, -DEC_12D34M56_78S_DEG),
        ("45.5", False, 45.5),
        ("   ", True, None),
        ("north-ish", False, None),
        (None, True, None),
        (188.5, True, 188.5),  # already degrees
        (-45.25, False, -45.25),
        (123456.78, True, RA_12H34M56_78S_DEG),  # packed hhmmss.s
        (-123456.78, False, -DEC_12D34M56_78S_DEG),  # packed ddmmss.s
        # A declination between 90 and 360 cannot be degrees; it is packed 0d02m30s.
        (230.0, False, 2.5 / 60),
        (float("nan"), False, None),
    ],
)
def test_header_coordinates_become_degrees(value, is_ra, expected):
    result = _coerce_coordinate_deg(value, is_ra=is_ra)
    assert result == (None if expected is None else pytest.approx(expected, abs=1e-6))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (1, True),
        ("0", False),
        (True, True),
        ("T", True),
        ("yes", True),
        ("F", False),
        ("no", False),
        ("maybe", None),
    ],
)
def test_header_flags_accept_fits_and_text_spellings(value, expected):
    assert _safe_bool_flag(value) is expected


def test_primary_header_fallback_reads_time_position_and_frame_flags(tmp_path):
    path = _write_folded(
        tmp_path / "flags.sf",
        primary_extra={"RAJ": "", "RA": 188.5, "DECJ": "", "DEC": 230.0, "BARYCENT": 1, "PULSAREN": "F"},
    )
    fallback = _psrfits_primary_fallback(path)
    assert fallback["tstart"] == pytest.approx(60000 + (3600 + 0.25) / 86400.0, abs=1e-12)
    assert fallback["telescope_name"] == "SYNTH"
    assert fallback["source_name"] == "FALLBACK"
    assert fallback["source_ra_deg"] == pytest.approx(188.5)
    assert fallback["source_dec_deg"] == pytest.approx(2.5 / 60)
    assert fallback["source_position_basis"] == "psrfits_primary_header"
    assert fallback["barycentric_header_flag"] is True
    assert fallback["pulsarcentric_header_flag"] is False


def test_primary_header_fallback_is_empty_for_unreadable_files(tmp_path):
    path = tmp_path / "broken.sf"
    path.write_bytes(b"not a FITS file")
    assert _psrfits_primary_fallback(path) == {}


def test_inspection_reports_sexagesimal_header_position(tmp_path):
    inspection = inspect_filterbank(_write_folded(tmp_path / "position.sf"))
    assert inspection.source_ra_deg == pytest.approx(RA_12H34M56_78S_DEG, abs=1e-6)
    assert inspection.source_dec_deg == pytest.approx(-DEC_12D34M56_78S_DEG, abs=1e-6)
    assert inspection.source_position_basis == "psrfits_primary_header"


# Fold period -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected_sec"),
    [
        ({}, NBIN * TBIN_S),
        ({"period_column": 0.0, "psrparam": ("P0 0.0714",)}, 0.0714),
        ({"period_column": None, "psrparam": ("PSRJ J0000+0000", "P0 0.0714")}, 0.0714),
        ({"period_column": None, "psrparam": (b"F0 12.5",)}, 1 / 12.5),
        # Malformed and unparsable rows are skipped, not fatal.
        ({"period_column": None, "psrparam": ("P0", "P0 abc", "F0 20")}, 1 / 20),
        ({"period_column": None, "psrparam": None}, NBIN * TBIN_S),
        ({"period_column": None, "psrparam": ("P0 -1",), "tbin": 2e-3}, NBIN * 2e-3),
    ],
    ids=["period-column", "zero-column-then-p0", "p0", "f0-bytes", "skips-bad-rows", "tbin", "negative-p0-then-tbin"],
)
def test_fold_period_falls_back_in_order(tmp_path, kwargs, expected_sec):
    period, _ = _period_and_freqs(_write_folded(tmp_path / "period.sf", **kwargs))
    assert period == pytest.approx(expected_sec, rel=1e-12)


def test_fold_period_without_any_source_names_every_field(tmp_path):
    path = _write_folded(tmp_path / "noperiod.sf", period_column=None, psrparam=None, tbin=None)
    with pytest.raises(MetadataMissingError) as excinfo:
        _period_and_freqs(path)
    assert excinfo.value.fields == ("SUBINT.PERIOD", "PSRPARAM.P0", "PSRPARAM.F0", "SUBINT.TBIN")


def test_psrparam_lookup_without_the_table_is_none(tmp_path):
    with fits.open(_write_folded(tmp_path / "noparam.sf", psrparam=None), memmap=False) as hdul:
        assert _psrparam_float(hdul, "P0", "F0") is None


# Channel frequencies ---------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"dat_freq": None},
        {"dat_freq": None, "chan_bw": None},
        {"dat_freq": CHANNEL_FREQS_MHZ[:-1]},  # wrong length: ignored
        {"dat_freq": np.where(np.arange(NCHAN) == 3, np.nan, CHANNEL_FREQS_MHZ)},  # non-finite: ignored
    ],
    ids=["dat-freq", "obsfreq-chan-bw", "obsfreq-obsbw", "short-dat-freq", "nan-dat-freq"],
)
def test_channel_frequencies_agree_across_header_sources(tmp_path, kwargs):
    _, freqs = _period_and_freqs(_write_folded(tmp_path / "freqs.sf", **kwargs))
    np.testing.assert_allclose(freqs, CHANNEL_FREQS_MHZ, rtol=0, atol=1e-9)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dat_freq": None, "obsfreq": None},
        {"dat_freq": None, "chan_bw": None, "obsbw": None},
        {"dat_freq": None, "chan_bw": 0.0},
    ],
    ids=["no-obsfreq", "no-bandwidth", "zero-chan-bw"],
)
def test_channel_frequencies_without_a_source_raise(tmp_path, kwargs):
    with pytest.raises(MetadataMissingError) as excinfo:
        _period_and_freqs(_write_folded(tmp_path / "nofreqs.sf", **kwargs))
    assert excinfo.value.fields == ("SUBINT.DAT_FREQ", "OBSFREQ", "SUBINT.CHAN_BW")


def test_folded_file_without_period_column_or_dat_freq_loads_identically(tmp_path):
    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)
    full_data, full = load_filterbank_data(_write_folded(tmp_path / "full.sf"), config)
    sparse_data, sparse = load_filterbank_data(
        _write_folded(tmp_path / "sparse.sf", period_column=None, dat_freq=None, chan_bw=None), config
    )
    np.testing.assert_allclose(sparse_data, full_data)
    np.testing.assert_allclose(sparse.freqs_mhz, full.freqs_mhz, atol=1e-9)
    assert full.tsamp == pytest.approx(TBIN_S)
    assert sparse.tsamp == pytest.approx(TBIN_S)
    # Without CHAN_BW the channel width comes from the channel spacing.
    assert full.freqres == pytest.approx(abs(CHAN_BW_MHZ))
    assert sparse.freqres == pytest.approx(abs(CHAN_BW_MHZ))
    assert sparse.bandwidth_mhz == pytest.approx(abs(CHAN_BW_MHZ) * NCHAN)
    assert sparse.start_mjd == pytest.approx(60000 + (3600 + 0.25) / 86400.0, abs=1e-12)


# Scales, offsets and raw products -------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, np.full((2, 3), 7.0)),
        (np.array([]), np.full((2, 3), 7.0)),
        (np.array([2.5]), np.full((2, 3), 2.5)),
        (np.array([1.0, 2.0, 3.0]), np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])),
        (np.arange(6.0), np.arange(6.0).reshape(2, 3)),
    ],
    ids=["missing", "empty", "scalar", "per-channel", "per-product"],
)
def test_scale_and_offset_columns_broadcast_to_products_and_channels(value, expected):
    result = _folded_psrfits_scale(value, npol=2, nchan=3, default=7.0, path=Path("x.sf"), field_name="DAT_SCL")
    np.testing.assert_array_equal(result, expected)


def test_scale_column_of_another_length_is_corrupt():
    with pytest.raises(CorruptedDataError, match="DAT_OFFS has 4 values; expected 1, 3, or 6"):
        _folded_psrfits_scale(np.arange(4.0), npol=2, nchan=3, default=0.0, path=Path("x.sf"), field_name="DAT_OFFS")


def test_raw_data_is_reshaped_or_rejected():
    single = np.arange(12.0).reshape(3, 4)
    np.testing.assert_array_equal(
        _folded_psrfits_raw_data({"DATA": single}, nbin=4, nchan=3, npol=1, path=Path("x.sf")), single[np.newaxis]
    )
    flat = np.arange(24.0)
    np.testing.assert_array_equal(
        _folded_psrfits_raw_data({"DATA": flat}, nbin=4, nchan=3, npol=2, path=Path("x.sf")), flat.reshape(2, 3, 4)
    )
    with pytest.raises(CorruptedDataError, match="has 23 values; expected 24"):
        _folded_psrfits_raw_data({"DATA": flat[:-1]}, nbin=4, nchan=3, npol=2, path=Path("x.sf"))
    with pytest.raises(MetadataMissingError) as excinfo:
        _folded_psrfits_raw_data({}, nbin=4, nchan=3, npol=2, path=Path("x.sf"))
    assert excinfo.value.fields == ("SUBINT.DATA",)


@pytest.mark.parametrize(
    ("npol", "order", "preset", "expected_total", "expected_npol"),
    [
        (1, "INTEN", None, 1.0, 1),
        (4, "IQUV", None, 1.0, 2),  # Stokes I is the first product
        (2, "AABB", None, 3.0, 2),  # AA + BB
        (4, "IQUV", "nrt", 3.0, 2),  # NRT labels coherency products IQUV
    ],
)
def test_folded_stokes_i_follows_the_polarization_products(npol, order, preset, expected_total, expected_npol):
    raw = np.stack([np.full((2, 3), float(index + 1)) for index in range(npol)])
    stokes_i, effective_npol = _build_folded_stokes_i(raw, order, preset_key=preset)
    np.testing.assert_array_equal(stokes_i, np.full((2, 3), expected_total))
    assert effective_npol == expected_npol
