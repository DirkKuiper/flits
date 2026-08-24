from __future__ import annotations

import warnings

import numpy as np
from scipy.signal import fftconvolve


def normalize(ds: np.ndarray, offpulse: np.ndarray) -> np.ndarray:
    dtype = np.result_type(ds.dtype, np.float32)
    ds = ds.astype(dtype, copy=True)
    offpulse = offpulse.astype(dtype, copy=True)
    normalized = np.empty_like(ds)

    for chan in range(ds.shape[0]):
        channel = ds[chan, :]
        offpulse_channel = offpulse[chan, :]

        chan_mean = np.nanmedian(offpulse_channel)
        chan_std = np.nanstd(offpulse_channel)
        if not np.isfinite(chan_std) or chan_std == 0:
            normalized[chan, :] = channel - chan_mean
        else:
            normalized[chan, :] = (channel - chan_mean) / chan_std
    return normalized


# Dispersion constant in MHz^2 pc^-1 cm^3 s, as k_DM = 1 / (2.41e-4). This is
# the pulsar-astronomy convention rather than the exact physical constant; see
# Manchester & Taylor (1977) and the discussion in Kulkarni (2020),
# arXiv:2007.02886.
DM_CONSTANT_S_MHZ2 = 1.0 / (2.41 * 10**-4)


def dedispersion_shift_bins(
    dm: float, freqs_mhz: np.ndarray, tsamp_sec: float
) -> np.ndarray:
    """Return the per-channel sample shift that dedisperses to the top of band.

    Shifts are rounded to whole samples, so the achievable DM resolution is
    limited by the time resolution of the data. `dedispersion_bin_resolution`
    reports the smallest DM step that moves the band edge by one sample.

    Parameters
    ----------
    dm
        Dispersion measure in pc cm^-3, relative to the current dedispersion of
        `data`.
    freqs_mhz
        Channel centre frequencies in MHz.
    tsamp_sec
        Sample interval in seconds.

    Returns
    -------
    numpy.ndarray
        Integer sample shift for each channel, positive towards later times.
    """
    freqs = np.asarray(freqs_mhz, dtype=np.float64)
    reffreq = np.max(freqs)
    time_shift = DM_CONSTANT_S_MHZ2 * dm * (reffreq**-2.0 - freqs**-2.0)
    return np.round(time_shift / tsamp_sec).astype(np.int64)


def dedispersion_bin_resolution(freqs_mhz: np.ndarray, tsamp_sec: float) -> float:
    """Return the smallest DM step that shifts the lowest channel by one sample.

    DM trials finer than this produce bit-identical dedispersed data, so a sweep
    stepped below it wastes work and reports a spuriously smooth score curve.
    """
    freqs = np.asarray(freqs_mhz, dtype=np.float64)
    reffreq = np.max(freqs)
    lowest = np.min(freqs)
    span = abs(reffreq**-2.0 - lowest**-2.0)
    if span == 0 or not np.isfinite(span):
        return float("inf")
    return float(tsamp_sec / (DM_CONSTANT_S_MHZ2 * span))


def _shift_channel(channel: np.ndarray, shift_bins: int, fill_value: float | None) -> np.ndarray:
    """Shift one channel, either wrapping or filling the vacated samples."""
    if fill_value is None or shift_bins == 0:
        return np.roll(channel, shift_bins)

    shifted = np.full_like(channel, fill_value)
    ntime = channel.size
    if abs(shift_bins) >= ntime:
        return shifted
    if shift_bins > 0:
        shifted[shift_bins:] = channel[:-shift_bins]
    else:
        shifted[:shift_bins] = channel[-shift_bins:]
    return shifted


def dedisperse(
    data: np.ndarray,
    dm: float,
    freqs_mhz: np.ndarray,
    tsamp_sec: float,
    *,
    fill_value: float | None = None,
) -> np.ndarray:
    """Dedisperse a dynamic spectrum to the top of the band.

    Parameters
    ----------
    data
        Dynamic spectrum with shape ``(n_channels, n_time)``.
    dm
        Dispersion measure in pc cm^-3, relative to the current dedispersion of
        `data`.
    freqs_mhz
        Channel centre frequencies in MHz.
    tsamp_sec
        Sample interval in seconds.
    fill_value
        What to put in samples vacated by the shift. ``None`` (the default)
        wraps them around the time axis, which is lossless and exactly
        reversible; that suits repeated in-place retuning of an already-loaded
        session. Pass a value (readers pass ``0.0``) for a one-shot
        dedispersion, where wrapped samples would otherwise fold burst power
        into the opposite edge of the read window and contaminate the off-pulse
        statistics computed there.

    Returns
    -------
    numpy.ndarray
        The dedispersed dynamic spectrum, same shape and dtype as `data`.
    """
    bin_shift = dedispersion_shift_bins(dm, freqs_mhz, tsamp_sec)
    return shift_channels(data, bin_shift, fill_value=fill_value)


def shift_channels(
    data: np.ndarray,
    shift_bins: np.ndarray,
    *,
    fill_value: float | None = None,
) -> np.ndarray:
    """Apply a per-channel integer sample shift to a dynamic spectrum.

    Separated from `dedisperse` so a session can retune its dispersion by the
    exact difference between two absolute shift solutions, rather than by
    re-rounding an incremental DM step each time.
    """
    shifted = np.zeros_like(data)
    for idx, bins in enumerate(shift_bins):
        shifted[idx, :] = _shift_channel(data[idx, :], int(bins), fill_value)
    return shifted


def dedispersion_edge_bins(
    dm: float, freqs_mhz: np.ndarray, tsamp_sec: float
) -> tuple[int, int]:
    """Return how many samples at each end are affected by the dedispersion shift.

    These are the samples whose content came from outside the read window. They
    are either wrapped-around data or fill values depending on how `dedisperse`
    was called, and in both cases they are not valid burst samples.

    Returns
    -------
    tuple of int
        ``(leading, trailing)`` sample counts to treat as invalid.
    """
    bin_shift = dedispersion_shift_bins(dm, freqs_mhz, tsamp_sec)
    if bin_shift.size == 0:
        return (0, 0)
    leading = int(max(0, int(np.max(bin_shift))))
    trailing = int(max(0, -int(np.min(bin_shift))))
    return (leading, trailing)


def block_reduce_mean(arr: np.ndarray, tfac: int = 1, ffac: int = 1) -> np.ndarray:
    tfac = max(1, int(tfac))
    ffac = max(1, int(ffac))
    if tfac == 1 and ffac == 1:
        return arr

    splicet = arr.shape[1] - (arr.shape[1] % tfac)
    splicef = arr.shape[0] - (arr.shape[0] % ffac)
    trimmed = arr[:splicef, :splicet]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if ffac > 1:
            trimmed = np.nanmean(trimmed.reshape(splicef // ffac, ffac, splicet), axis=1)
        if tfac > 1:
            trimmed = np.nanmean(
                trimmed.reshape(trimmed.shape[0], trimmed.shape[1] // tfac, tfac),
                axis=2,
            )
    return trimmed


def radiometer(tsamp_ms: float, bw_mhz: float, npol: int, sefd_jy: float) -> float:
    return sefd_jy * (1 / np.sqrt((bw_mhz * 1e6) * npol * tsamp_ms * 1e-3))


def acf_1d(array: np.ndarray) -> np.ndarray:
    return fftconvolve(array, array[::-1], mode="same")


def gaussian_1d(x: np.ndarray, amp: float, mu: float, sigma: float, offset: float) -> np.ndarray:
    return amp * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2)) + offset
