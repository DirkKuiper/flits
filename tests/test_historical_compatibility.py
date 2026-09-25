"""Read real release-generated artifacts without running their old producers."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import pytest
from PIL import Image
from your import Your

from flits.cli import _compare_measurements
from flits.session import BurstSession

FIXTURES = Path(__file__).parent / "fixtures" / "compatibility"
GENERATION = json.loads((FIXTURES / "generation.json").read_text())
RELEASES = GENERATION["releases"]


def test_historical_fixture_integrity():
    for name, expected in GENERATION["sha256"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == expected, name


@pytest.mark.parametrize("release", RELEASES, ids=lambda item: item["tag"])
def test_historical_snapshot_preserves_stored_results_and_selections(release, monkeypatch):
    monkeypatch.setenv("FLITS_DATA_DIR", str(FIXTURES.resolve()))
    snapshot = json.loads((FIXTURES / release["tag"] / "session.json").read_text())
    assert snapshot["schema_version"] == release["session_schema"]
    restored = BurstSession.from_snapshot(snapshot)
    saved = restored.snapshot_dict()
    for field in ("dm", "crop_bins", "event_bins", "offpulse_regions", "masked_channels", "time_factor", "freq_factor"):
        assert saved[field] == snapshot[field]
    assert not _compare_measurements(saved["results"], snapshot["results"], 1e-12)
    assert saved["software_provenance"]["products"]["results"]["environment_id"] is None
    # Saving and reopening again must not manufacture historical provenance.
    reopened = BurstSession.from_snapshot(saved)
    assert reopened.software_provenance()["products"]["results"]["environment_id"] is None
    reopened.compute_properties()
    differences = _compare_measurements(reopened.results.to_dict(), snapshot["results"], 1e-6)
    if release["tag"] == "flits-v0.2.0":
        # Release 0.2.1 deliberately reclassified these diagnostic error bars;
        # retaining old values is supported, silently claiming identical
        # recalculation with the new uncertainty semantics is not.
        assert {item["field"] for item in differences} == {
            "uncertainties.toa_topo_mjd",
            "uncertainties.width_ms_acf",
            "uncertainties.spectral_width_mhz_acf",
            "uncertainties.peak_flux_jy",
            "uncertainties.fluence_jyms",
        }
    else:
        assert not differences


@pytest.mark.parametrize("release", RELEASES, ids=lambda item: item["tag"])
def test_historical_exports_are_readable_with_independent_readers(release):
    directory = FIXTURES / release["tag"]
    science = json.loads((directory / "science.json").read_text())
    assert science["schema_version"] == release["export_schema"]
    assert science["flits_version"] == release["flits_version"]
    with (directory / "catalog.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert float(row["fluence_jyms"]) == pytest.approx(science["results"]["fluence_jyms"])
    for path in directory.glob("*.npz"):
        with np.load(path, allow_pickle=False) as arrays:
            for key in arrays.files:
                assert not arrays[key].dtype.hasobject
            assert arrays["dynamic_spectrum"].ndim == 2
            assert arrays["dynamic_spectrum"].shape == (arrays["freq_axis_mhz"].size, arrays["time_axis_ms"].size)
    for path in directory.glob("*.png"):
        with Image.open(path) as image:
            image.verify()
    for path in directory.glob("*.svg"):
        assert ElementTree.parse(path).getroot().tag.endswith("svg")
    metadata = json.loads((directory / "window_native.meta.json").read_text())
    reader = Your(str(directory / "window_native.fil"))
    raw = reader.get_data(nstart=0, nsamp=reader.your_header.nspectra).T
    with np.load(directory / "window_native.npz", allow_pickle=False) as arrays:
        assert raw.shape == arrays["dynamic_spectrum"].shape
        np.testing.assert_allclose(raw, np.nan_to_num(arrays["dynamic_spectrum"]), rtol=1e-6, atol=1e-6)
        assert json.loads(str(arrays["window_metadata_json"])) == metadata
