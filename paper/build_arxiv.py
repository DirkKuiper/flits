"""Build and verify a self-contained arXiv source archive from paper.md.

Requires Docker. The pinned Inara image supplies Pandoc and TeX Live. The
archive is extracted and compiled in isolation with networking disabled;
only main.tex is uploaded, with its complete rendered bibliography embedded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
IMAGE = "openjournals/inara@sha256:a0414b8b72fd8923917ede614d340d98dc7aa3102aabc2e955e9c02a6100fd62"


def run_container(directory: Path, executable: str, arguments: list[str], *, paper: bool = False) -> None:
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--volume",
        f"{directory}:/work",
        "--workdir",
        "/work",
    ]
    if paper:
        command += ["--volume", f"{PAPER}:/paper:ro"]
    command += ["--entrypoint", executable, IMAGE, *arguments]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"{executable} failed:\n{result.stdout[-12000:]}")


def build(destination: Path) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "image", "inspect", IMAGE], stdout=subprocess.DEVNULL, check=True)
    with tempfile.TemporaryDirectory(prefix="flits-arxiv-") as scratch:
        work = Path(scratch)
        run_container(
            work,
            "pandoc",
            [
                "/paper/paper.md",
                "--from=markdown",
                "--to=latex",
                "--standalone",
                "--citeproc",
                "--bibliography=/paper/paper.bib",
                "--csl=/usr/local/share/openjournals/apa.csl",
                "--template=/paper/arxiv-template.tex",
                "--output=main.tex",
            ],
            paper=True,
        )
        source = (work / "main.tex").read_bytes()
        text = source.decode()
        keys = set(re.findall(r"@([A-Za-z0-9_:-]+)", (PAPER / "paper.md").read_text()))
        rendered = set(re.findall(r"\\bibitem\[.*?\]\{ref-([^}]+)\}", text))
        if keys != rendered:
            raise RuntimeError(
                f"Bibliography differs from the manuscript: missing={keys - rendered}, extra={rendered - keys}"
            )
        if any(marker in text for marker in ("10.xxxxxx", "January 1970", "DRAFT", "??")):
            raise RuntimeError("Preprint contains unresolved proof markers.")
        archive = destination / "flits-arxiv-source.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            info = zipfile.ZipInfo("main.tex", (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, source)
        # Validate exactly the upload bytes, with no mounted manuscript or
        # local bibliography available to accidentally satisfy dependencies.
        isolated = work / "isolated"
        isolated.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(isolated)
        run_container(isolated, "latexmk", ["-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])
        log = (isolated / "main.log").read_text(errors="replace")
        problems = [
            line
            for line in log.splitlines()
            if "Overfull" in line or "undefined" in line.lower() or "Missing character:" in line
        ]
        if problems:
            raise RuntimeError("Inspect TeX warnings before submitting:\n" + "\n".join(problems))
        (destination / "main.tex").write_bytes(source)
        (destination / "flits-arxiv.pdf").write_bytes((isolated / "main.pdf").read_bytes())
        report = {
            "builder_image": IMAGE,
            "compiler": "pdfLaTeX via latexmk (TeX Live 2024)",
            "network_during_build": False,
            "archive_members": ["main.tex"],
            "bibliography_entries": len(rendered),
            "source_sha256": {
                name: hashlib.sha256((PAPER / name).read_bytes()).hexdigest()
                for name in ("paper.md", "paper.bib", "arxiv-template.tex")
            },
            "output_sha256": {
                name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                for name in ("main.tex", "flits-arxiv-source.zip", "flits-arxiv.pdf")
            },
        }
        (destination / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Verified {archive}; {len(rendered)} references; isolated offline pdfLaTeX build passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "arxiv")
    build(parser.parse_args().output)
