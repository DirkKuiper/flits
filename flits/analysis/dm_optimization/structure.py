"""Smoothed-structure DM metric with a noise-propagated DM interval.

Implements the method of Sutinjo et al. (2023, ApJ 954, 37): each trial DM's
event light curve is smoothed with a low-pass filter applied in the discrete
cosine transform (DCT-II) domain, and scored by the 2-norm of the derivative of
the smoothed curve (the "structure parameter"). Because the score is a norm,
the triangle inequality bounds how much the noise left over after smoothing can
change it, which gives each trial a noise term. A trial DM is consistent with
the best one when its score plus that noise term reaches the maximum (their
eq. 15), and the consistent trials span the reported DM interval.

This is a clean-room FLITS implementation from the paper. It runs on FLITS's
dedispersion and on the session's event window, spectral window and mask.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.fft import dct, idct

DEFAULT_FILTER_ORDER = 3
"""Order of the Butterworth-like low-pass filter (Sutinjo et al. use 3)."""

MIN_EVENT_BINS = 16
"""Fewest event-window samples the DCT spectrum can be judged from."""

_CUTOFF_WINDOW = 5


@dataclass(frozen=True)
class StructureCurve:
    """Structure scores and the eq. 15 consistency test for one cutoff.

    Attributes
    ----------
    cutoff
        Low-pass cutoff index ``k_c`` used for the filter.
    order
        Filter order.
    scores
        Structure parameter of each trial, ``||D1 i~_j||``.
    noise_terms
        ``||D1 S (dI_j - dI_best)||``, the noise allowance of each trial
        relative to the best trial, where ``dI = i - i~`` is the detrended noise.
    consistent
        Trials whose score plus noise term reaches the best score.
    best_index
        Index of the highest-scoring trial.
    """

    cutoff: int
    order: int
    scores: np.ndarray
    noise_terms: np.ndarray
    consistent: np.ndarray
    best_index: int


def dct_envelope(lightcurves: np.ndarray) -> np.ndarray:
    """Largest absolute DCT-II coefficient at each index across the trials.

    Signal occupies the low indices and noise the rest, so this spectrum shows
    where to put the low-pass cutoff (Sutinjo et al. 2023, sect. 4.3).
    """
    coefficients = dct(np.asarray(lightcurves, dtype=float), type=2, norm="ortho", axis=-1)
    return np.max(np.abs(coefficients), axis=0)


def noise_floor(envelope: np.ndarray) -> float:
    """Level of the noise-dominated upper half of a DCT envelope."""
    values = np.asarray(envelope, dtype=float)
    return float(np.mean(values[values.size // 2 :]))


def automatic_cutoff(envelope: np.ndarray, window: int = _CUTOFF_WINDOW) -> int | None:
    """Pick ``k_c`` where the DCT envelope first falls to the noise floor.

    The floor is the mean of the envelope's upper half. The cutoff is the first
    index at which a running mean over `window` + 1 coefficients is at or below
    it. Returns None when the envelope never reaches the floor.
    """
    values = np.asarray(envelope, dtype=float)
    floor = noise_floor(values)
    for index in range(window, values.size):
        if float(np.mean(values[index - window : index + 1])) <= floor:
            return index
    return None


def _filters(size: int, cutoff: int, order: int) -> tuple[np.ndarray, np.ndarray]:
    """Low-pass response and the derivative operator's DCT-domain gain."""
    k = np.arange(1, size + 1, dtype=float)
    lowpass = 1.0 / (1.0 + (k / float(cutoff)) ** (2 * int(order)))  # eq. 25
    # Square roots of the eigenvalues of the first-difference operator D1^T D1,
    # which the DCT-II diagonalizes.
    derivative = np.sqrt(2.0 - 2.0 * np.cos((k - 1.0) * np.pi / size))
    return lowpass, derivative


def structure_curve(lightcurves: np.ndarray, *, cutoff: int, order: int = DEFAULT_FILTER_ORDER) -> StructureCurve:
    """Score each trial light curve and apply the eq. 15 consistency test.

    Parameters
    ----------
    lightcurves
        Array of shape ``(n_trials, n_samples)``: the event light curve at each
        trial DM, in the same units throughout.
    cutoff
        Low-pass cutoff index ``k_c``, at least 1 and below ``n_samples``.
    order
        Filter order.
    """
    curves = np.asarray(lightcurves, dtype=float)
    if curves.ndim != 2 or curves.shape[0] < 1:
        raise ValueError("Structure scoring needs a 2-D array of trial light curves.")
    size = curves.shape[1]
    if not 1 <= int(cutoff) < size:
        raise ValueError(f"Low-pass cutoff k_c must be between 1 and {size - 1}; got {cutoff}.")
    if int(order) < 1:
        raise ValueError("Low-pass filter order must be at least 1.")

    lowpass, derivative = _filters(size, int(cutoff), int(order))
    coefficients = dct(curves, type=2, norm="ortho", axis=-1)
    band = lowpass * derivative
    scores = np.linalg.norm(coefficients * band, axis=1)  # eq. 27: ||D1 i~||
    best_index = int(np.argmax(scores))

    # Detrended noise dI = i - i~, and its difference from the best trial's.
    detrended = curves - idct(coefficients * lowpass, type=2, norm="ortho", axis=-1)
    relative = detrended - detrended[best_index]
    noise_terms = np.linalg.norm(dct(relative, type=2, norm="ortho", axis=-1) * band, axis=1)  # eq. 28

    consistent = scores + noise_terms >= scores[best_index]  # eq. 15
    return StructureCurve(
        cutoff=int(cutoff),
        order=int(order),
        scores=scores,
        noise_terms=noise_terms,
        consistent=consistent,
        best_index=best_index,
    )


def consistent_interval(trial_dms: np.ndarray, consistent: np.ndarray) -> tuple[float, float, list[str]]:
    """The DM span of the consistent trials, with warnings about its shape."""
    dms = np.asarray(trial_dms, dtype=float)
    indices = np.flatnonzero(np.asarray(consistent, dtype=bool))
    flags: list[str] = []
    if indices.size and np.any(np.diff(indices) > 1):
        flags.append("structure_interval_has_gaps")
    if indices.size and (indices[0] == 0 or indices[-1] == dms.size - 1):
        flags.append("structure_interval_on_sweep_edge")
    return float(dms[indices[0]]), float(dms[indices[-1]]), flags
