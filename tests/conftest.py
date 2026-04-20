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
_MJD_EPOCH = datetime.datetime(1858, 11, 17, tzinfo=datetime.timezone.utc)


@dataclass(frozen=True)
class SyntheticWaterfall:
    path: Path
    format_id: str
    data: np.ndarray            # (nchan, ntime) pre-dedispersion Stokes-I
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
    burst_profile = burst_amp * np.exp(-((t - burst_time_idx) ** 2) / (2 * burst_width_bins ** 2))
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
    loc_dtype = np.dtype(
        [("ra", "<f8"), ("dec", "<f8"), ("x_400MHz", "<f8"), ("y_400MHz", "<f8"), ("pol", "S1")]
    )

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
        tz=datetime.timezone.utc,
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
