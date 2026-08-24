"""Validate the local JOSS manuscript and citation metadata."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_PATH = ROOT / "paper" / "paper.md"
BIB_PATH = ROOT / "paper" / "paper.bib"
CFF_PATH = ROOT / "CITATION.cff"

REQUIRED_SECTIONS = (
    "Summary",
    "Statement of need",
    "State of the field",
    "Software design",
    "Research impact",
    "AI usage disclosure",
    "Acknowledgements",
    "References",
)


def _orcid_is_valid(value: str) -> bool:
    compact = value.replace("https://orcid.org/", "").replace("-", "")
    if not re.fullmatch(r"\d{15}[\dX]", compact):
        return False
    total = 0
    for character in compact[:15]:
        total = (total + int(character)) * 2
    check = (12 - total % 11) % 11
    expected = "X" if check == 10 else str(check)
    return compact[-1] == expected


def _resolver_status(doi: str) -> tuple[str, int | None, str | None]:
    result = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--head",
            "--max-time",
            "30",
            "--retry",
            "2",
            "--user-agent",
            "FLITS-JOSS-paper-check/1.0",
            "--output",
            os.devnull,
            "--write-out",
            "%{http_code}\t%{redirect_url}",
            f"https://doi.org/{doi}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    try:
        status_text, location = result.stdout.strip().split("\t", 1)
        status = int(status_text)
    except (ValueError, TypeError):
        return doi, None, None
    return doi, status, location or None


def validate(check_dois: bool) -> list[str]:
    errors: list[str] = []
    paper = PAPER_PATH.read_text(encoding="utf-8")
    bibliography = BIB_PATH.read_text(encoding="utf-8")
    citation = CFF_PATH.read_text(encoding="utf-8")

    frontmatter_parts = paper.split("---", 2)
    if len(frontmatter_parts) != 3:
        return ["paper.md must start with a YAML front matter block"]
    frontmatter = frontmatter_parts[1]
    manuscript = frontmatter_parts[2]

    headings = tuple(re.findall(r"^# (.+)$", manuscript, flags=re.MULTILINE))
    missing_sections = [section for section in REQUIRED_SECTIONS if section not in headings]
    if missing_sections:
        errors.append(f"missing required sections: {', '.join(missing_sections)}")

    body = manuscript.split("# References", 1)[0]
    words = re.findall(r"\b[\w'-]+\b", body)
    if not 750 <= len(words) <= 1750:
        errors.append(f"manuscript body has {len(words)} words; expected 750-1750")

    paper_title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', frontmatter, re.MULTILINE)
    cff_title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', citation, re.MULTILINE)
    paper_title = paper_title_match.group(1).rstrip("\"'") if paper_title_match else ""
    cff_title = cff_title_match.group(1).rstrip("\"'") if cff_title_match else ""
    if not paper_title or paper_title != cff_title:
        errors.append("paper.md and CITATION.cff titles must match exactly")

    paper_orcid_match = re.search(r"^\s+orcid:\s*(\S+)$", frontmatter, re.MULTILINE)
    cff_orcid_match = re.search(r"^\s+orcid:\s*(\S+)$", citation, re.MULTILINE)
    paper_orcid = paper_orcid_match.group(1) if paper_orcid_match else ""
    cff_orcid = cff_orcid_match.group(1) if cff_orcid_match else ""
    if not _orcid_is_valid(paper_orcid):
        errors.append(f"paper.md has an invalid ORCID: {paper_orcid or 'missing'}")
    if not _orcid_is_valid(cff_orcid):
        errors.append(f"CITATION.cff has an invalid ORCID: {cff_orcid or 'missing'}")
    if paper_orcid and cff_orcid.removeprefix("https://orcid.org/") != paper_orcid:
        errors.append("paper.md and CITATION.cff ORCIDs do not match")

    used_keys = set(re.findall(r"@([A-Za-z0-9_:-]+)", body))
    defined_keys = set(re.findall(r"^@[A-Za-z]+\{([^,]+),", bibliography, re.MULTILINE))
    missing_keys = sorted(used_keys - defined_keys)
    unused_keys = sorted(defined_keys - used_keys)
    if missing_keys:
        errors.append(f"undefined citation keys: {', '.join(missing_keys)}")
    if unused_keys:
        errors.append(f"unused bibliography entries: {', '.join(unused_keys)}")

    dois = sorted(set(re.findall(r"doi\s*=\s*\{([^}]+)\}", bibliography, re.IGNORECASE)))
    invalid_dois = [doi for doi in dois if not re.fullmatch(r"10\.\d{4,9}/\S+", doi)]
    if invalid_dois:
        errors.append(f"invalid DOI syntax: {', '.join(invalid_dois)}")

    if check_dois and not invalid_dois:
        if shutil.which("curl") is None:
            errors.append("curl is required for --check-dois")
            return errors
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(_resolver_status, dois))
        failed = [doi for doi, status, _ in results if status is None or not 200 <= status < 400]
        if failed:
            errors.append(f"DOIs that did not resolve: {', '.join(failed)}")
        for doi, status, location in results:
            print(f"DOI {doi}: HTTP {status} -> {location or 'no redirect'}")

    print(f"Paper body: {len(words)} words")
    print(f"Sections: {', '.join(headings)}")
    print(f"Citations: {len(used_keys)} used, {len(defined_keys)} defined")
    print(f"DOIs: {len(dois)} syntactically valid")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-dois",
        action="store_true",
        help="also contact doi.org and require every DOI to resolve",
    )
    arguments = parser.parse_args()
    errors = validate(check_dois=arguments.check_dois)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("JOSS paper checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
