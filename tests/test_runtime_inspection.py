"""Metadata-only loading and runtime checks that catch stale installations."""

from __future__ import annotations

import copy
import importlib
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import flits
from flits.cli import main
from flits.io import detect_reader
from flits.runtime import compare_server_runtime, runtime_info


@pytest.mark.parametrize(
    "synthetic_waterfall", ["sigproc", "psrfits", "chime_hdf5", "chime_bbdata_beamformed"], indirect=True
)
def test_inspection_does_not_load_the_waterfall(synthetic_waterfall, monkeypatch):
    reader = detect_reader(synthetic_waterfall.path)

    def forbidden(*args, **kwargs):
        pytest.fail("Metadata inspection must not load the waterfall")

    monkeypatch.setattr(type(reader), "load", forbidden)
    record = flits.inspect_burst(synthetic_waterfall.path)
    assert record["status"] == "ok"
    assert record["format_id"] == reader.format_id
    assert record["metadata"]["source_path"] == str(synthetic_waterfall.path)
    assert record["metadata"]["freq_hi_mhz"] > record["metadata"]["freq_lo_mhz"]
    if synthetic_waterfall.coherent_dm is not None:
        assert record["suggested_dm"] == synthetic_waterfall.coherent_dm
        assert "residual DM" in record["dm_guidance"]
    else:
        assert record["suggested_dm"] is None
    json.dumps(record, allow_nan=False)


def test_inspection_keeps_failed_files_and_continues(synthetic_waterfall, tmp_path, capsys):
    missing = tmp_path / "missing.fil"
    assert main(["inspect", str(missing), str(synthetic_waterfall.path)]) == 1
    failure, success = json.loads(capsys.readouterr().out)
    assert failure["status"] == "error"
    assert failure["error_type"] == "FileNotFoundError"
    assert success["status"] == "ok"
    with pytest.raises(FileNotFoundError):
        flits.inspect_burst(missing)


def test_runtime_identity_is_stable_without_exposing_paths(monkeypatch):
    first = runtime_info()
    assert first["flits_version"] == flits.__version__
    assert first["fitter"] == "flits"
    assert "native_fitting" in first["capabilities"]
    assert first["source"]["python_source_sha256"]
    assert "package_path" not in first and "python_executable" not in first
    module = importlib.import_module("flits.runtime")

    def forbidden(*args):
        pytest.fail("An existing process must retain the identity it captured")

    monkeypatch.setattr(module, "source_identity", forbidden)
    # Editing files while a server is running must not relabel its loaded code.
    assert runtime_info() == first
    first["source"]["python_source_sha256"] = "changed by caller"
    assert runtime_info()["source"]["python_source_sha256"] != "changed by caller"


def test_info_identifies_local_interpreter(capsys):
    assert main(["info"]) == 0
    local = json.loads(capsys.readouterr().out)
    assert local["package_path"] == str(Path(flits.__file__).parent)
    assert local["python_executable"]
    assert local["schema_version"] == "1.0"


@contextmanager
def _health_server(payload):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/health"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, True),
        ({"flits_version": "1.5.0"}, False),
        ({"fitter": "fitburst"}, False),
        ({"source": {"python_source_sha256": "other"}}, False),
        ({"source": None}, None),
        ({"source": "malformed"}, None),
    ],
)
def test_runtime_comparison_checks_code_not_just_release(change, expected):
    local = runtime_info()
    server = {**copy.deepcopy(local), **change}
    assert compare_server_runtime(local, server) is expected


@pytest.mark.parametrize("kind,expected", [("matching", True), ("older", False), ("legacy", None), ("invalid", None)])
def test_info_compares_live_http_server(kind, expected, capsys):
    payload = {"status": "ok", **runtime_info()}
    if kind == "older":
        payload["flits_version"] = "1.5.0"
    elif kind == "legacy":
        payload = {"status": "ok"}
    raw = "not JSON" if kind == "invalid" else json.dumps(payload)
    with _health_server(raw) as url:
        assert main(["info", "--server", url]) == (0 if expected is True else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["same_flits_code"] is expected
    assert bool(report["error"]) == (kind == "invalid")


def test_info_connection_failure_is_machine_readable(capsys):
    with _health_server("{}") as url:
        pass
    assert main(["info", "--server", url]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["server"] is None
    assert report["same_flits_code"] is None
    assert report["error"]


def test_catalog_inspection_preserves_dedispersed_dm_convention(synthetic_waterfall, tmp_path):
    import h5py
    import numpy as np

    path = tmp_path / "catalog.h5"
    with h5py.File(path, "w") as handle:
        frb = handle.create_group("frb")
        frb.create_dataset("extent", data=[0.0, 256.0, 400.0, 800.0])
        frb.create_dataset("plot_freq", data=np.linspace(400.0, 800.0, synthetic_waterfall.data.shape[0]))
        frb.create_dataset("calibrated_wfall", data=synthetic_waterfall.data)
    record = flits.inspect_burst(path)
    assert record["metadata"]["schema_version"] == "chime_frb_catalog_v1"
    assert record["suggested_dm"] == 0.0
    assert "already dedispersed" in record["dm_guidance"]
