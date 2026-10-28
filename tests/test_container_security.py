"""Keep the container's installed MessagePack above its patched version."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("installed", "expected"),
    [("1.2.1", 1), ("1.2.2", 1), ("1.2.3", 0), ("1.2.4", 0)],
)
def test_container_rejects_msgpack_before_memory_safety_fix(monkeypatch, capsys, installed, expected):
    path = Path(__file__).resolve().parents[1] / "docker" / "verify_security_floors.py"
    spec = importlib.util.spec_from_file_location("verify_security_floors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "version", lambda name: installed if name == "msgpack" else "78.1.1")

    assert module.main() == expected
    error = capsys.readouterr().err
    if expected:
        assert f"msgpack {installed} is below 1.2.3" in error
        assert "CVE-2026-114613" in error
    else:
        assert error == ""
