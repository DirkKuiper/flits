"""Dedispersion on load must not let vacated samples into the normalization statistics."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import write_dispersed_filterbank

from flits.measure import MeasureOptions, measure_burst
from flits.session import BurstSession
from flits.signal import (
    dedisperse_and_normalize,
    dedispersion_shift_bins,
    normalize,
    normalize_stokes,
)


def _offpulse_std(session: BurstSession, burst_time_s: float) -> np.ndarray:
    """Per-channel deviation well before the burst, where every channel holds noise."""
    stop = session.ms_to_bin(burst_time_s * 1000.0 - 100.0)
    return session.data[:, :stop].std(axis=1)


def test_a_baseline_offset_does_not_change_the_measured_burst(tmp_path):
    zero = write_dispersed_filterbank(tmp_path / "zero.fil", baseline=0.0)
    offset = write_dispersed_filterbank(tmp_path / "offset.fil", baseline=100.0)

    results = [measure_burst(burst.path, MeasureOptions(dm=burst.dm)) for burst in (zero, offset)]

    assert [result["status"] for result in results] == ["ok", "ok"]
    snr_zero, snr_offset = (result["measurements"]["snr_peak"] for result in results)
    # Before the fix the zero fill, 100 units below the baseline, inflated the
    # low channels' deviation about twentyfold and the peak S/N fell to ~0.4.
    assert snr_zero == pytest.approx(44.6, abs=0.5)
    assert snr_offset == pytest.approx(snr_zero, rel=1e-3)


@pytest.mark.parametrize("nbits", [8, 32])
def test_every_channel_is_normalized_to_unit_deviation(tmp_path, nbits):
    burst = write_dispersed_filterbank(
        tmp_path / "burst.fil", baseline=100.0, noise_std=8.0, burst_amp=24.0, nbits=nbits
    )
    session = BurstSession.from_file(str(burst.path), dm=burst.dm)

    std = _offpulse_std(session, burst.burst_time_s)
    np.testing.assert_allclose(std, 1.0, atol=0.05)


def test_vacated_samples_are_zero_after_normalization(tmp_path):
    burst = write_dispersed_filterbank(tmp_path / "burst.fil", baseline=100.0)
    session = BurstSession.from_file(str(burst.path), dm=burst.dm)

    shifts = dedispersion_shift_bins(burst.dm, burst.freqs_mhz, burst.tsamp_s)
    lowest = int(np.argmin(burst.freqs_mhz))
    vacated = -int(shifts[lowest])
    assert vacated == 246  # 0.246 s sweep across 1248-1500 MHz at DM 300, in 1 ms samples
    assert np.all(session.data[lowest, -vacated:] == 0.0)
    assert np.all(session.data[lowest, -vacated - 50 : -vacated] != 0.0)


def test_without_dispersion_it_matches_the_plain_normalization():
    rng = np.random.default_rng(3)
    values = rng.normal(50.0, 4.0, size=(16, 400)).astype(np.float32)
    tail = values[:, 300:]

    result = dedisperse_and_normalize(values, 0.0, np.linspace(1500, 1400, 16), 1e-3, 0.25)

    np.testing.assert_array_equal(result, normalize(values, tail).astype(np.float32))
    assert values[0, 0] != result[0, 0]  # the input is not modified


def test_stokes_cube_matches_normalize_stokes_without_dispersion():
    rng = np.random.default_rng(4)
    cube = rng.normal(10.0, 2.0, size=(4, 8, 200)).astype(np.float32)

    result = dedisperse_and_normalize(cube, 0.0, np.linspace(1500, 1400, 8), 1e-3, 0.25)

    np.testing.assert_allclose(result, normalize_stokes(cube, cube[..., 150:]), rtol=1e-6, atol=1e-6)


def test_stokes_cube_shares_the_stokes_i_shift_and_scale():
    rng = np.random.default_rng(5)
    stokes_i = rng.normal(100.0, 5.0, size=(8, 600)).astype(np.float32)
    cube = np.stack([stokes_i, 0.5 * stokes_i, -0.25 * stokes_i, 0.1 * stokes_i])
    freqs = np.linspace(1500, 1200, 8)

    result = dedisperse_and_normalize(cube, 200.0, freqs, 1e-3, 0.25)
    expected_i = dedisperse_and_normalize(stokes_i, 200.0, freqs, 1e-3, 0.25)

    np.testing.assert_array_equal(result[0], expected_i)
    # Q is half of I, so after the common scale it stays half of I.
    np.testing.assert_allclose(result[1], 0.5 * expected_i, atol=1e-5)
