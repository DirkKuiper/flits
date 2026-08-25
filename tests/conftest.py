"""Shared pytest fixtures — most notably `synthetic_waterfall` which produces a
known burst in any of the three supported input formats.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

_DM_CONSTANT = 1 / (2.41 * 10**-4)
_MJD_EPOCH = datetime.datetime(1858, 11, 17, tzinfo=datetime.UTC)


@dataclass(frozen=True)
class SyntheticWaterfall:
    path: Path
    format_id: str
    data: np.ndarray  # (nchan, ntime) pre-dedispersion Stokes-I
    tsamp_s: float
    fch1_mhz: float
    foff_mhz: float
    tstart_mjd: float
    source_name: str
    telescope_id: int
    burst_time_idx: int
    burst_chan_idx: int
    coherent_dm: float | None = None


def _make_synthetic_array(
    nchan: int = 64,
    ntime: int = 256,
    burst_time_idx: int = 128,
    burst_width_bins: float = 2.5,
    burst_amp: float = 50.0,
    noise_std: float = 1.0,
    rng_seed: int = 42,
) -> np.ndarray:
    rng = np.random.default_rng(rng_seed)
    data = rng.normal(loc=0.0, scale=noise_std, size=(nchan, ntime)).astype(np.float32)
    t = np.arange(ntime)
    burst_profile = burst_amp * np.exp(-((t - burst_time_idx) ** 2) / (2 * burst_width_bins**2))
    data += burst_profile[np.newaxis, :].astype(np.float32)
    return data


def _mjd_to_unix(mjd: float) -> float:
    return _MJD_EPOCH.timestamp() + (float(mjd) * 86400.0)


def _shift_with_zero_fill(values: np.ndarray, shift_bins: int) -> np.ndarray:
    out = np.zeros(values.shape, dtype=np.float32)
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


def _write_sigproc(
    path: Path,
    data: np.ndarray,
    tsamp_s: float,
    fch1_mhz: float,
    foff_mhz: float,
    tstart_mjd: float,
    source_name: str,
    telescope_id: int,
) -> None:
    from flits.io.sigproc import SigprocFilterbankHeader, build_sigproc_filterbank_bytes

    header = SigprocFilterbankHeader(
        rawdatafile=path.name,
        source_name=source_name,
        nchans=int(data.shape[0]),
        foff=float(foff_mhz),
        fch1=float(fch1_mhz),
        tsamp=float(tsamp_s),
        tstart=float(tstart_mjd),
        telescope_id=int(telescope_id),
        machine_id=0,
        # Non-zero RA/DEC so downstream tools (e.g. your.Writer.to_fits) don't trip on None.
        src_raj=123456.78,
        src_dej=-123456.78,
        nbits=32,
        nifs=1,
    )
    path.write_bytes(build_sigproc_filterbank_bytes(data, header))


def _write_chime_hdf5(
    path: Path,
    data: np.ndarray,
    tsamp_s: float,
    fch1_mhz: float,
    foff_mhz: float,
    tstart_mjd: float,
    source_name: str,
    telescope_id: int,
) -> None:
    h5py = pytest.importorskip("h5py")
    with h5py.File(path, "w") as fh:
        fh.attrs["schema_version"] = "flits_chime_v1"
        fh.attrs["tsamp_s"] = float(tsamp_s)
        fh.attrs["fch1_mhz"] = float(fch1_mhz)
        fh.attrs["foff_mhz"] = float(foff_mhz)
        fh.attrs["tstart_mjd"] = float(tstart_mjd)
        fh.attrs["source_name"] = source_name
        fh.attrs["telescope_id"] = int(telescope_id)
        fh.attrs["npol"] = 1
        fh.attrs["nchan"] = int(data.shape[0])
        fh.create_dataset("wfall", data=data.astype(np.float32), chunks=True)


def _write_chime_bbdata_beamformed(
    path: Path,
    data: np.ndarray,
    tsamp_s: float,
    fch1_mhz: float,
    foff_mhz: float,
    tstart_mjd: float,
    coherent_dm: float,
) -> None:
    h5py = pytest.importorskip("h5py")

    nchan, ntime = data.shape
    freqs = fch1_mhz + (foff_mhz * np.arange(nchan, dtype=float))
    freq_ref = float(np.max(freqs))
    dm_delay = _DM_CONSTANT * float(coherent_dm) * (freqs**-2.0 - freq_ref**-2.0)
    extra_offset_bins = (np.arange(nchan, dtype=int) % 5).astype(int)
    channel_start_sec = _mjd_to_unix(tstart_mjd) + dm_delay + (extra_offset_bins * tsamp_s)

    power = np.zeros((nchan, 2, ntime), dtype=np.float32)
    for chan in range(nchan):
        stored = _shift_with_zero_fill(np.asarray(data[chan, :], dtype=np.float32), -int(extra_offset_bins[chan]))
        power[chan, 0, :] = stored
        power[chan, 1, :] = stored

    freq_dtype = np.dtype([("centre", "<f8"), ("id", "<u4")])
    time0_dtype = np.dtype([("fpga_count", "<u8"), ("ctime", "<f8"), ("ctime_offset", "<f8")])
    loc_dtype = np.dtype([("ra", "<f8"), ("dec", "<f8"), ("x_400MHz", "<f8"), ("y_400MHz", "<f8"), ("pol", "S1")])

    freq_table = np.zeros(nchan, dtype=freq_dtype)
    freq_table["centre"] = freqs
    freq_table["id"] = np.arange(nchan, dtype=np.uint32)

    time0_table = np.zeros(nchan, dtype=time0_dtype)
    time0_table["ctime"] = channel_start_sec
    time0_table["ctime_offset"] = 0.0
    time0_table["fpga_count"] = np.arange(nchan, dtype=np.uint64)

    tiedbeam_locations = np.zeros(2, dtype=loc_dtype)
    tiedbeam_locations["ra"] = 123.4
    tiedbeam_locations["dec"] = 56.7
    tiedbeam_locations["x_400MHz"] = 1.0
    tiedbeam_locations["y_400MHz"] = 2.0
    tiedbeam_locations["pol"] = [b"S", b"E"]

    event_dt = datetime.datetime.fromtimestamp(
        float(np.max(channel_start_sec)),
        tz=datetime.UTC,
    ).strftime("%Y-%m-%dT %H:%M:%S.%f")

    with h5py.File(path, "w") as fh:
        fh.attrs["__memh5_subclass"] = "baseband_analysis.core.bbdata.BBData"
        fh.attrs["delta_time"] = float(tsamp_s)
        fh.attrs["event_date"] = event_dt
        fh.create_dataset("tiedbeam_power", data=power)
        fh["tiedbeam_power"].attrs["DM_coherent"] = float(coherent_dm)
        fh["tiedbeam_power"].attrs["axis"] = np.array(["freq", "beam", "time"], dtype="S8")
        fh.create_dataset("time0", data=time0_table)
        index_map = fh.create_group("index_map")
        index_map.create_dataset("freq", data=freq_table)
        fh.create_dataset("tiedbeam_locations", data=tiedbeam_locations)


def _write_psrfits(
    path: Path,
    sigproc_path: Path,
) -> None:
    """Convert an existing SIGPROC .fil to PSRFITS using your.Writer.

    `your` has no from-scratch PSRFITS builder, so we bootstrap from the .fil
    fixture. The written file is a valid search-mode PSRFITS.
    """
    your_pkg = pytest.importorskip("your")
    try:
        reader = your_pkg.Your(str(sigproc_path))
    except Exception as exc:
        pytest.skip(f"your could not open fixture .fil: {exc}")
    try:
        writer = your_pkg.Writer(
            reader,
            outdir=str(path.parent),
            outname=path.stem,
            progress=False,
        )
        writer.to_fits()
    finally:
        fp = getattr(reader, "fp", None)
        if fp is not None and not getattr(fp, "closed", True):
            fp.close()

    produced = path.parent / f"{path.stem}.fits"
    if not produced.exists():
        pytest.skip("your.Writer.to_fits() did not produce the expected .fits file")
    if produced != path:
        produced.rename(path)


def _write_folded_psrfits(
    path: Path,
    data: np.ndarray,
    tsamp_s: float,
    fch1_mhz: float,
    foff_mhz: float,
    tstart_mjd: float,
    source_name: str,
) -> None:
    fits = pytest.importorskip("astropy.io.fits")

    nchan, nbin = data.shape
    npol = 1
    stt_imjd = int(tstart_mjd)
    stt_seconds = (float(tstart_mjd) - stt_imjd) * 86400.0
    stt_smjd = int(stt_seconds)
    stt_offs = stt_seconds - stt_smjd

    packed = np.rint(data).astype(">i2").reshape(1, npol, nchan, nbin)
    freqs = fch1_mhz + (foff_mhz * np.arange(nchan, dtype=float))
    period = float(tsamp_s) * nbin

    primary = fits.PrimaryHDU()
    primary.header["FITSTYPE"] = "PSRFITS"
    primary.header["OBS_MODE"] = "PSR"
    primary.header["SRC_NAME"] = source_name
    primary.header["TELESCOP"] = "SYNTH"
    primary.header["OBSFREQ"] = float(np.mean(freqs))
    primary.header["OBSBW"] = float(foff_mhz * nchan)
    primary.header["OBSNCHAN"] = nchan
    primary.header["STT_IMJD"] = stt_imjd
    primary.header["STT_SMJD"] = stt_smjd
    primary.header["STT_OFFS"] = stt_offs
    primary.header["RAJ"] = "12:34:56.78"
    primary.header["DECJ"] = "-12:34:56.78"

    subint = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="PERIOD", format="D", array=np.array([period])),
            fits.Column(name="DAT_FREQ", format=f"{nchan}D", array=freqs.reshape(1, nchan)),
            fits.Column(name="DAT_WTS", format=f"{nchan}E", array=np.ones((1, nchan), dtype=">f4")),
            fits.Column(name="DAT_OFFS", format=f"{nchan}E", array=np.zeros((1, nchan), dtype=">f4")),
            fits.Column(name="DAT_SCL", format=f"{nchan}E", array=np.ones((1, nchan), dtype=">f4")),
            fits.Column(
                name="DATA",
                format=f"{npol * nchan * nbin}I",
                dim=f"({nbin},{nchan},{npol})",
                array=packed,
            ),
        ],
        name="SUBINT",
    )
    subint.header["NBIN"] = nbin
    subint.header["NCHAN"] = nchan
    subint.header["NPOL"] = npol
    subint.header["TBIN"] = float(tsamp_s)
    subint.header["CHAN_BW"] = float(foff_mhz)
    subint.header["POL_TYPE"] = "INTEN"

    psrparam = fits.BinTableHDU.from_columns(
        [
            fits.Column(
                name="PARAM",
                format="96A",
                array=np.array(
                    [
                        f"PSRJ {source_name}",
                        f"P0 {period:.12g}",
                        "DM 0.0",
                    ],
                    dtype="S96",
                ),
            )
        ],
        name="PSRPARAM",
    )
    fits.HDUList([primary, psrparam, subint]).writeto(path)


@dataclass(frozen=True)
class FullStokesWaterfall:
    """A four-IF SIGPROC filterbank holding a burst with a known rotation measure."""

    path: Path
    rm_rad_m2: float
    linear_fraction: float
    intrinsic_angle_rad: float
    nchan: int
    ntime: int
    burst_time_idx: int
    fch1_mhz: float
    foff_mhz: float
    tsamp_s: float


_STOKES_NCHAN = 64
_STOKES_NTIME = 512
_STOKES_BURST_BIN = 256
_STOKES_FCH1_MHZ = 1500.0
_STOKES_FOFF_MHZ = -4.0
_STOKES_TSAMP_S = 1e-3
_C_M_S = 299_792_458.0


def _linear_feed_products(
    *,
    rm_rad_m2: float,
    linear_fraction: float,
    intrinsic_angle_rad: float,
    rng_seed: int,
) -> np.ndarray:
    """Coherency products AA/BB/CR/CI for a Faraday-rotated linear burst."""
    rng = np.random.default_rng(rng_seed)
    freqs = _STOKES_FCH1_MHZ + _STOKES_FOFF_MHZ * np.arange(_STOKES_NCHAN, dtype=float)
    lambda_sq = (_C_M_S / (freqs * 1e6)) ** 2

    times = np.arange(_STOKES_NTIME, dtype=float)
    profile = np.exp(-((times - _STOKES_BURST_BIN) ** 2) / (2 * 3.0**2))
    stokes_i = 40.0 * np.ones(_STOKES_NCHAN)[:, None] * profile[None, :]

    angle = rm_rad_m2 * lambda_sq + intrinsic_angle_rad
    stokes_q = linear_fraction * stokes_i * np.cos(2.0 * angle)[:, None]
    stokes_u = linear_fraction * stokes_i * np.sin(2.0 * angle)[:, None]
    stokes_v = 0.05 * stokes_i

    # A linear feed records AA=(I+Q)/2, BB=(I-Q)/2, CR=U/2, CI=V/2, on a positive
    # system-temperature pedestal with radiometer noise.
    def noise() -> np.ndarray:
        return rng.normal(0.0, 1.0, size=(_STOKES_NCHAN, _STOKES_NTIME))

    return np.stack(
        [
            100.0 + 0.5 * (stokes_i + stokes_q) + noise(),
            100.0 + 0.5 * (stokes_i - stokes_q) + noise(),
            0.5 * stokes_u + noise(),
            0.5 * stokes_v + noise(),
        ]
    ).astype(np.float32)


def write_full_stokes_filterbank(
    path: Path,
    *,
    telescope_id: int = 3,
    rm_rad_m2: float = 137.5,
    linear_fraction: float = 0.8,
    intrinsic_angle_rad: float = 0.4,
    rng_seed: int = 7,
) -> FullStokesWaterfall:
    """Write a four-IF filterbank and describe the burst that was injected.

    `telescope_id` 3 selects the NRT preset, which declares the linear-feed
    coherency basis; 0 selects the generic preset, which declares none, and is
    how tests reach the "basis unknown" path.
    """
    from flits.io.sigproc import SigprocFilterbankHeader, build_sigproc_filterbank_bytes

    products = _linear_feed_products(
        rm_rad_m2=rm_rad_m2,
        linear_fraction=linear_fraction,
        intrinsic_angle_rad=intrinsic_angle_rad,
        rng_seed=rng_seed,
    )
    header = SigprocFilterbankHeader(
        rawdatafile=path.name,
        source_name="STOKES_TEST",
        nchans=_STOKES_NCHAN,
        foff=_STOKES_FOFF_MHZ,
        fch1=_STOKES_FCH1_MHZ,
        tsamp=_STOKES_TSAMP_S,
        tstart=60000.0,
        telescope_id=int(telescope_id),
        machine_id=0,
        src_raj=123456.78,
        src_dej=-123456.78,
        nbits=32,
        nifs=4,
    )
    path.write_bytes(build_sigproc_filterbank_bytes(products, header))
    return FullStokesWaterfall(
        path=path,
        rm_rad_m2=rm_rad_m2,
        linear_fraction=linear_fraction,
        intrinsic_angle_rad=intrinsic_angle_rad,
        nchan=_STOKES_NCHAN,
        ntime=_STOKES_NTIME,
        burst_time_idx=_STOKES_BURST_BIN,
        fch1_mhz=_STOKES_FCH1_MHZ,
        foff_mhz=_STOKES_FOFF_MHZ,
        tsamp_s=_STOKES_TSAMP_S,
    )


def write_full_stokes_folded_psrfits(
    path: Path,
    *,
    feed_polarization: str | None = "LIN",
    pol_type: str = "AABBCRCI",
    rm_rad_m2: float = 137.5,
    linear_fraction: float = 0.8,
    intrinsic_angle_rad: float = 0.4,
    rng_seed: int = 11,
) -> FullStokesWaterfall:
    """Write a four-polarization folded PSRFITS whose header names the basis.

    This is the one input format that can settle the polarization basis on its
    own, through ``POL_TYPE`` and ``FD_POLN``. Pass ``feed_polarization=None`` to
    write the ambiguous file that names ``AABBCRCI`` without saying which feed
    produced it.
    """
    fits = pytest.importorskip("astropy.io.fits")

    products = _linear_feed_products(
        rm_rad_m2=rm_rad_m2,
        linear_fraction=linear_fraction,
        intrinsic_angle_rad=intrinsic_angle_rad,
        rng_seed=rng_seed,
    )
    npol, nchan, nbin = products.shape
    freqs = _STOKES_FCH1_MHZ + _STOKES_FOFF_MHZ * np.arange(nchan, dtype=float)
    period = float(_STOKES_TSAMP_S) * nbin
    tstart_mjd = 60000.0
    stt_imjd = int(tstart_mjd)
    stt_seconds = (tstart_mjd - stt_imjd) * 86400.0

    primary = fits.PrimaryHDU()
    primary.header["FITSTYPE"] = "PSRFITS"
    primary.header["OBS_MODE"] = "PSR"
    primary.header["SRC_NAME"] = "STOKES_FOLD_TEST"
    primary.header["TELESCOP"] = "SYNTH"
    primary.header["OBSFREQ"] = float(np.mean(freqs))
    primary.header["OBSBW"] = float(_STOKES_FOFF_MHZ * nchan)
    primary.header["OBSNCHAN"] = nchan
    primary.header["STT_IMJD"] = stt_imjd
    primary.header["STT_SMJD"] = int(stt_seconds)
    primary.header["STT_OFFS"] = stt_seconds - int(stt_seconds)
    primary.header["RAJ"] = "12:34:56.78"
    primary.header["DECJ"] = "-12:34:56.78"
    if feed_polarization is not None:
        primary.header["FD_POLN"] = feed_polarization

    subint = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="PERIOD", format="D", array=np.array([period])),
            fits.Column(name="DAT_FREQ", format=f"{nchan}D", array=freqs.reshape(1, nchan)),
            fits.Column(name="DAT_OFFS", format=f"{npol * nchan}E", array=np.zeros((1, npol * nchan), dtype=">f4")),
            fits.Column(name="DAT_SCL", format=f"{npol * nchan}E", array=np.ones((1, npol * nchan), dtype=">f4")),
            fits.Column(
                name="DATA",
                format=f"{npol * nchan * nbin}I",
                dim=f"({nbin},{nchan},{npol})",
                array=np.rint(products).astype(">i2").reshape(1, npol, nchan, nbin),
            ),
        ],
        name="SUBINT",
    )
    subint.header["NBIN"] = nbin
    subint.header["NCHAN"] = nchan
    subint.header["NPOL"] = npol
    subint.header["TBIN"] = float(_STOKES_TSAMP_S)
    subint.header["CHAN_BW"] = float(_STOKES_FOFF_MHZ)
    subint.header["POL_TYPE"] = pol_type

    fits.HDUList([primary, subint]).writeto(path)
    return FullStokesWaterfall(
        path=path,
        rm_rad_m2=rm_rad_m2,
        linear_fraction=linear_fraction,
        intrinsic_angle_rad=intrinsic_angle_rad,
        nchan=nchan,
        ntime=nbin,
        burst_time_idx=_STOKES_BURST_BIN,
        fch1_mhz=_STOKES_FCH1_MHZ,
        foff_mhz=_STOKES_FOFF_MHZ,
        tsamp_s=_STOKES_TSAMP_S,
    )


@pytest.fixture
def full_stokes_waterfall(tmp_path: Path) -> FullStokesWaterfall:
    """A four-IF burst whose rotation measure is known exactly."""
    return write_full_stokes_filterbank(tmp_path / "full_stokes.fil")


@pytest.fixture
def synthetic_waterfall(request, tmp_path: Path) -> SyntheticWaterfall:
    """Generate a synthetic burst and materialize it in the requested format.

    Parametrize via `@pytest.mark.parametrize("synthetic_waterfall", ["sigproc",
    "psrfits", "chime_hdf5", "chime_bbdata_beamformed"], indirect=True)`.
    """
    format_id = getattr(request, "param", "sigproc")

    nchan, ntime = 64, 256
    burst_time_idx = 128
    burst_chan_idx = nchan // 2
    tsamp_s = 1e-3
    fch1_mhz = 1500.0
    foff_mhz = -1.0
    tstart_mjd = 60000.0
    source_name = "TEST_BURST"
    telescope_id = 0
    coherent_dm = 50.0

    data = _make_synthetic_array(
        nchan=nchan,
        ntime=ntime,
        burst_time_idx=burst_time_idx,
    )

    sigproc_path = tmp_path / "synthetic.fil"
    _write_sigproc(
        sigproc_path,
        data,
        tsamp_s=tsamp_s,
        fch1_mhz=fch1_mhz,
        foff_mhz=foff_mhz,
        tstart_mjd=tstart_mjd,
        source_name=source_name,
        telescope_id=telescope_id,
    )

    if format_id == "sigproc":
        path = sigproc_path
    elif format_id == "chime_hdf5":
        path = tmp_path / "synthetic.h5"
        _write_chime_hdf5(
            path,
            data,
            tsamp_s=tsamp_s,
            fch1_mhz=fch1_mhz,
            foff_mhz=foff_mhz,
            tstart_mjd=tstart_mjd,
            source_name=source_name,
            telescope_id=telescope_id,
        )
    elif format_id == "psrfits":
        path = tmp_path / "synthetic.fits"
        _write_psrfits(path, sigproc_path)
    elif format_id == "psrfits_fold":
        path = tmp_path / "synthetic_fold.fits"
        _write_folded_psrfits(
            path,
            data,
            tsamp_s=tsamp_s,
            fch1_mhz=fch1_mhz,
            foff_mhz=foff_mhz,
            tstart_mjd=tstart_mjd,
            source_name=source_name,
        )
    elif format_id == "chime_bbdata_beamformed":
        path = tmp_path / "synthetic_beamformed.h5"
        _write_chime_bbdata_beamformed(
            path,
            data,
            tsamp_s=tsamp_s,
            fch1_mhz=fch1_mhz,
            foff_mhz=foff_mhz,
            tstart_mjd=tstart_mjd,
            coherent_dm=coherent_dm,
        )
    else:
        raise ValueError(f"Unsupported synthetic_waterfall format: {format_id!r}")

    return SyntheticWaterfall(
        path=path,
        format_id=format_id,
        data=data,
        tsamp_s=tsamp_s,
        fch1_mhz=fch1_mhz,
        foff_mhz=foff_mhz,
        tstart_mjd=tstart_mjd,
        source_name=source_name,
        telescope_id=telescope_id,
        burst_time_idx=burst_time_idx,
        burst_chan_idx=burst_chan_idx,
        coherent_dm=coherent_dm if format_id == "chime_bbdata_beamformed" else None,
    )
