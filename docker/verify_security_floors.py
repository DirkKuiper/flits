"""Fail the container build if a package with a known advisory is installed.

FLITS does not import these packages directly; they arrive transitively. The
published image is a release artifact, so a regression here should stop the
build rather than surface later as a red scheduled vulnerability scan.

Each entry records the advisory that set the floor, so the list can be pruned
once the ecosystem has moved past it.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

# package -> (minimum version, advisory that set it)
FLOORS: dict[str, tuple[tuple[int, ...], str]] = {
    "msgpack": ((1, 2, 1), "GHSA-6v7p-g79w-8964"),
    "setuptools": ((78, 1, 1), "CVE-2025-47273"),
}


def _parse(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in raw.split(".")[:3]:
        digits = "".join(itm for itm in chunk if itm.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def main() -> int:
    failures: list[str] = []
    for name, (minimum, advisory) in FLOORS.items():
        try:
            found = version(name)
        except PackageNotFoundError:
            # Not installed at all is fine: nothing vulnerable is shipped.
            continue
        if _parse(found) < minimum:
            wanted = ".".join(str(part) for part in minimum)
            failures.append(f"{name} {found} is below {wanted} required by {advisory}")

    for failure in failures:
        print(f"security floor violated: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
