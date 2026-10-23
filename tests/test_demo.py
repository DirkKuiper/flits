"""Tests for `flits demo`: fetching the tutorial burst and opening it prepared.

The download is exercised against a fake response, so these run offline. The
one test that needs the real 42 MB burst is marked `realdata`; the Reference
Workflows action runs it with FLITS_DEMO_DIR pointing at the downloaded file.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from urllib.error import URLError

import pytest

from flits import demo as demo_module
from flits.cli import main
from flits.demo import (
    DEMO_FILE_NAME,
    DemoError,
    default_demo_dir,
    demo_snapshot_path,
    fetch_demo_burst,
    write_demo_snapshot,
)
from flits.session import BurstSession

ROOT = Path(__file__).resolve().parents[1]


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _serve_bytes(monkeypatch, payload: bytes) -> list[str]:
    """Make the demo download return `payload` and publish its checksum."""
    requested: list[str] = []

    def fake_urlopen(request, timeout=None):
        requested.append(request.full_url)
        return _FakeResponse(payload)

    monkeypatch.setattr(demo_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(demo_module, "DEMO_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(demo_module, "DEMO_SIZE_BYTES", len(payload))
    return requested


def test_default_dir_prefers_flits_demo_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLITS_DEMO_DIR", str(tmp_path / "chosen"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert default_demo_dir() == tmp_path / "chosen"


def test_default_dir_falls_back_to_the_user_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLITS_DEMO_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert default_demo_dir() == tmp_path / "cache" / "flits" / "demo"


def test_fetch_downloads_and_verifies_the_burst(monkeypatch, tmp_path: Path) -> None:
    requested = _serve_bytes(monkeypatch, b"filterbank bytes" * 1000)

    path = fetch_demo_burst(tmp_path, show_progress=False)

    assert path == tmp_path / DEMO_FILE_NAME
    assert path.read_bytes() == b"filterbank bytes" * 1000
    assert requested == [demo_module.DEMO_URL]
    assert not list(tmp_path.glob(".*.part"))


def test_fetch_reuses_a_verified_copy_without_the_network(monkeypatch, tmp_path: Path) -> None:
    payload = b"already here"
    requested = _serve_bytes(monkeypatch, payload)
    (tmp_path / DEMO_FILE_NAME).write_bytes(payload)

    fetch_demo_burst(tmp_path, show_progress=False)

    assert requested == []


def test_fetch_replaces_a_copy_that_fails_its_checksum(monkeypatch, tmp_path: Path) -> None:
    requested = _serve_bytes(monkeypatch, b"the real burst")
    (tmp_path / DEMO_FILE_NAME).write_bytes(b"truncated")

    path = fetch_demo_burst(tmp_path, show_progress=False)

    assert requested == [demo_module.DEMO_URL]
    assert path.read_bytes() == b"the real burst"


def test_fetch_refuses_a_download_that_fails_its_checksum(monkeypatch, tmp_path: Path) -> None:
    _serve_bytes(monkeypatch, b"the real burst")
    monkeypatch.setattr(demo_module, "DEMO_SHA256", "0" * 64)

    with pytest.raises(DemoError, match="failed its checksum"):
        fetch_demo_burst(tmp_path, show_progress=False)

    # Nothing unverified is left behind to be mistaken for the burst later.
    assert list(tmp_path.iterdir()) == []


def test_fetch_explains_how_to_download_by_hand_when_offline(monkeypatch, tmp_path: Path) -> None:
    def offline(request, timeout=None):
        raise URLError("no route to host")

    monkeypatch.setattr(demo_module, "urlopen", offline)

    with pytest.raises(DemoError) as excinfo:
        fetch_demo_burst(tmp_path, show_progress=False)

    message = str(excinfo.value)
    assert "no route to host" in message
    assert demo_module.DEMO_URL in message
    assert str(tmp_path / DEMO_FILE_NAME) in message
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def synthetic_demo(monkeypatch, synthetic_waterfall):
    """Stand the synthetic burst in for the tutorial burst."""

    def prepare(burst_path: Path) -> BurstSession:
        session = BurstSession.from_file(str(burst_path), dm=0.0, sefd_jy=10.0)
        session.set_event_ms(100.0, 160.0)
        session.add_offpulse_ms(0.0, 50.0)
        session.compute_properties()
        return session

    monkeypatch.setattr(demo_module, "prepare_demo_session", prepare)
    return synthetic_waterfall.path


def test_snapshot_is_written_where_the_interface_lists_it(synthetic_demo: Path, tmp_path: Path) -> None:
    snapshot = write_demo_snapshot(tmp_path, synthetic_demo)

    assert snapshot == demo_snapshot_path(tmp_path)
    assert snapshot.parent.name == "snapshots"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["results"] is not None
    assert not list(snapshot.parent.glob(".*.part"))


def test_snapshot_keeps_changes_saved_from_the_interface(synthetic_demo: Path, tmp_path: Path) -> None:
    snapshot = write_demo_snapshot(tmp_path, synthetic_demo)
    snapshot.write_text('{"edited": true}\n', encoding="utf-8")

    assert write_demo_snapshot(tmp_path, synthetic_demo) == snapshot
    assert json.loads(snapshot.read_text(encoding="utf-8")) == {"edited": True}

    write_demo_snapshot(tmp_path, synthetic_demo, reset=True)
    assert json.loads(snapshot.read_text(encoding="utf-8"))["results"] is not None


def test_demo_command_serves_the_prepared_session(monkeypatch, synthetic_demo: Path, tmp_path: Path, capsys) -> None:
    import importlib

    monkeypatch.setattr(demo_module, "fetch_demo_burst", lambda directory: synthetic_demo)
    monkeypatch.setenv("FLITS_DATA_DIR", "unchanged-by-teardown")
    served: dict[str, object] = {}

    def fake_run(app, host, port, reload):
        served.update(app=app, host=host, port=port)

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["demo", "--dir", str(tmp_path), "--no-browser", "--port", "8124"]) == 0

    out = capsys.readouterr().out
    link = "http://127.0.0.1:8124/?open-snapshot=snapshots/" + demo_snapshot_path(tmp_path).name
    assert link in out
    assert served == {"app": "flits.web.app:app", "host": "127.0.0.1", "port": 8124}

    # The interface must find the session the link names.
    app_module = importlib.import_module("flits.web.app")
    assert app_module.data_dir() == tmp_path.resolve()
    library = app_module._refresh_session_snapshot_index(force=True)
    assert [item["relative_path"] for item in library["sessions"]] == [f"snapshots/{demo_snapshot_path(tmp_path).name}"]


def test_demo_command_reports_a_failed_download(monkeypatch, tmp_path: Path, capsys) -> None:
    def fail(directory):
        raise DemoError("Could not download the demo burst: offline")

    monkeypatch.setattr(demo_module, "fetch_demo_burst", fail)
    monkeypatch.setenv("FLITS_DATA_DIR", "unchanged-by-teardown")
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: pytest.fail("the server must not start"))

    assert main(["demo", "--dir", str(tmp_path), "--no-browser"]) == 1
    assert "offline" in capsys.readouterr().err


def test_help_lists_the_demo_command(capsys) -> None:
    assert main(["--help"]) == 0
    assert "demo" in capsys.readouterr().out


@pytest.mark.realdata
def test_demo_session_reproduces_the_guided_workflow(monkeypatch, tmp_path: Path) -> None:
    """The demo opens on the guided workflow's published reference values."""
    source_dir = default_demo_dir()
    source = source_dir / DEMO_FILE_NAME
    if not source.exists():
        pytest.skip(f"Tutorial burst not found in {source_dir}; set FLITS_DEMO_DIR")
    monkeypatch.setenv("FLITS_DATA_DIR", str(source_dir.resolve()))

    burst = fetch_demo_burst(source_dir, show_progress=False)
    snapshot = json.loads(write_demo_snapshot(tmp_path, burst).read_text(encoding="utf-8"))

    reference = json.loads((ROOT / "docs" / "examples" / "gbt-reference-summary.json").read_text(encoding="utf-8"))
    for field, expected in reference["measurements"].items():
        actual = snapshot["results"][field]
        assert math.isclose(
            actual,
            expected,
            rel_tol=reference["relative_tolerance"],
            abs_tol=reference["absolute_tolerance"],
        ), f"{field}: demo {actual}, reference {expected}"
    assert snapshot["dm_optimization"]["best_dm"] == pytest.approx(
        reference["dm_sweep"]["best_residual_dm"],
        rel=reference["relative_tolerance"],
        abs=reference["absolute_tolerance"],
    )
