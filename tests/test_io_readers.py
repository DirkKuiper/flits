"""Tests for the reader framework: detection, round-trip, corruption handling,
and format-hint override.
"""
from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from flits.io import detect_reader, inspect_filterbank, load_filterbank_data
from flits.io.errors import (
    CorruptedDataError,
    FormatDetectionError,
    MetadataMissingError,
    UnsupportedFormatError,
    UnsupportedSchemaError,
)
from flits.io.validation import validate_metadata
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig


_ALL_FORMATS = ["sigproc", "chime_hdf5", "psrfits", "chime_bbdata_beamformed"]


@pytest.mark.parametrize("synthetic_waterfall", _ALL_FORMATS, indirect=True)
def test_detect_reader_picks_correct_format(synthetic_waterfall):
    reader = detect_reader(synthetic_waterfall.path)
    expected = {
        "sigproc": "sigproc",
        "chime_hdf5": "chime_hdf5",
        "psrfits": "sigproc",  # shared YourFilterbankReader
        "chime_bbdata_beamformed": "chime_hdf5",
    }[synthetic_waterfall.format_id]
    assert reader.format_id == expected


@pytest.mark.parametrize("synthetic_waterfall", _ALL_FORMATS, indirect=True)
def test_round_trip_metadata(synthetic_waterfall):
    inspection = inspect_filterbank(synthetic_waterfall.path)
    target_dm = (
        synthetic_waterfall.coherent_dm
        if synthetic_waterfall.coherent_dm is not None
        else 0.0
    )
    config = ObservationConfig.from_preset(dm=target_dm, preset_key="generic", sefd_jy=1.0)
    data, metadata = load_filterbank_data(synthetic_waterfall.path, config, inspection=inspection)

    assert isinstance(metadata, FilterbankMetadata)
    assert metadata.tsamp == pytest.approx(synthetic_waterfall.tsamp_s, rel=1e-6)
    assert metadata.start_mjd == pytest.approx(synthetic_waterfall.tstart_mjd, rel=1e-9)
    assert metadata.freqres == pytest.approx(abs(synthetic_waterfall.foff_mhz), rel=1e-6)
    assert metadata.bandwidth_mhz == pytest.approx(
        abs(synthetic_waterfall.foff_mhz) * synthetic_waterfall.data.shape[0], rel=1e-6
    )
    assert data.shape[0] == synthetic_waterfall.data.shape[0]
    # The burst peak should survive dedispersion + normalization in the middle of the time axis.
    collapsed = data.mean(axis=0)
    peak_bin = int(np.argmax(collapsed))
    assert abs(peak_bin - synthetic_waterfall.burst_time_idx) < 5


@pytest.mark.parametrize("synthetic_waterfall", ["sigproc"], indirect=True)
def test_magic_byte_sniff_overrides_missing_extension(synthetic_waterfall, tmp_path):
    renamed = tmp_path / "no_extension_here"
    synthetic_waterfall.path.rename(renamed)
    reader = detect_reader(renamed)
    assert reader.format_id == "sigproc"


def test_unsupported_format_raises(tmp_path):
    junk = tmp_path / "junk.xyz"
    junk.write_bytes(b"this is definitely not a recognized format\n")
    with pytest.raises(UnsupportedFormatError):
        detect_reader(junk)


def test_generic_hdf5_container_is_not_claimed(tmp_path):
    path = tmp_path / "generic.h5"
    with h5py.File(path, "w") as fh:
        fh.create_dataset("data", data=np.zeros((4, 16), dtype=np.float32))
    with pytest.raises(UnsupportedFormatError):
        detect_reader(path)


def test_generic_fits_container_is_not_claimed(tmp_path):
    from astropy.io import fits

    path = tmp_path / "generic.fits"
    fits.PrimaryHDU(data=np.zeros((4, 4), dtype=np.float32)).writeto(path)
    with pytest.raises(UnsupportedFormatError):
        detect_reader(path)


@pytest.mark.parametrize("synthetic_waterfall", ["sigproc"], indirect=True)
def test_format_hint_on_matching_file_works(synthetic_waterfall):
    reader = detect_reader(synthetic_waterfall.path, format_hint="sigproc")
    assert reader.format_id == "sigproc"


@pytest.mark.parametrize("synthetic_waterfall", ["sigproc"], indirect=True)
def test_format_hint_contradicted_by_sniff_raises(synthetic_waterfall):
    with pytest.raises(FormatDetectionError):
        detect_reader(synthetic_waterfall.path, format_hint="chime_hdf5")


def test_format_hint_unknown_format_raises(tmp_path):
    junk = tmp_path / "junk.fil"
    junk.write_bytes(b"irrelevant")
    with pytest.raises(UnsupportedFormatError):
        detect_reader(junk, format_hint="definitely_not_a_format")


def test_hdf5_unsupported_schema_raises(tmp_path):
    path = tmp_path / "bad_schema.h5"
    with h5py.File(path, "w") as fh:
        fh.attrs["schema_version"] = "some_other_tool_v42"
        fh.attrs["tsamp_s"] = 1e-3
        fh.attrs["fch1_mhz"] = 1500.0
        fh.attrs["foff_mhz"] = -1.0
        fh.attrs["tstart_mjd"] = 60000.0
        fh.create_dataset("wfall", data=np.zeros((4, 16), dtype=np.float32))

    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)
    with pytest.raises(UnsupportedSchemaError):
        load_filterbank_data(path, config)


def test_hdf5_missing_required_attr_raises(tmp_path):
    path = tmp_path / "missing_attrs.h5"
    with h5py.File(path, "w") as fh:
        fh.attrs["schema_version"] = "flits_chime_v1"
        # Intentionally omit tsamp_s, fch1_mhz, foff_mhz, tstart_mjd.
        fh.create_dataset("wfall", data=np.zeros((4, 16), dtype=np.float32))

    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)
    with pytest.raises(MetadataMissingError) as excinfo:
        load_filterbank_data(path, config)
    assert set(excinfo.value.fields) >= {"tsamp_s", "fch1_mhz", "foff_mhz", "tstart_mjd"}


def test_validate_metadata_rejects_bad_tsamp(tmp_path):
    metadata = FilterbankMetadata(
        source_path=tmp_path / "synthetic.fil",
        source_name="test",
        tsamp=0.0,  # invalid
        freqres=1.0,
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=64.0,
        npol=1,
        freqs_mhz=np.array([1500.0, 1499.0, 1498.0, 1497.0]),
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="test",
    )
    with pytest.raises(CorruptedDataError):
        validate_metadata(metadata)


def test_validate_metadata_rejects_non_monotonic_freqs(tmp_path):
    metadata = FilterbankMetadata(
        source_path=tmp_path / "synthetic.fil",
        source_name="test",
        tsamp=1e-3,
        freqres=1.0,
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=64.0,
        npol=1,
        freqs_mhz=np.array([1500.0, 1502.0, 1501.0, 1503.0]),
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="test",
    )
    with pytest.raises(CorruptedDataError):
        validate_metadata(metadata)


def test_validate_metadata_accepts_descending_freqs(tmp_path):
    metadata = FilterbankMetadata(
        source_path=tmp_path / "synthetic.fil",
        source_name="test",
        tsamp=1e-3,
        freqres=1.0,
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=10.0,
        bandwidth_mhz=64.0,
        npol=1,
        freqs_mhz=np.array([1503.0, 1502.0, 1501.0, 1500.0]),
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="test",
    )
    # Ascending and descending are both valid; order is preserved.
    out = validate_metadata(metadata)
    assert out.freqs_mhz[0] > out.freqs_mhz[-1]


def test_corrupted_hdf5_file_raises(tmp_path):
    bad = tmp_path / "corrupted.h5"
    bad.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 32)  # HDF5 magic but garbage body

    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic", sefd_jy=1.0)
    with pytest.raises(CorruptedDataError):
        load_filterbank_data(bad, config)


@pytest.mark.parametrize("synthetic_waterfall", ["chime_hdf5"], indirect=True)
def test_chime_flits_schema_detected_as_chime_preset(synthetic_waterfall):
    inspection = inspect_filterbank(synthetic_waterfall.path)
    assert inspection.detected_preset_key == "chime"
    assert "flits_chime_v1" in inspection.detection_basis
    assert inspection.schema_version == "flits_chime_v1"


def test_chime_catalog_schema_detected_as_chime_preset(tmp_path):
    path = tmp_path / "catalog.h5"
    nchan, ntime = 16, 64
    with h5py.File(path, "w") as fh:
        frb = fh.create_group("frb")
        frb.attrs["tns_name"] = "FRB20180729A"
        frb.create_dataset("extent", data=np.array([0.0, 0.1, 400.0, 800.0]))
        frb.create_dataset("plot_freq", data=np.linspace(400.0, 800.0, nchan))
        frb.create_dataset(
            "calibrated_wfall",
            data=np.zeros((nchan, ntime), dtype=np.float32),
        )

    inspection = inspect_filterbank(path)
    assert inspection.detected_preset_key == "chime"
    assert "chime_frb_catalog_v1" in inspection.detection_basis
    assert inspection.schema_version == "chime_frb_catalog_v1"


@pytest.mark.parametrize("synthetic_waterfall", ["chime_bbdata_beamformed"], indirect=True)
def test_chime_bbdata_schema_detected_as_chime_preset(synthetic_waterfall):
    inspection = inspect_filterbank(synthetic_waterfall.path)
    assert inspection.detected_preset_key == "chime"
    assert inspection.schema_version == "chime_bbdata_beamformed_v1"
    assert inspection.coherent_dm == pytest.approx(synthetic_waterfall.coherent_dm, rel=1e-6)
    assert "chime_bbdata_beamformed_v1" in inspection.detection_basis


def test_chime_telescope_name_attr_detected_without_schema(tmp_path):
    path = tmp_path / "nameonly.h5"
    with h5py.File(path, "w") as fh:
        fh.attrs["telescope_name"] = "CHIME"
        fh.attrs["tsamp_s"] = 1e-3
        fh.attrs["fch1_mhz"] = 1500.0
        fh.attrs["foff_mhz"] = -1.0
        fh.attrs["tstart_mjd"] = 60000.0
        fh.attrs["nchan"] = 4
        fh.create_dataset("wfall", data=np.zeros((4, 16), dtype=np.float32))

    inspection = inspect_filterbank(path)
    assert inspection.detected_preset_key == "chime"
    assert "CHIME" in inspection.detection_basis


def test_chime_frequency_band_fallback_without_name_or_schema(tmp_path):
    path = tmp_path / "bandonly.h5"
    nchan = 16
    with h5py.File(path, "w") as fh:
        fh.attrs["tsamp_s"] = 1e-3
        fh.attrs["fch1_mhz"] = 400.0
        fh.attrs["foff_mhz"] = 25.0  # 400 + 25*15 = 775 MHz → inside 400–800
        fh.attrs["tstart_mjd"] = 60000.0
        fh.attrs["nchan"] = nchan
        fh.create_dataset("wfall", data=np.zeros((nchan, 16), dtype=np.float32))

    inspection = inspect_filterbank(path)
    assert inspection.detected_preset_key == "generic"
    assert inspection.detection_basis == "no matching telescope hints"


@pytest.mark.parametrize("synthetic_waterfall", ["chime_bbdata_beamformed"], indirect=True)
def test_chime_bbdata_uses_residual_dm_after_coherent_alignment(synthetic_waterfall):
    inspection = inspect_filterbank(synthetic_waterfall.path)
    coherent_dm = float(synthetic_waterfall.coherent_dm)

    matched_config = ObservationConfig.from_preset(dm=coherent_dm, preset_key="generic", sefd_jy=1.0)
    shifted_config = ObservationConfig.from_preset(dm=coherent_dm + 20.0, preset_key="generic", sefd_jy=1.0)

    matched_data, _ = load_filterbank_data(synthetic_waterfall.path, matched_config, inspection=inspection)
    shifted_data, _ = load_filterbank_data(synthetic_waterfall.path, shifted_config, inspection=inspection)

    matched_profile = matched_data.mean(axis=0)
    shifted_profile = shifted_data.mean(axis=0)
    assert int(np.argmax(matched_profile)) == pytest.approx(synthetic_waterfall.burst_time_idx, abs=5)
    assert float(np.max(matched_profile)) > float(np.max(shifted_profile))
    assert not np.allclose(matched_data, shifted_data, equal_nan=True)


def test_chime_bbdata_non_polarization_beam_axis_raises(tmp_path):
    path = tmp_path / "bad_beamformed.h5"
    nchan, ntime = 8, 64
    power = np.zeros((nchan, 2, ntime), dtype=np.float32)

    freq_dtype = np.dtype([("centre", "<f8"), ("id", "<u4")])
    time0_dtype = np.dtype([("fpga_count", "<u8"), ("ctime", "<f8"), ("ctime_offset", "<f8")])
    loc_dtype = np.dtype(
        [("ra", "<f8"), ("dec", "<f8"), ("x_400MHz", "<f8"), ("y_400MHz", "<f8"), ("pol", "S1")]
    )

    freq_table = np.zeros(nchan, dtype=freq_dtype)
    freq_table["centre"] = np.linspace(445.0, 400.0, nchan)
    freq_table["id"] = np.arange(nchan, dtype=np.uint32)
    time0_table = np.zeros(nchan, dtype=time0_dtype)
    time0_table["ctime"] = 1.7e9
    bad_locations = np.zeros(2, dtype=loc_dtype)
    bad_locations["ra"] = [10.0, 11.0]
    bad_locations["dec"] = [20.0, 20.0]
    bad_locations["x_400MHz"] = [0.0, 1.0]
    bad_locations["y_400MHz"] = [0.0, 0.0]
    bad_locations["pol"] = [b"S", b"E"]

    with h5py.File(path, "w") as fh:
        fh.attrs["__memh5_subclass"] = "baseband_analysis.core.bbdata.BBData"
        fh.attrs["delta_time"] = 2.56e-6
        fh.create_dataset("tiedbeam_power", data=power)
        fh["tiedbeam_power"].attrs["DM_coherent"] = 50.0
        fh.create_dataset("time0", data=time0_table)
        fh.create_dataset("tiedbeam_locations", data=bad_locations)
        index_map = fh.create_group("index_map")
        index_map.create_dataset("freq", data=freq_table)

    config = ObservationConfig.from_preset(dm=50.0, preset_key="generic", sefd_jy=1.0)
    with pytest.raises(CorruptedDataError, match="multi-beam layouts are not yet supported"):
        load_filterbank_data(path, config)


def test_corrupt_optional_hdf5_metadata_raises_reader_error(tmp_path):
    path = tmp_path / "bad_optional_meta.h5"
    with h5py.File(path, "w") as fh:
        fh.attrs["schema_version"] = "flits_chime_v1"
        fh.attrs["tsamp_s"] = 1e-3
        fh.attrs["fch1_mhz"] = 1500.0
        fh.attrs["foff_mhz"] = -1.0
        fh.attrs["tstart_mjd"] = 60000.0
        fh.attrs["telescope_id"] = "not_an_int"
        fh.create_dataset("wfall", data=np.zeros((4, 16), dtype=np.float32))

    with pytest.raises(CorruptedDataError, match="telescope_id"):
        inspect_filterbank(path)


def test_chime_catalog_load_does_not_rededisperse(tmp_path):
    path = tmp_path / "catalog.h5"
    nchan, ntime = 16, 64
    waterfall = np.zeros((nchan, ntime), dtype=np.float32)
    waterfall[:, 32] = 1.0
    with h5py.File(path, "w") as fh:
        frb = fh.create_group("frb")
        frb.attrs["tns_name"] = "FRB20180729A"
        frb.create_dataset("extent", data=np.array([0.0, 0.064, 400.0, 800.0]))
        frb.create_dataset("plot_freq", data=np.linspace(400.0, 800.0, nchan))
        frb.create_dataset("calibrated_wfall", data=waterfall)

    inspection = inspect_filterbank(path)
    config = ObservationConfig.from_preset(dm=400.0, preset_key="generic", sefd_jy=1.0)
    data, _ = load_filterbank_data(path, config, inspection=inspection)
    assert int(np.argmax(data.mean(axis=0))) == 32
