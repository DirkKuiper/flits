"""Estimate what loading a burst costs, and refuse a load that would not fit in memory.

FLITS reads a read window of the file -- the whole file unless told otherwise --
converts it to float32, dedisperses and normalizes it. Several float32 copies of
the window on top of the raw samples are alive at the peak: a Stokes I load peaks
at about twice the window's size on disk for 32-bit data and five times for
8-bit data, and a full Stokes cube at several times more. :func:`estimate_load` works this out from the header alone, and
:func:`enforce_load_budget` raises :class:`~flits.io.errors.LoadTooLargeError`
before a single sample is read when the estimate exceeds the budget.

The budget is, in order of precedence: ``ObservationConfig.max_load_gb``, the
``FLITS_MAX_LOAD_GB`` environment variable, or half the memory available when
the load starts (capped by a container's memory limit). Zero or a negative
value turns the check off.

:func:`burst_read_window` gives the smallest read window that holds a whole
dispersed burst, so a burst in a file far larger than memory can be loaded by
its arrival time alone.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from flits.io.errors import LoadTooLargeError
from flits.signal import dispersion_delay_sec

if TYPE_CHECKING:
    from flits.io.reader import FilterbankInspection
    from flits.settings import ObservationConfig

MAX_LOAD_ENV_VAR = "FLITS_MAX_LOAD_GB"
BYTES_PER_GB = 1e9
DEFAULT_MEMORY_FRACTION = 0.5
"""Share of the available memory one load may use when no budget is set."""

STOKES_I_FLOAT32_COPIES = 1.25
"""Float32 copies of the read window alive at a Stokes I load's peak, on top of the raw samples.

Measured with tracemalloc on SIGPROC files of 8 and 32 bits with one and four
IFs: one copy, since the reader dedisperses and normalizes in place. 0.25 is a
margin."""

SUMMED_STOKES_I_FLOAT32_COPIES = 2.25
"""As :data:`STOKES_I_FLOAT32_COPIES`, for two-product files, whose Stokes I is a sum (measured: two)."""

STOKES_CUBE_FLOAT32_COPIES = 9.5
"""Float32 copies of one Stokes plane alive while a full I/Q/U/V cube loads (measured: nine)."""

DEFAULT_BURST_PAD_SEC = 0.5
"""Data kept on either side of a dispersed burst when the read window is anchored on its time."""

_FLOAT32_BYTES = 4


@dataclass(frozen=True)
class LoadEstimate:
    """What loading one read window of a file is expected to cost.

    Times are seconds from the start of the file. `bytes_read` is what is read
    from disk; `estimated_load_bytes` the peak memory the load is expected to
    reach, which is what the budget is checked against.
    """

    read_start_sec: float
    read_end_sec: float
    nsamples: int
    bytes_read: int
    estimated_load_bytes: int
    windowed_read: bool


def read_window_samples(
    nsamples: int,
    tsamp_s: float,
    read_start_sec: float,
    read_end_sec: float | None,
) -> tuple[int, int]:
    """Return ``(first_sample, sample_count)`` for a read window, as the readers compute it."""
    nstart = min(max(int(float(read_start_sec) / tsamp_s), 0), max(nsamples - 1, 0))
    nread = max(1, nsamples - nstart)
    if read_end_sec is not None:
        nend = max(nstart + 1, int(float(read_end_sec) / tsamp_s))
        nread = min(nread, nend - nstart)
    return nstart, nread


def _bytes_per_window_sample(inspection: FilterbankInspection, *, stokes: bool) -> float:
    """Peak bytes each time sample of the window costs during a load."""
    nchans = int(inspection.nchans or 0)
    npol = max(1, int(inspection.polarization_products or 1))
    raw_bytes_per_value = float(inspection.in_memory_bytes_per_value or _FLOAT32_BYTES)
    if stokes:
        copies = STOKES_CUBE_FLOAT32_COPIES
    elif npol == 2:
        copies = SUMMED_STOKES_I_FLOAT32_COPIES
    else:
        copies = STOKES_I_FLOAT32_COPIES
    return nchans * npol * raw_bytes_per_value + copies * _FLOAT32_BYTES * nchans


def estimate_load(
    inspection: FilterbankInspection,
    read_start_sec: float = 0.0,
    read_end_sec: float | None = None,
    *,
    stokes: bool = False,
) -> LoadEstimate | None:
    """Estimate the cost of loading `[read_start_sec, read_end_sec)` of a file.

    `read_end_sec` None means the end of the file. Pass `stokes=True` for a full
    I/Q/U/V cube rather than Stokes I. Returns None when the reader does not
    report the layout the estimate needs.
    """
    nchans, nsamples, tsamp_s = inspection.nchans, inspection.nsamples, inspection.tsamp_s
    if not nchans or not nsamples or not tsamp_s or tsamp_s <= 0:
        return None
    nstart, nread = read_window_samples(nsamples, tsamp_s, read_start_sec, read_end_sec)
    windowed = bool(inspection.windowed_read)
    # A reader that reads the whole file holds all of it until it cuts the window.
    held = nread if windowed else nsamples
    data_bytes = inspection.data_bytes
    bytes_read = int(round(data_bytes * held / nsamples)) if data_bytes is not None else 0
    estimated = int(math.ceil(held * _bytes_per_window_sample(inspection, stokes=stokes)))
    return LoadEstimate(
        read_start_sec=nstart * tsamp_s,
        read_end_sec=(nstart + nread) * tsamp_s,
        nsamples=nread,
        bytes_read=bytes_read,
        estimated_load_bytes=estimated,
        windowed_read=windowed,
    )


def _cgroup_memory_limit() -> int | None:
    """The memory limit of the container FLITS runs in, if any (cgroup v2, then v1)."""
    for candidate in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            text = Path(candidate).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if text.isdigit():
            limit = int(text)
            # v1 reports a huge sentinel when there is no limit.
            if 0 < limit < 1 << 60:
                return limit
    return None


def available_memory_bytes() -> int | None:
    """Memory available to a new load, or None when it cannot be determined."""
    available: int | None = None
    try:
        import psutil

        available = int(psutil.virtual_memory().available)
    except Exception:
        try:
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        available = int(line.split()[1]) * 1024
                        break
        except OSError:
            pass
        if available is None:
            try:
                available = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
            except (AttributeError, OSError, ValueError):
                available = None
    limit = _cgroup_memory_limit()
    if limit is not None:
        available = limit if available is None else min(available, limit)
    return available


def load_budget_bytes(max_load_gb: float | None = None) -> int | None:
    """Return the load budget in bytes, or None for no limit.

    `max_load_gb` takes precedence, then ``FLITS_MAX_LOAD_GB``, then half the
    available memory. Zero or a negative value means no limit.
    """
    if max_load_gb is None:
        raw = os.environ.get(MAX_LOAD_ENV_VAR, "").strip()
        if raw:
            try:
                max_load_gb = float(raw)
            except ValueError as exc:
                raise ValueError(f"{MAX_LOAD_ENV_VAR} must be a number of GB; got {raw!r}") from exc
            if not math.isfinite(max_load_gb):
                raise ValueError(f"{MAX_LOAD_ENV_VAR} must be finite; got {raw!r}")
    if max_load_gb is not None:
        if not math.isfinite(max_load_gb):
            raise ValueError("max_load_gb must be finite")
        return None if max_load_gb <= 0 else int(max_load_gb * BYTES_PER_GB)
    available = available_memory_bytes()
    return None if available is None else int(available * DEFAULT_MEMORY_FRACTION)


def format_bytes(value: float) -> str:
    """Bytes as a short decimal-unit string, such as ``1.9 GB``."""
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if abs(value) >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{int(value)} B"


def max_window_sec(inspection: FilterbankInspection, budget_bytes: int, *, stokes: bool = False) -> float | None:
    """The longest read window whose load fits `budget_bytes`, or None if no window does or it is unknown."""
    if not inspection.windowed_read or not inspection.tsamp_s:
        return None
    per_sample = _bytes_per_window_sample(inspection, stokes=stokes)
    if per_sample <= 0:
        return None
    samples = int(budget_bytes // per_sample)
    return None if samples < 1 else samples * float(inspection.tsamp_s)


def enforce_load_budget(
    path: Path,
    inspection: FilterbankInspection,
    config: ObservationConfig,
    *,
    stokes: bool = False,
) -> LoadEstimate | None:
    """Raise :class:`LoadTooLargeError` if loading `config`'s read window would exceed the budget.

    Returns the estimate, or None when the reader does not report enough layout
    to estimate the load (the load then goes ahead unchecked).
    """
    estimate = estimate_load(inspection, config.read_start_sec, config.read_end_sec, stokes=stokes)
    if estimate is None:
        return None
    budget = load_budget_bytes(config.max_load_gb)
    if budget is None or estimate.estimated_load_bytes <= budget:
        return estimate

    longest = max_window_sec(inspection, budget, stokes=stokes)
    what = "the Stokes I/Q/U/V cube for" if stokes else "the read window"
    span = f"{estimate.read_start_sec:.3f}-{estimate.read_end_sec:.3f} s"
    duration = inspection.duration_s
    of_file = "" if duration is None else f" of {duration:.3f} s"
    lines = [
        f"Loading {what} {span}{of_file} of {path.name} would need about "
        f"{format_bytes(estimate.estimated_load_bytes)} of memory "
        f"({inspection.nchans} channels x {estimate.nsamples} samples); "
        f"the load budget is {format_bytes(budget)}."
    ]
    if longest is not None:
        lines.append(
            f"Load only the burst: give its arrival time with --around T (Python: burst_time_sec=T), "
            f"or a window of at most {longest:.3f} s with --read START END "
            "(Python: read_start_sec/read_end_sec)."
        )
    else:
        lines.append("This reader reads the whole file before cutting the window, so a smaller window does not help.")
    lines.append(f"To allow larger loads, pass --max-load-gb GB (Python: max_load_gb) or set {MAX_LOAD_ENV_VAR}.")
    raise LoadTooLargeError(
        " ".join(lines),
        path=path,
        estimated_load_bytes=estimate.estimated_load_bytes,
        budget_bytes=budget,
        max_window_sec=longest,
    )


def burst_read_window(
    inspection: FilterbankInspection,
    dm: float,
    burst_time_sec: float,
    pad_sec: float = DEFAULT_BURST_PAD_SEC,
) -> tuple[float, float]:
    """Return the smallest read window that holds a whole dispersed burst, with `pad_sec` either side.

    `burst_time_sec` is the burst's arrival time at the top of the band (the
    highest channel frequency), in seconds from the start of the file -- the
    reference FLITS dedisperses to. The window is
    ``[T - pad, T + sweep + pad]``, where the sweep is the dispersion delay
    across the band at the DM the data is dedispersed by on load.
    """
    if not math.isfinite(burst_time_sec) or burst_time_sec < 0:
        raise ValueError("burst_time_sec must be a finite, non-negative time in seconds from the start of the file")
    if not math.isfinite(pad_sec) or pad_sec < 0:
        raise ValueError("The padding around the burst must be a finite, non-negative number of seconds")
    duration = inspection.duration_s
    if duration is not None and burst_time_sec >= duration:
        raise ValueError(f"burst_time_sec={burst_time_sec:g} s is beyond the end of the file ({duration:.6g} s)")
    if inspection.freq_lo_mhz is None or inspection.freq_hi_mhz is None:
        raise ValueError("The file's frequency range is unknown, so the dispersion sweep cannot be worked out")
    # Data coherently dedispersed on disk (CHIME BBData) only carries the residual DM.
    residual_dm = float(dm) - float(inspection.coherent_dm or 0.0)
    sweep = dispersion_delay_sec(residual_dm, inspection.freq_lo_mhz, inspection.freq_hi_mhz)
    start = max(0.0, burst_time_sec - pad_sec + min(0.0, sweep))
    end = burst_time_sec + pad_sec + max(0.0, sweep)
    return start, end


__all__ = [
    "DEFAULT_BURST_PAD_SEC",
    "MAX_LOAD_ENV_VAR",
    "LoadEstimate",
    "available_memory_bytes",
    "burst_read_window",
    "enforce_load_budget",
    "estimate_load",
    "format_bytes",
    "load_budget_bytes",
    "max_window_sec",
    "read_window_samples",
]
