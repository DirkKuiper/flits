from __future__ import annotations

import numpy as np

from flits.io.errors import CorruptedDataError, MetadataMissingError
from flits.models import FilterbankMetadata


_MJD_MIN = 40000.0  # 1968-05-24, well before any digital radio data
_MJD_MAX = 80000.0  # 2076-11-26, well past any realistic observation


def validate_metadata(metadata: FilterbankMetadata) -> FilterbankMetadata:
    """Validate a FilterbankMetadata emitted by a reader.

    FLITS is freq-order-agnostic (see tests/test_frequency_order.py), so this
    validator checks monotonicity and sanity but does **not** reorder the axis.
    Returns the input unchanged on success; raises on any invariant violation.
    """
    path = metadata.source_path

    if not np.isfinite(metadata.tsamp) or metadata.tsamp <= 0.0:
        raise CorruptedDataError(
            f"Non-positive or non-finite tsamp: {metadata.tsamp!r}", path=path
        )
    if not np.isfinite(metadata.freqres) or metadata.freqres <= 0.0:
        raise CorruptedDataError(
            f"Non-positive or non-finite freqres: {metadata.freqres!r}", path=path
        )
    if not np.isfinite(metadata.bandwidth_mhz) or metadata.bandwidth_mhz <= 0.0:
        raise CorruptedDataError(
            f"Non-positive or non-finite bandwidth_mhz: {metadata.bandwidth_mhz!r}",
            path=path,
        )
    if int(metadata.header_npol) < 1:
        raise CorruptedDataError(
            f"header_npol must be >= 1, got {metadata.header_npol!r}", path=path
        )
    if int(metadata.npol) < 1:
        raise CorruptedDataError(
            f"npol must be >= 1, got {metadata.npol!r}", path=path
        )

    if not (_MJD_MIN <= metadata.start_mjd <= _MJD_MAX):
        raise CorruptedDataError(
            f"start_mjd {metadata.start_mjd!r} is outside plausible range "
            f"[{_MJD_MIN}, {_MJD_MAX}]",
            path=path,
        )

    freqs = np.asarray(metadata.freqs_mhz, dtype=float)
    if freqs.ndim != 1 or freqs.size < 2:
        raise CorruptedDataError(
            f"freqs_mhz must be a 1-D array with at least 2 channels, "
            f"got shape {freqs.shape}",
            path=path,
        )
    if not np.all(np.isfinite(freqs)):
        raise CorruptedDataError("freqs_mhz contains non-finite values", path=path)

    diffs = np.diff(freqs)
    if not (np.all(diffs > 0) or np.all(diffs < 0)):
        raise CorruptedDataError(
            "freqs_mhz must be strictly monotonic (ascending or descending)",
            path=path,
        )

    return metadata


def require_fields(path: object, missing: tuple[str, ...], *, context: str) -> None:
    """Helper for readers: raise MetadataMissingError if `missing` is non-empty."""
    if missing:
        raise MetadataMissingError(
            f"{context}: required fields missing: {', '.join(missing)}",
            path=path,
            fields=missing,
        )


__all__ = ["validate_metadata", "require_fields"]
