from __future__ import annotations

import errno
import os
import shutil
import struct
import tempfile
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Iterator

import numpy as np
import psrchive

from flits.io.errors import (
    CorruptedDataError,
    FormatDetectionError,
    MetadataMissingError,
)
from flits.io.reader import FilterbankInspection
from flits.io.validation import validate_metadata
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig
from flits.signal import dedisperse, normalize

logger = logging.getLogger(__name__)

# ============================================================
# Optional dependency: your (SIGPROC backend)
# ============================================================
try:
    import your as _your
    _YOUR_IMPORT_ERROR: Exception | None = None
except Exception as exc:
    _your = SimpleNamespace(Your=None)
    _YOUR_IMPORT_ERROR = exc

your = _your

_SIGPROC_MAGIC = struct.pack("i", 12) + b"HEADER_START"
_FITS_MAGIC = b"SIMPLE  ="


# ============================================================
# Utilities
# ============================================================

def _is_mmap_deadlock(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.EDEADLK


@contextmanager
def _open_your(source_path: Path) -> Iterator[object]:
    if your.Your is None:
        raise RuntimeError("SIGPROC backend 'your' unavailable") from _YOUR_IMPORT_ERROR

    tmp_path: Path | None = None
    reader = None

    try:
        try:
            reader = your.Your(str(source_path))
        except OSError as exc:
            if not _is_mmap_deadlock(exc):
                raise

            tmp_path = Path(tempfile.gettempdir()) / f"flits_{os.getpid()}_{source_path.name}"
            shutil.copyfile(source_path, tmp_path)
            reader = your.Your(str(tmp_path))

        yield reader

    finally:
        if reader and hasattr(reader, "fp"):
            try:
                reader.fp.close()
            except Exception:
                pass

        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


# ============================================================
# PSRFITS HANDLING (STRICT)
# ============================================================

def _is_psrfits_file(path: Path) -> bool:
    """Cheap + robust FITS detection."""
    try:
        head = path.read_bytes()[:16]
        return head.startswith(_FITS_MAGIC)
    except Exception:
        return False


def _psrchive_is_fold_mode(path: Path) -> bool:
    """Single authoritative check using PSRCHIVE."""
    try:
        ar = psrchive.Archive_load(str(path))
        ar_type = str(ar.get_type()).lower() if ar.get_type() else ""
        return "fold" in ar_type
    except Exception:
        return False


def _load_folded_psrchive(
    path: Path,
    config: ObservationConfig,
) -> tuple[np.ndarray, FilterbankMetadata]:

    logger.info(f"[PSRCHIVE] folded load: {path.name}")

    ar = psrchive.Archive_load(str(path))
    ar.remove_baseline()

    if not ar.get_dedispersed():
        ar.dedisperse()

    dm = float(ar.get_dispersion_measure())
    period = float(ar[0].get_folding_period())

    data = ar.get_data()  # (subint, pol, chan, bin)

    # → Stokes I
    data = data[:, 0, :, :]        # (subint, chan, bin)
    dyn = np.mean(data, axis=2)    # (subint, chan)
    dyn = dyn.T                    # (chan, subint)

    freqs = np.asarray(ar.get_frequencies(), dtype=float)
    order = np.argsort(freqs)

    freqs = freqs[order]
    dyn = dyn[order, :]

    dyn = normalize(dyn, dyn[:, int(0.8 * dyn.shape[1]):]).astype(np.float32)

    metadata = FilterbankMetadata(
        source_path=path,
        source_name=path.stem,

        tsamp=period,
        freqres=float(abs(freqs[1] - freqs[0])) if len(freqs) > 1 else 0.0,
        start_mjd=float(ar.get_start_time().in_days()) if ar.get_start_time() else 0.0,

        sefd_jy=config.sefd_jy or 1.0,
        bandwidth_mhz=float(freqs.max() - freqs.min()),

        npol=1,
        freqs_mhz=freqs,
        header_npol=1,

        telescope_name=str(ar.get_telescope()) if ar.get_telescope() else "unknown",

        detected_preset_key="psrchive_fold",
        detection_basis="folded_psrfits",

        time_scale="folded",
        time_reference_frame="topocentric",
    )

    return dyn, validate_metadata(metadata)


# ============================================================
# MAIN READER
# ============================================================

class YourFilterbankReader:
    """
    Clean, strict, non-ambiguous reader:

    SIGPROC (.fil) → your backend
    PSRFITS fold   → psrchive ONLY
    PSRFITS search → REJECTED (explicit)
    """

    format_id: ClassVar[str] = "sigproc"
    extensions: ClassVar[tuple[str, ...]] = (".fil", ".fits", ".sf")

    # --------------------------------------------------------
    def sniff(self, path: Path) -> bool:
        try:
            head = path.read_bytes()[:16]
        except Exception:
            return False

        if head.startswith(_SIGPROC_MAGIC):
            return True

        if head.startswith(_FITS_MAGIC):
            return _is_psrfits_file(path)

        return False

    # --------------------------------------------------------
    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:

        path = Path(path).expanduser().resolve()
        is_fits = path.suffix.lower() in {".fits", ".sf"}

        # =====================================================
        # PSRFITS PATH (STRICT GATE)
        # =====================================================
        if is_fits:
            if not _is_psrfits_file(path):
                raise FormatDetectionError("Invalid FITS file", path=path)

            # CRITICAL FIX: decide mode BEFORE anything else
            is_fold = _psrchive_is_fold_mode(path)

            if not is_fold:
                raise FormatDetectionError(
                    "PSRFITS search-mode not supported in this backend.",
                    path=path,
                )

            return _load_folded_psrchive(path, config)

        # =====================================================
        # SIGPROC PATH ONLY
        # =====================================================
        if path.suffix.lower() != ".fil":
            raise FormatDetectionError(
                "File is neither SIGPROC (.fil) nor supported PSRFITS fold.",
                path=path,
            )

        with _open_your(path) as reader:
            h = reader.your_header

            tsamp = float(h.tsamp)
            foff = float(h.foff)
            fch1 = float(h.fch1)
            nchans = int(h.nchans)
            nspectra = int(h.nspectra)

            raw = reader.get_data(0, nspectra, npoln=1)
            stokes_i = raw[:, 0, :].T

            freqs = fch1 + foff * np.arange(nchans)

            stokes_i = dedisperse(stokes_i, config.dm, freqs, tsamp)

            stokes_i = normalize(
                stokes_i,
                stokes_i[:, int(0.8 * stokes_i.shape[1]):]
            ).astype(np.float32)

        metadata = FilterbankMetadata(
            source_path=path,
            source_name=path.stem,

            tsamp=tsamp,
            freqres=abs(foff),
            start_mjd=float(h.tstart),

            sefd_jy=config.sefd_jy or 1.0,
            bandwidth_mhz=float(freqs.max() - freqs.min()),

            npol=1,
            freqs_mhz=freqs,
            header_npol=1,

            telescope_name="generic",

            detected_preset_key="sigproc",
            detection_basis="your_sigproc",

            time_scale="utc",
            time_reference_frame="topocentric",
        )

        return stokes_i, validate_metadata(metadata)


__all__ = ["YourFilterbankReader"]