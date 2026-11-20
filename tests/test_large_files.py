"""Large files: estimate the load from the header, refuse what will not fit, load only the burst."""

from __future__ import annotations

import dataclasses
import json
import tracemalloc

import numpy as np
import pytest
from conftest import write_dispersed_filterbank
from jsonschema import Draft202012Validator

from flits.cli import main
from flits.inspection import inspect_burst
from flits.io import inspect_filterbank, load_stokes_data
from flits.io import load_budget as load_budget_module
from flits.io.errors import LoadTooLargeError
from flits.io.load_budget import (
    MAX_LOAD_ENV_VAR,
    burst_read_window,
    estimate_load,
    load_budget_bytes,
)
from flits.io.search_data import SigprocFile
from flits.manifest import load_measure_manifest
from flits.measure import MeasureOptions, measure_burst
from flits.schema import get_schema
from flits.session import BurstSession
from flits.settings import ObservationConfig

# Dispersion sweep across 1248-1500 MHz at DM 300, the default dispersed burst.
SWEEP_S = 0.24598566132239927


@pytest.fixture(autouse=True)
def _no_budget_from_the_environment(monkeypatch):
    monkeypatch.delenv(MAX_LOAD_ENV_VAR, raising=False)


@pytest.fixture
def dispersed(tmp_path):
    return write_dispersed_filterbank(tmp_path / "dispersed.fil")


# ---------------------------------------------------------------- estimate


@pytest.mark.parametrize(
    ("synthetic_waterfall", "nsamples", "nbits", "windowed"),
    [
        ("sigproc", 256, 32, True),
        ("psrfits", 256, 32, True),
        ("chime_hdf5", 256, 32, True),
        ("chime_bbdata_beamformed", 256, 32, False),
        ("psrfits_fold", 256, 16, False),
    ],
    indirect=["synthetic_waterfall"],
)
def test_every_reader_reports_its_layout(synthetic_waterfall, nsamples, nbits, windowed):
    inspection = inspect_filterbank(synthetic_waterfall.path)

    assert inspection.nchans == 64
    assert inspection.nsamples == nsamples
    assert inspection.tsamp_s == pytest.approx(1e-3)
    assert inspection.duration_s == pytest.approx(nsamples * 1e-3)
    assert inspection.nbits == nbits
    assert inspection.windowed_read is windowed
    assert estimate_load(inspection) is not None


def test_inspect_reports_size_and_load_estimate(synthetic_waterfall, monkeypatch):
    monkeypatch.setenv(MAX_LOAD_ENV_VAR, "2")
    record = inspect_burst(synthetic_waterfall.path)

    assert record["duration_s"] == pytest.approx(0.256)
    assert record["nsamples"] == 256
    assert record["bytes_on_disk"] == synthetic_waterfall.path.stat().st_size
    assert record["metadata"]["data_bytes"] == 64 * 256 * 4
    # 256 samples x (64 channels x 4 raw bytes + 1.25 float32 copies x 4 bytes x 64 channels)
    assert record["estimated_load_bytes"] == 147_456
    assert record["load_budget_bytes"] == 2_000_000_000
    # 2e9 bytes / 576 bytes per sample, in 1 ms samples
    assert record["max_window_s"] == pytest.approx(3_472_222 * 1e-3)
    Draft202012Validator(get_schema("inspection")).validate(record)


def test_inspect_cli_records_validate_against_the_schema(synthetic_waterfall, tmp_path, capsys):
    missing = tmp_path / "missing.fil"
    assert main(["inspect", str(synthetic_waterfall.path), str(missing)]) == 1
    records = json.loads(capsys.readouterr().out)
    validator = Draft202012Validator(get_schema("inspection"))
    for record in records:
        validator.validate(record)
    assert [record["status"] for record in records] == ["ok", "error"]


def test_a_window_scales_the_estimate(dispersed):
    inspection = inspect_filterbank(dispersed.path)
    whole = estimate_load(inspection)
    window = estimate_load(inspection, 10.0, 12.0)

    assert whole is not None and window is not None
    assert whole.nsamples == 20_000
    assert window.nsamples == 2_000
    assert window.bytes_read == 64 * 2_000 * 4
    assert window.estimated_load_bytes * 10 == whole.estimated_load_bytes
    stokes = estimate_load(inspection, 10.0, 12.0, stokes=True)
    assert stokes is not None and stokes.estimated_load_bytes > 4 * window.estimated_load_bytes


@pytest.mark.parametrize(("nbits", "nifs"), [(8, 1), (32, 1), (8, 2)])
def test_the_estimate_bounds_the_measured_peak(tmp_path, nbits, nifs):
    burst = write_dispersed_filterbank(
        tmp_path / "burst.fil",
        nchan=256,
        duration_s=10.0,
        burst_time_s=5.0,
        baseline=100.0,
        noise_std=8.0,
        nbits=nbits,
        nifs=nifs,
    )
    estimate = estimate_load(inspect_filterbank(burst.path))
    assert estimate is not None

    tracemalloc.start()
    try:
        BurstSession.from_file(str(burst.path), dm=burst.dm)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Conservative, but not so much that it refuses loads that would fit.
    assert peak <= estimate.estimated_load_bytes <= 1.4 * peak


# ---------------------------------------------------------------- budget


def test_budget_precedence(monkeypatch):
    monkeypatch.setattr(load_budget_module, "available_memory_bytes", lambda: 8_000_000_000)
    assert load_budget_bytes() == 4_000_000_000
    monkeypatch.setenv(MAX_LOAD_ENV_VAR, "1.5")
    assert load_budget_bytes() == 1_500_000_000
    assert load_budget_bytes(0.25) == 250_000_000
    assert load_budget_bytes(0) is None
    monkeypatch.setenv(MAX_LOAD_ENV_VAR, "0")
    assert load_budget_bytes() is None
    monkeypatch.setenv(MAX_LOAD_ENV_VAR, "lots")
    with pytest.raises(ValueError, match=MAX_LOAD_ENV_VAR):
        load_budget_bytes()


def test_an_oversized_load_is_refused_before_reading(dispersed, monkeypatch):
    def fail_read(self, nstart, nsamp):
        raise AssertionError("the file was read")

    monkeypatch.setattr(SigprocFile, "read", fail_read)

    with pytest.raises(LoadTooLargeError) as caught:
        BurstSession.from_file(str(dispersed.path), dm=dispersed.dm, max_load_gb=0.005)

    error = caught.value
    # 20000 samples x (64 x 4 + 1.25 x 4 x 64) bytes
    assert error.estimated_load_bytes == 11_520_000
    assert error.budget_bytes == 5_000_000
    # 5e6 bytes / 576 bytes per sample = 8680 samples of 1 ms
    assert error.max_window_sec == pytest.approx(8.68)
    message = str(error)
    assert "11.5 MB" in message and "5.0 MB" in message
    assert "--around" in message and "--read" in message and MAX_LOAD_ENV_VAR in message


def test_the_default_budget_follows_available_memory(dispersed, monkeypatch):
    monkeypatch.setattr(load_budget_module, "available_memory_bytes", lambda: 10_000_000)
    with pytest.raises(LoadTooLargeError):
        BurstSession.from_file(str(dispersed.path), dm=dispersed.dm)
    # A window that fits half of 10 MB loads.
    session = BurstSession.from_file(str(dispersed.path), dm=dispersed.dm, burst_time_sec=dispersed.burst_time_s)
    assert session.data.shape == (64, 1245)


def test_a_zero_budget_turns_the_check_off(dispersed, monkeypatch):
    monkeypatch.setattr(load_budget_module, "available_memory_bytes", lambda: 1)
    monkeypatch.setenv(MAX_LOAD_ENV_VAR, "0")
    session = BurstSession.from_file(str(dispersed.path), dm=dispersed.dm)
    assert session.data.shape == (64, 20_000)


def test_a_stokes_cube_is_checked_too(full_stokes_waterfall):
    config = ObservationConfig.from_preset(0.0, "nrt", polarization_basis="coherency_linear", max_load_gb=1e-6)
    with pytest.raises(LoadTooLargeError, match="Stokes I/Q/U/V cube"):
        load_stokes_data(full_stokes_waterfall.path, config)


# ---------------------------------------------------------------- burst window


def test_the_window_holds_the_dispersed_burst(dispersed):
    inspection = inspect_filterbank(dispersed.path)

    start, end = burst_read_window(inspection, dispersed.dm, dispersed.burst_time_s, pad_sec=0.5)

    assert start == pytest.approx(10.5)
    assert end == pytest.approx(11.5 + SWEEP_S)


def test_coherently_dedispersed_data_needs_only_the_residual_sweep(dispersed):
    inspection = dataclasses.replace(inspect_filterbank(dispersed.path), coherent_dm=300.0)

    assert burst_read_window(inspection, 300.0, 11.0, pad_sec=0.2) == pytest.approx((10.8, 11.2))
    # A negative residual DM moves the low channels earlier, so the window opens earlier.
    start, end = burst_read_window(inspection, 0.0, 11.0, pad_sec=0.2)
    assert (start, end) == pytest.approx((10.8 - SWEEP_S, 11.2))


@pytest.mark.parametrize(
    ("time", "pad", "message"),
    [(25.0, 0.5, "beyond the end"), (-1.0, 0.5, "non-negative"), (5.0, float("nan"), "padding")],
)
def test_an_impossible_burst_time_is_rejected(dispersed, time, pad, message):
    with pytest.raises(ValueError, match=message):
        burst_read_window(inspect_filterbank(dispersed.path), dispersed.dm, time, pad_sec=pad)


def test_a_burst_time_and_a_read_window_are_exclusive(dispersed):
    with pytest.raises(ValueError, match="burst_time_sec"):
        BurstSession.from_file(str(dispersed.path), dm=300.0, burst_time_sec=11.0, read_start_sec=1.0)
    with pytest.raises(ValueError, match="burst_pad_sec"):
        BurstSession.from_file(str(dispersed.path), dm=300.0, burst_pad_sec=1.0)


def test_loading_around_the_burst_measures_what_a_full_load_does(dispersed):
    # The default time downsampling grows with the window, so pin it to compare like with like.
    full = measure_burst(dispersed.path, MeasureOptions(dm=dispersed.dm, time_factor=1))
    around = measure_burst(
        dispersed.path, MeasureOptions(dm=dispersed.dm, time_factor=1, burst_time_s=dispersed.burst_time_s)
    )

    assert full["status"] == around["status"] == "ok"
    assert full["selection"]["read_window_s"] == pytest.approx([0.0, 20.0])
    assert around["selection"]["read_window_s"] == pytest.approx([10.5, 11.745])
    # Times stay in ms from the start of the file, whatever was loaded.
    assert full["selection"]["event_ms"] == pytest.approx([10990.0, 11009.0])
    # Localization sees less context in a short window; its edges move by a few samples.
    assert around["selection"]["event_ms"] == pytest.approx([10990.0, 11009.0], abs=5.0)
    # The off-pulse noise comes from a shorter stretch, so the S/N agrees to its scatter.
    assert full["measurements"]["snr_integrated"] == pytest.approx(42.13, abs=0.05)
    assert around["measurements"]["snr_integrated"] == pytest.approx(full["measurements"]["snr_integrated"], rel=0.03)
    assert around["measurements"]["snr_peak"] == pytest.approx(full["measurements"]["snr_peak"], rel=0.06)
    Draft202012Validator(get_schema("measurement")).validate(around)


def test_a_read_window_loads_only_that_span(dispersed):
    record = measure_burst(dispersed.path, MeasureOptions(dm=dispersed.dm, read_window_s=(10.0, 12.5)))

    assert record["status"] == "ok"
    assert record["selection"]["read_window_s"] == pytest.approx([10.0, 12.5])
    assert record["selection"]["event_ms"] == pytest.approx([10990.0, 11009.0], abs=5.0)


# ---------------------------------------------------------------- command line and manifest


def test_measure_around_from_the_command_line(dispersed, capsys):
    code = main(["measure", str(dispersed.path), "--dm", "300", "--around", "11", "--pad", "0.3", "--mask", "none"])

    assert code == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["selection"]["read_window_s"] == pytest.approx([10.7, 11.3 + SWEEP_S], abs=1e-3)


def test_measure_read_from_the_command_line(dispersed, capsys):
    assert main(["measure", str(dispersed.path), "--dm", "300", "--read", "10", "12"]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["selection"]["read_window_s"] == pytest.approx([10.0, 12.0])


@pytest.mark.parametrize(
    "flags",
    [
        ["--around", "11", "--read", "10", "12"],
        ["--pad", "0.3"],
        ["--read", "12", "10"],
        ["--around", "-1"],
        ["--max-load-gb", "inf"],
    ],
)
def test_inconsistent_window_flags_are_usage_errors(dispersed, flags):
    with pytest.raises(SystemExit) as caught:
        main(["measure", str(dispersed.path), "--dm", "300", *flags])
    assert caught.value.code == 2


def test_an_oversized_burst_becomes_an_error_record(dispersed, capsys):
    code = main(["measure", str(dispersed.path), "--dm", "300", "--max-load-gb", "0.005"])

    assert code == 1
    (record,) = json.loads(capsys.readouterr().out)
    assert record["status"] == "error"
    assert record["error"].startswith("LoadTooLargeError: Loading the read window 0.000-20.000 s")
    Draft202012Validator(get_schema("measurement")).validate(record)


def test_manifest_windows(dispersed, tmp_path, capsys):
    manifest = tmp_path / "batch.json"
    payload = {
        "schema_version": "1.0",
        "defaults": {"dm": 300, "mask": "none"},
        "bursts": [
            {"id": "around", "file": dispersed.path.name, "options": {"burst_time_s": 11, "burst_pad_s": 0.3}},
            {"id": "read", "file": dispersed.path.name, "options": {"read_window_s": [10, 12]}},
        ],
    }
    Draft202012Validator(get_schema("batch-manifest")).validate(payload)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["measure", "--manifest", str(manifest), "--max-load-gb", "1"]) == 0
    records = {record["id"]: record for record in json.loads(capsys.readouterr().out)}
    assert records["around"]["selection"]["read_window_s"] == pytest.approx([10.7, 11.3 + SWEEP_S], abs=1e-3)
    assert records["read"]["selection"]["read_window_s"] == pytest.approx([10.0, 12.0])


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"burst_time_s": 11, "read_window_s": [10, 12]}, "both set the read window"),
        ({"burst_pad_s": 0.3}, "only applies together"),
        ({"burst_time_s": -1}, "non-negative"),
        ({"read_window_s": [12, 10]}, "increasing"),
    ],
)
def test_manifest_rejects_inconsistent_windows(tmp_path, options, message):
    manifest = tmp_path / "batch.json"
    manifest.write_text(
        json.dumps({"schema_version": "1.0", "defaults": {"dm": 0}, "bursts": [{"file": "a.fil", "options": options}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=message):
        load_measure_manifest(manifest)


def test_manifest_window_flags_conflict_with_manifest(tmp_path, dispersed):
    with pytest.raises(SystemExit):
        main(["measure", "--manifest", str(tmp_path / "batch.json"), "--around", "11"])


def test_offpulse_stays_finite_in_a_short_window(dispersed):
    session = BurstSession.from_file(
        str(dispersed.path), dm=dispersed.dm, burst_time_sec=dispersed.burst_time_s, burst_pad_sec=0.1
    )
    assert np.isfinite(session.data).all()
    stop = session.ms_to_bin(dispersed.burst_time_s * 1000.0 - 20.0)
    std = session.data[:, :stop].std(axis=1)
    # 80 samples per channel, so single channels scatter; none collapses or blows up.
    assert np.median(std) == pytest.approx(1.0, abs=0.05)
    assert std.min() > 0.5 and std.max() < 1.5


def test_a_snapshot_reopens_the_same_window(dispersed):
    session = BurstSession.from_file(str(dispersed.path), dm=dispersed.dm, burst_time_sec=dispersed.burst_time_s)
    snapshot = session.snapshot_dict()

    assert snapshot["read_start_sec"] == pytest.approx(10.5)
    assert snapshot["read_end_sec"] == pytest.approx(11.5 + SWEEP_S)
    reopened = BurstSession.from_snapshot(snapshot)
    np.testing.assert_array_equal(reopened.data, session.data)
