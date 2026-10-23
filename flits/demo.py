"""A prepared example burst, for trying FLITS without data of your own.

``flits demo`` downloads the public GBT burst used by the guided workflow once,
checks it against its published SHA-256, and saves a session next to it holding
the guided workflow's selections, measurements and residual-DM sweep. It then
starts the interface with that session open, so the first thing a new user sees
is a measured burst rather than an empty load panel.

The selections match docs/guided-workflow.md, so the numbers on screen are the
ones the guide explains.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from flits.session import BurstSession

logger = logging.getLogger("flits.demo")

DEMO_FILE_NAME = "flits-tutorial-gbt-frb20240114a-v1.fil"
DEMO_URL = f"https://github.com/DirkKuiper/flits/releases/download/tutorial-data-v1/{DEMO_FILE_NAME}"
DEMO_SHA256 = "e80c69842cdd5f41b5a1fc617b7b2cebd864c121e27830bbb5d2161af539a2cc"
DEMO_SIZE_BYTES = 42_347_878
GUIDE_URL = "https://dirkkuiper.github.io/flits/guided-workflow/"

_CHUNK_BYTES = 1 << 20
_SNAPSHOT_SUFFIX = "_flits_session.json"


class DemoError(RuntimeError):
    """The demo burst could not be fetched or verified."""


def default_demo_dir() -> Path:
    """Where the demo burst is kept: ``$FLITS_DEMO_DIR``, else the user cache."""
    configured = os.environ.get("FLITS_DEMO_DIR")
    if configured:
        return Path(configured).expanduser()
    cache_root = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_root).expanduser() if cache_root else Path.home() / ".cache"
    return base / "flits" / "demo"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manual_download_hint(directory: Path) -> str:
    return (
        f"Download it by hand and run the demo again:\n"
        f"  mkdir -p {directory}\n"
        f"  curl --fail --location -o {directory / DEMO_FILE_NAME} \\\n"
        f"    {DEMO_URL}"
    )


def fetch_demo_burst(directory: Path, *, show_progress: bool | None = None) -> Path:
    """Return the verified demo burst in `directory`, downloading it if needed.

    A file already present is reused when its checksum matches, so the demo
    works offline once it has run, or from a copy placed there by hand.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / DEMO_FILE_NAME
    if target.exists():
        if _sha256(target) == DEMO_SHA256:
            return target
        logger.warning("Replacing %s: its checksum does not match the published burst", target)

    if show_progress is None:
        show_progress = sys.stderr.isatty()

    partial = target.with_name(f".{target.name}.part")
    logger.info("Downloading the demo burst (%.0f MB) from %s", DEMO_SIZE_BYTES / 1e6, DEMO_URL)
    try:
        request = Request(DEMO_URL, headers={"User-Agent": "flits-demo"})
        with urlopen(request, timeout=60) as response, partial.open("wb") as handle:
            received = 0
            while chunk := response.read(_CHUNK_BYTES):
                handle.write(chunk)
                received += len(chunk)
                if show_progress:
                    percent = min(100, 100 * received // DEMO_SIZE_BYTES)
                    print(
                        f"\r  {received / 1e6:5.1f} / {DEMO_SIZE_BYTES / 1e6:.1f} MB ({percent}%)",
                        end="",
                        file=sys.stderr,
                    )
        if show_progress:
            print(file=sys.stderr)
    except (URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise DemoError(f"Could not download the demo burst: {exc}\n{_manual_download_hint(directory)}") from exc

    digest = _sha256(partial)
    if digest != DEMO_SHA256:
        partial.unlink(missing_ok=True)
        raise DemoError(
            f"The downloaded demo burst failed its checksum (expected {DEMO_SHA256}, got {digest}).\n"
            f"{_manual_download_hint(directory)}"
        )
    partial.replace(target)
    return target


def prepare_demo_session(burst_path: Path) -> BurstSession:
    """Apply the guided workflow's selections and run its measurements.

    Mirrors examples/gbt_reference_workflow.py, which is checked against the
    published reference values in docs/examples/.
    """
    from flits.session import BurstSession

    session = BurstSession.from_file(str(burst_path.resolve()), dm=0.0, telescope="gbt", sefd_jy=10.0)
    session.set_time_factor(32)
    session.set_freq_factor(1)
    session.set_event_ms(235.0, 250.0)
    session.clear_offpulse()
    session.add_offpulse_ms(221.0, 233.0)
    session.add_offpulse_ms(260.0, 280.0)
    session.set_spectral_extent_freq(1300.0, 1750.0)
    session.notes = (
        "FLITS demo: the public GBT burst from the guided workflow "
        f"({GUIDE_URL}). The cutout is already dedispersed, so it is loaded at "
        "DM 0 and the DM sweep is a residual diagnostic. Event 235-250 ms; "
        "off-pulse 221-233 and 260-280 ms; band 1300-1750 MHz; time factor 32; "
        "no masked channels; preset SEFD 10 Jy with no SEFD uncertainty, so flux "
        "uncertainties are statistical only."
    )
    session.compute_properties()
    session.optimize_dm(center_dm=0.0, half_range=10.0, step=0.5, metric="dm_phase")
    return session


def demo_snapshot_path(directory: Path) -> Path:
    """The demo session's snapshot, where the interface saves that burst's sessions."""
    return directory / "snapshots" / f"{Path(DEMO_FILE_NAME).stem}{_SNAPSHOT_SUFFIX}"


def write_demo_snapshot(directory: Path, burst_path: Path, *, reset: bool = False) -> Path:
    """Write the prepared demo session, keeping an existing one unless `reset`.

    Keeping it means changes saved from the interface survive the next
    ``flits demo``; ``--reset`` restores the guided workflow's state.
    """
    target = demo_snapshot_path(directory)
    if target.exists() and not reset:
        return target

    logger.info("Preparing the demo session")
    payload = prepare_demo_session(burst_path).snapshot_dict()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    partial.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    partial.replace(target)
    return target
