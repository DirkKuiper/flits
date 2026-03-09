from __future__ import annotations

import warnings

import numpy as np
from scipy.signal import fftconvolve


def normalize(ds: np.ndarray, offpulse: np.ndarray) -> np.ndarray:
    ds = ds.copy().astype(float)
    offpulse = offpulse.copy().astype(float)
    normalized = np.zeros_like(ds)

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


def normalise(ds: np.ndarray, t_cent: int, t_sig: int) -> np.ndarray:
    ds = ds.copy()
    ds_off = np.concatenate((ds[:, 0 : int(t_cent - 3 * t_sig)], ds[:, int(t_cent + 3 * t_sig) :]), axis=1)
    for chan in range(ds_off.shape[0]):
        ds[chan, :] = ds[chan, :] - np.mean(ds_off[chan, :])
        ds_off[chan, :] = ds_off[chan, :] - np.mean(ds_off[chan, :])
        std = np.std(ds_off[chan, :])
        if std != 0:
            ds[chan, :] = ds[chan, :] / std
        else:
            ds[chan, :] = 0
    return ds


def dedisperse(data: np.ndarray, dm: float, freqs_mhz: np.ndarray, tsamp_sec: float) -> np.ndarray:
    freqs = freqs_mhz.astype(np.float64)
    reffreq = np.max(freqs)
    shifted = np.zeros_like(data)
    dmconst = 1 / (2.41 * 10 ** -4)
    time_shift = dmconst * dm * (reffreq ** -2.0 - freqs ** -2.0)
    bin_shift = np.round(time_shift / tsamp_sec).astype(np.int32)
    for idx, shift_bins in enumerate(bin_shift):
        shifted[idx, :] = np.roll(data[idx, :], shift_bins)
    return shifted


def block_reduce_mean(arr: np.ndarray, tfac: int = 1, ffac: int = 1) -> np.ndarray:
    tfac = max(1, int(tfac))
    ffac = max(1, int(ffac))
    if tfac == 1 and ffac == 1:
        return arr.copy()

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


def acf_2d(array: np.ndarray) -> np.ndarray:
    return fftconvolve(array, array[::-1, ::-1], mode="same")


def acf_1d(array: np.ndarray) -> np.ndarray:
    return fftconvolve(array, array[::-1], mode="same")


def gaussian_2d(xy: tuple[np.ndarray, np.ndarray], amp: float, x0: float, y0: float, sigma_x: float, sigma_y: float, offset: float) -> np.ndarray:
    x, y = xy
    return amp * np.exp(-(((x - x0) ** 2) / (2 * sigma_x ** 2) + ((y - y0) ** 2) / (2 * sigma_y ** 2))) + offset


def gaussian_1d(x: np.ndarray, amp: float, mu: float, sigma: float, offset: float) -> np.ndarray:
    return amp * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2)) + offset
