"""Verify the reader-plugin registry discovers third-party readers."""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from flits.io import detect_reader, register_reader, unregister_reader
from flits.io.reader import FilterbankInspection, list_readers, reader_diagnostics
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig


_FAKE_MAGIC = b"FLITS_FAKE_FMT\n"


class _FakeReader:
    format_id: ClassVar[str] = "fake_format"
    extensions: ClassVar[tuple[str, ...]] = (".fake",)

    def sniff(self, path: Path) -> bool:
        try:
            with open(path, "rb") as handle:
                return handle.read(len(_FAKE_MAGIC)) == _FAKE_MAGIC
        except OSError:
            return False

    def inspect(self, path: Path) -> FilterbankInspection:
        return FilterbankInspection(
            source_path=path,
            source_name="FAKE",
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="fake-plugin",
        )

    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        data = np.zeros((4, 8), dtype=np.float32)
        metadata = FilterbankMetadata(
            source_path=path,
            source_name="FAKE",
            tsamp=1e-3,
            freqres=1.0,
            start_mjd=60000.0,
            read_start_sec=0.0,
            sefd_jy=1.0,
            bandwidth_mhz=4.0,
            npol=1,
            freqs_mhz=np.array([1500.0, 1499.0, 1498.0, 1497.0]),
            header_npol=1,
            telescope_id=None,
            machine_id=None,
            detected_preset_key="generic",
            detection_basis="fake-plugin",
        )
        return data, metadata


@pytest.fixture
def fake_plugin():
    register_reader(_FakeReader)
    try:
        yield _FakeReader
    finally:
        unregister_reader(_FakeReader)


def test_register_reader_adds_to_registry(fake_plugin):
    readers = list_readers()
    format_ids = [r.format_id for r in readers]
    assert "fake_format" in format_ids


def test_registered_reader_is_picked_by_extension(fake_plugin, tmp_path):
    path = tmp_path / "burst.fake"
    path.write_bytes(_FAKE_MAGIC + b"rest of file")
    reader = detect_reader(path)
    assert reader.format_id == "fake_format"


def test_registered_reader_is_picked_by_magic_bytes(fake_plugin, tmp_path):
    path = tmp_path / "no_ext_match"
    path.write_bytes(_FAKE_MAGIC + b"rest of file")
    reader = detect_reader(path)
    assert reader.format_id == "fake_format"


def test_unregister_removes_from_registry(fake_plugin):
    unregister_reader(_FakeReader)
    assert all(r.format_id != "fake_format" for r in list_readers())


def test_diagnostics_include_builtin_readers():
    diags = reader_diagnostics()
    targets = [entry["target"] for entry in diags]
    assert any("your_reader:YourFilterbankReader" in t for t in targets)
    assert any("chime_hdf5_reader:ChimeHdf5Reader" in t for t in targets)
