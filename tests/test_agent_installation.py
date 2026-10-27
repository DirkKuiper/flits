"""Skill installation and setup must work outside a FLITS checkout."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "tools" / "install_agent_skill.py"
SKILL = ROOT / "plugins" / "flits" / "skills" / "frb-analysis"
CHECK = SKILL / "scripts" / "check_environment.py"


@pytest.mark.parametrize(
    "agent,directory", [("codex", ".agents"), ("cursor", ".cursor"), ("copilot", ".github"), ("claude", ".claude")]
)
def test_project_install_copies_a_complete_skill(agent, directory, tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--agent", agent, "--project-dir", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    target = tmp_path / directory / "skills" / "frb-analysis"
    for path in SKILL.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            assert (target / path.relative_to(SKILL)).read_bytes() == path.read_bytes()
    assert str(target) in result.stdout
    assert not list(target.rglob("*.pyc"))


def test_user_install_uses_the_selected_agents_home_directory(tmp_path, monkeypatch) -> None:
    installer = runpy.run_path(str(INSTALLER))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert installer["main"](["--agent", "copilot", "--scope", "user"]) == 0
    assert (tmp_path / ".copilot" / "skills" / "frb-analysis" / "SKILL.md").is_file()


def test_install_refuses_overwrite_and_update_preserves_local_files(tmp_path) -> None:
    install = runpy.run_path(str(INSTALLER))["install_skill"]
    target = install(tmp_path / "skill")
    (target / "SKILL.md").write_text("local changes", encoding="utf-8")
    (target / "notes.txt").write_text("local notes", encoding="utf-8")
    with pytest.raises(FileExistsError):
        install(target)
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "local changes"
    install(target, update=True)
    assert (target / "SKILL.md").read_bytes() == (SKILL / "SKILL.md").read_bytes()
    assert (target / "notes.txt").read_text(encoding="utf-8") == "local notes"


def test_update_refuses_an_unrelated_directory(tmp_path) -> None:
    install = runpy.run_path(str(INSTALLER))["install_skill"]
    with pytest.raises(ValueError, match="not an existing skill"):
        install(tmp_path, update=True)


def test_environment_check_runs_from_another_directory(tmp_path) -> None:
    result = subprocess.run([sys.executable, str(CHECK)], cwd=tmp_path, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert result.returncode == (0 if report["ready"] else 1)
    assert report["python"] == sys.executable
    assert isinstance(report["problems"], list)
    assert report["ready"] or report["problems"]


def test_environment_check_accepts_the_supported_checkout() -> None:
    report = runpy.run_path(str(CHECK))["check_environment"]()
    assert report["ready"], report["problems"]
    assert Path(report["flits_path"]).resolve() == ROOT / "flits" / "__init__.py"
    assert all(report["capabilities"].values())


def test_optional_component_comparison_does_not_block_basic_measurement(monkeypatch) -> None:
    import flits

    check = runpy.run_path(str(CHECK))["check_environment"]
    # Published FLITS 1.6 includes the native fitter but not the newer comparison method.
    session = type(
        "PublishedSession",
        (),
        {
            name: getattr(flits.BurstSession, name)
            for name in ("from_file", "auto_mask", "auto_localize", "compute_properties", "fit_model")
        },
    )
    package = SimpleNamespace(
        __version__="1.6.0",
        __file__=flits.__file__,
        BurstSession=session,
        MeasureOptions=flits.MeasureOptions,
        measure_burst=flits.measure_burst,
        measure_bursts=flits.measure_bursts,
    )
    monkeypatch.setitem(sys.modules, "flits", package)
    report = check()
    assert report["ready"], report["problems"]
    assert report["capabilities"] == {"native_fitting": True, "component_comparison": False}
    assert check(("native_fitting",))["ready"]
    missing = check(("component_comparison",))
    assert not missing["ready"]
    assert any("component_comparison" in problem for problem in missing["problems"])


def test_environment_check_explains_an_unsupported_interpreter(monkeypatch) -> None:
    check = runpy.run_path(str(CHECK))["check_environment"]
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))
    report = check()
    assert not report["ready"]
    assert report["problems"] == ["Select Python 3.11 or later before installing FLITS."]


@pytest.mark.parametrize("version", ["0.7.0", "1.5.0", "unknown"])
def test_environment_check_rejects_old_installations(version, monkeypatch) -> None:
    check = runpy.run_path(str(CHECK))["check_environment"]
    monkeypatch.setitem(sys.modules, "flits", SimpleNamespace(__version__=version))
    report = check()
    assert not report["ready"]
    assert any("1.6" in problem for problem in report["problems"])
    assert any("Missing Python entry point" in problem for problem in report["problems"])


def test_environment_check_reports_missing_dependency(monkeypatch) -> None:
    check = runpy.run_path(str(CHECK))["check_environment"]
    import importlib

    def missing(name):
        raise ModuleNotFoundError("No module named 'numpy'")

    monkeypatch.setattr(importlib, "import_module", missing)
    report = check()
    assert not report["ready"]
    assert any("numpy" in problem for problem in report["problems"])


def test_portable_and_claude_plugins_use_the_same_canonical_skill() -> None:
    for path, manifest_path in [
        (ROOT / ".agents" / "plugins" / "marketplace.json", "plugin.json"),
        (ROOT / ".claude-plugin" / "marketplace.json", ".claude-plugin/plugin.json"),
    ]:
        marketplace = json.loads(path.read_text(encoding="utf-8"))
        entry = marketplace["plugins"][0]
        source = entry["source"]
        relative = source["path"] if isinstance(source, dict) else source
        root = (ROOT / relative).resolve()
        assert root.is_relative_to(ROOT)
        manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
        assert manifest["name"] == entry["name"] == "flits"
        assert (root / "skills" / "frb-analysis").samefile(SKILL)
