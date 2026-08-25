"""Conversion of raw four-product polarization data into Stokes I/Q/U/V.

Burst files carry four polarization products, but which four is not something
FLITS can infer from the numbers alone. A backend may write Stokes directly, or
it may write the coherency products of a linear or a circular feed. The three
cases differ only by which pair of products carries the linear polarization, so
guessing wrong silently swaps Stokes V into Q and produces an RM that is wrong
without looking wrong.

This module therefore keeps the basis explicit. Readers resolve it from the file
header where the format records it (PSRFITS ``POL_TYPE`` plus ``FD_POLN``), from
the telescope preset where the instrument is known, or from an operator override
-- and refuse to build a Stokes cube when none of those settle the question.
"""

from __future__ import annotations

from typing import Final

import numpy as np

#: Data are already Stokes I, Q, U, V.
BASIS_IQUV: Final = "iquv"
#: Coherency products of a linear feed: AA=XX, BB=YY, CR=Re(XY*), CI=Im(XY*).
BASIS_COHERENCY_LINEAR: Final = "coherency_linear"
#: Coherency products of a circular feed: AA=LL, BB=RR, CR=Re(LR*), CI=Im(LR*).
BASIS_COHERENCY_CIRCULAR: Final = "coherency_circular"

POLARIZATION_BASES: Final[dict[str, str]] = {
    BASIS_IQUV: "Stokes I, Q, U, V in that order.",
    BASIS_COHERENCY_LINEAR: "Linear-feed coherency products AA, BB, CR, CI.",
    BASIS_COHERENCY_CIRCULAR: "Circular-feed coherency products AA, BB, CR, CI.",
}

STOKES_LABELS: Final[tuple[str, str, str, str]] = ("I", "Q", "U", "V")

_BASIS_ALIASES: Final[dict[str, str]] = {
    "iquv": BASIS_IQUV,
    "stokes": BASIS_IQUV,
    "i_q_u_v": BASIS_IQUV,
    "aabbcrci": BASIS_COHERENCY_LINEAR,
    "aabbcrci_linear": BASIS_COHERENCY_LINEAR,
    "coherency": BASIS_COHERENCY_LINEAR,
    "coherency_linear": BASIS_COHERENCY_LINEAR,
    "linear": BASIS_COHERENCY_LINEAR,
    "lin": BASIS_COHERENCY_LINEAR,
    "xxyy": BASIS_COHERENCY_LINEAR,
    "aabbcrci_circular": BASIS_COHERENCY_CIRCULAR,
    "coherency_circular": BASIS_COHERENCY_CIRCULAR,
    "circular": BASIS_COHERENCY_CIRCULAR,
    "circ": BASIS_COHERENCY_CIRCULAR,
    "llrr": BASIS_COHERENCY_CIRCULAR,
}

#: PSRFITS ``POL_TYPE`` values that describe four full-Stokes-capable products.
_PSRFITS_FULL_STOKES_POL_TYPES: Final[frozenset[str]] = frozenset({"IQUV", "AABBCRCI"})


class PolarizationBasisError(ValueError):
    """Raised when four polarization products cannot be interpreted as Stokes."""


def normalize_polarization_basis(value: object) -> str | None:
    """Return the canonical basis name for ``value``, or None if unset.

    Raises PolarizationBasisError for a non-empty value that names no known
    basis, so a typo in a preset or an API request fails loudly.
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_").replace("+", "")
    if not text or text == "auto":
        return None
    resolved = _BASIS_ALIASES.get(text)
    if resolved is None:
        valid = ", ".join(sorted(POLARIZATION_BASES))
        raise PolarizationBasisError(f"Unknown polarization basis {value!r}. Valid values: {valid}.")
    return resolved


def basis_from_psrfits(pol_type: object, feed_polarization: object = None) -> str | None:
    """Map a PSRFITS ``POL_TYPE`` (with ``FD_POLN``) onto a FLITS basis.

    ``AABBCRCI`` names the coherency products without saying which feed produced
    them, so the receiver's ``FD_POLN`` decides. A file that declares neither is
    left unresolved rather than assumed.
    """
    if pol_type is None:
        return None
    text = str(pol_type).strip().upper().replace(" ", "")
    if not text or text not in _PSRFITS_FULL_STOKES_POL_TYPES:
        return None
    if text == "IQUV":
        return BASIS_IQUV
    feed = "" if feed_polarization is None else str(feed_polarization).strip().upper()
    if feed.startswith("LIN"):
        return BASIS_COHERENCY_LINEAR
    if feed.startswith("CIRC"):
        return BASIS_COHERENCY_CIRCULAR
    return None


def stokes_from_products(products: np.ndarray, basis: str) -> np.ndarray:
    """Convert four polarization products into a Stokes I/Q/U/V cube.

    ``products`` must have the four products on its first axis; the remaining
    axes are carried through untouched. The returned array is float32 and has
    the same trailing shape.

    The coherency conversions follow the PSRFITS/PSRCHIVE convention:
    a linear feed gives ``I=AA+BB, Q=AA-BB, U=2CR, V=2CI`` and a circular feed
    gives ``I=AA+BB, V=AA-BB, Q=2CR, U=2CI``.
    """
    resolved = normalize_polarization_basis(basis)
    if resolved is None:
        raise PolarizationBasisError("A polarization basis is required to build a Stokes cube.")

    values = np.asarray(products)
    if values.ndim < 2 or values.shape[0] < 4:
        raise PolarizationBasisError(
            f"Expected at least four polarization products on the first axis, got shape {values.shape}."
        )
    values = values[:4].astype(np.float32, copy=False)

    if resolved == BASIS_IQUV:
        return np.array(values, dtype=np.float32, copy=True)

    aa, bb, cr, ci = values
    total = aa + bb
    difference = aa - bb
    if resolved == BASIS_COHERENCY_LINEAR:
        stokes = (total, difference, 2.0 * cr, 2.0 * ci)
    else:
        stokes = (total, 2.0 * cr, 2.0 * ci, difference)
    return np.stack(stokes, axis=0).astype(np.float32, copy=False)


__all__ = [
    "BASIS_COHERENCY_CIRCULAR",
    "BASIS_COHERENCY_LINEAR",
    "BASIS_IQUV",
    "POLARIZATION_BASES",
    "STOKES_LABELS",
    "PolarizationBasisError",
    "basis_from_psrfits",
    "normalize_polarization_basis",
    "stokes_from_products",
]
