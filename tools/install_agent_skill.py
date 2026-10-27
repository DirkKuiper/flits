"""Copy the canonical FLITS skill into a supported agent's discovery directory.

Run from a FLITS source checkout. No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "plugins" / "flits" / "skills" / "frb-analysis"
DIRECTORIES = {
    "codex": {"project": ".agents", "user": ".agents"},
    "cursor": {"project": ".cursor", "user": ".cursor"},
    "copilot": {"project": ".github", "user": ".copilot"},
    "claude": {"project": ".claude", "user": ".claude"},
}


def install_skill(destination: Path, *, update: bool = False) -> Path:
    """Install the whole skill, refusing to replace existing instructions by default."""
    destination = destination.expanduser().absolute()
    if destination.is_symlink():
        raise ValueError(f"Refusing to write through a symlink: {destination}")
    if destination.exists() and not update:
        raise FileExistsError(f"Skill already exists: {destination}. Review it before using --update.")
    if destination.exists() and not (destination / "SKILL.md").is_file():
        raise ValueError(f"Destination is not an existing skill: {destination}")
    # Copy resources as well as SKILL.md. In particular, setup must work after
    # installation from another working directory, on Windows as well as Unix.
    shutil.copytree(SOURCE, destination, dirs_exist_ok=update, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=sorted(DIRECTORIES), required=True)
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument(
        "--project-dir", type=Path, default=Path.cwd(), help="Project root (default: current directory)."
    )
    parser.add_argument(
        "--update", action="store_true", help="Replace packaged skill files after reviewing the installed copy."
    )
    args = parser.parse_args(argv)
    base = Path.home() if args.scope == "user" else args.project_dir
    destination = base / DIRECTORIES[args.agent][args.scope] / "skills" / SOURCE.name
    try:
        installed = install_skill(destination, update=args.update)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Installed FLITS skill: {installed}")
    print("Open a new agent session and check that frb-analysis appears among the available skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
