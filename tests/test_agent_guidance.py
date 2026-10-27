"""Guidance for coding agents: help text, package docstring, and the FLITS skill.

Agents learn FLITS from these rather than from the docs site, so the tests check
that every command, option, function and method they name exists, and that the
skill's Python examples run.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path

import pytest

import flits
from flits.analysis.dm_optimization.core import DM_METRIC_REGISTRY
from flits.cli import (
    LLMS_TXT_URL,
    build_demo_parser,
    build_measure_parser,
    build_replay_parser,
    build_serve_parser,
    main,
)
from flits.session import BurstSession

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
SKILL = ROOT / "plugins" / "flits" / "skills" / "frb-analysis" / "SKILL.md"

# Frontmatter keys defined by the Agent Skills standard (https://agentskills.io).
# Claude Code accepts more, but other agents reject them.
PORTABLE_SKILL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _skill() -> tuple[dict[str, str], str]:
    match = re.match(r"---\n(.*?)\n---\n(.*)", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    return fields, match.group(2)


def _code_blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def test_top_level_help_shows_examples_and_docs_for_language_models(capsys) -> None:
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "flits measure burst.fil --dm 527.7" in out
    assert LLMS_TXT_URL in out


def test_measure_help_names_the_python_equivalent(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["measure", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "flits.measure_burst(path," in out
    assert "localization.warning_flags" in out


def test_help_wraps_without_splitting_urls_or_options(monkeypatch, capsys) -> None:
    monkeypatch.setenv("COLUMNS", "60")
    with pytest.raises(SystemExit):
        main(["measure", "--help"])
    words = capsys.readouterr().out.split()
    assert "https://dirkkuiper.github.io/flits/user-guide/headless-measurement/" in words
    assert "--snapshot-dir" in words


def test_package_docstring_names_entry_points_that_exist() -> None:
    doc = flits.__doc__ or ""
    for name in ("measure_burst", "MeasureOptions", "BurstSession"):
        assert f"flits.{name}" in doc
        assert hasattr(flits, name)
    assert "BurstSession.from_file" in doc and hasattr(BurstSession, "from_file")
    assert "llms.txt" in doc


def test_marketplace_lists_each_plugin_under_its_manifest_name() -> None:
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert marketplace["plugins"]
    for entry in marketplace["plugins"]:
        assert entry["source"].startswith("./") and ".." not in entry["source"]
        manifest = json.loads((ROOT / entry["source"] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["name"] == entry["name"]


def test_skill_frontmatter_follows_the_agent_skills_standard() -> None:
    fields, body = _skill()
    assert set(fields) <= PORTABLE_SKILL_KEYS
    assert fields["name"] == SKILL.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"]) and len(fields["name"]) <= 64
    assert 1 <= len(fields["description"]) <= 1024
    for extension in (".fil", ".fits", ".h5"):
        assert extension in fields["description"]
    assert len(body.splitlines()) < 500


def test_skill_names_only_existing_command_line_options() -> None:
    _, body = _skill()
    known = {
        option
        for parser in (build_serve_parser(), build_demo_parser(), build_measure_parser(), build_replay_parser())
        for action in parser._actions
        for option in action.option_strings
    }
    named = set()
    for line in "\n".join(_code_blocks(body, "bash")).splitlines():
        if line.startswith("flits "):
            assert line.split()[1] in {"measure", "replay", "demo"} or line.split()[1].startswith("--")
            named.update(word for word in shlex.split(line) if word.startswith("--"))
    assert named and named <= known, sorted(named - known)


def test_skill_names_only_existing_session_methods_and_dm_metrics() -> None:
    _, body = _skill()
    table = body.split("| Analysis | Method |", 1)[1].split("\n\n", 1)[0]
    for name in re.findall(r"`([a-z_]+)", table):
        if name in {"fitburst", "integrated_event_snr", "dm_phase", "smoothed_structure"}:
            continue
        assert callable(getattr(BurstSession, name, None)), name
    assert {"integrated_event_snr", "dm_phase", "smoothed_structure"} <= set(DM_METRIC_REGISTRY)
    for name in re.findall(r"session\.([a-z_]+)\(", body):
        assert callable(getattr(BurstSession, name, None)), name


def test_skill_python_examples_run(synthetic_waterfall, tmp_path, monkeypatch, capsys) -> None:
    shutil.copy(synthetic_waterfall.path, tmp_path / "burst.fil")
    monkeypatch.chdir(tmp_path)
    _, body = _skill()
    blocks = _code_blocks(body, "python")
    assert len(blocks) == 2
    for block in blocks:
        # The synthetic burst is stored dedispersed, so it is measured at DM 0.
        exec(compile(block.replace("527.7", "0.0"), str(SKILL), "exec"), {})

    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.startswith("ok ")
    assert json.loads((tmp_path / "burst_flits_session.json").read_text(encoding="utf-8"))
