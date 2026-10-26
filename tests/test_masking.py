"""Automatic interference masking from per-channel statistics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy import stats

from flits.analysis.masking import bandpass_trend, channel_statistic, flag_outlying_channels, running_median
from flits.models import FilterbankMetadata
from flits.session import BurstSession
from flits.settings import ObservationConfig


def _noise_with_bandpass(ntime: int = 4000, nchan: int = 256, seed: int = 0) -> np.ndarray:
    """Gaussian noise whose level and scatter follow a smooth bandpass."""
    rng = np.random.default_rng(seed)
    bandpass = 1.0 + 0.8 * np.sin(np.linspace(0.0, np.pi, nchan))
    return (rng.standard_normal((ntime, nchan)) * bandpass + 10.0 * bandpass).astype(np.float32)


def test_statistics_match_their_definitions() -> None:
    spectra = _noise_with_bandpass(ntime=500, nchan=16)
    np.testing.assert_allclose(channel_statistic(spectra, "skew"), stats.skew(spectra.astype(float), axis=0), rtol=1e-9)
    np.testing.assert_allclose(channel_statistic(spectra, "stand-dev"), spectra.astype(float).std(axis=0), rtol=1e-8)
    with pytest.raises(ValueError):
        channel_statistic(spectra, "kurtosis")


def test_intermittent_interference_is_flagged_by_skewness() -> None:
    spectra = _noise_with_bandpass()
    rng = np.random.default_rng(1)
    for channel in (40, 41, 200):
        bursts = rng.random(spectra.shape[0]) < 0.01
        spectra[bursts, channel] += 25.0

    flagged = np.flatnonzero(flag_outlying_channels(spectra, "skew"))

    assert {40, 41, 200} <= set(flagged.tolist())
    assert len(flagged) <= 5


def test_excess_scatter_is_flagged_and_the_bandpass_is_not() -> None:
    spectra = _noise_with_bandpass()
    spectra[:, 120] *= 3.0

    flagged = flag_outlying_channels(spectra, "stand-dev")

    # The bandpass changes the scatter by a factor 1.8 across the band, yet only
    # channels that stand out from their neighbours are flagged: the 3x channel,
    # plus the few noise channels a 3-sigma cut takes from 256 (about four, after
    # the running median makes the residuals peaked; jess takes about six).
    assert flagged[120]
    assert flagged.sum() <= 8
    assert not flagged[:5].any() and not flagged[-5:].any()


def test_a_block_of_interference_at_the_band_edge_is_flagged() -> None:
    # The guided-workflow GBT burst has interference in its nine lowest channels;
    # a one-sided running median there followed the interference and missed it.
    spectra = _noise_with_bandpass()
    rng = np.random.default_rng(3)
    for channel in range(247, 256):
        spectra[rng.random(spectra.shape[0]) < 0.02, channel] += 20.0

    flagged = np.flatnonzero(flag_outlying_channels(spectra, "skew"))

    assert set(range(247, 256)) <= set(flagged.tolist())
    assert len(flagged) <= 13


def test_a_block_of_interference_inside_the_band_is_flagged() -> None:
    spectra = _noise_with_bandpass()
    rng = np.random.default_rng(4)
    for channel in range(100, 112):
        spectra[rng.random(spectra.shape[0]) < 0.02, channel] += 20.0

    flagged = flag_outlying_channels(spectra, "skew")

    assert flagged[100:112].all()
    assert flagged.sum() <= 16


def test_a_sloping_bandpass_does_not_flag_its_edges() -> None:
    spectra = _noise_with_bandpass()
    spectra *= np.linspace(0.5, 2.0, spectra.shape[1], dtype=np.float32)

    flagged = flag_outlying_channels(spectra, "stand-dev")

    assert not flagged[:16].any() and not flagged[-16:].any()


def test_bandpass_trend_follows_a_line_exactly_at_the_edges() -> None:
    line = 3.0 + 0.25 * np.arange(100.0)
    np.testing.assert_allclose(bandpass_trend(line, 31), line)
    np.testing.assert_allclose(running_median(line, 31)[15:-15], line[15:-15])


def test_nothing_is_flagged_without_an_outlier_scale() -> None:
    assert not flag_outlying_channels(np.ones((50, 8), dtype=np.float32), "stand-dev").any()
    assert not flag_outlying_channels(np.zeros((50, 2), dtype=np.float32), "skew").any()


def _session(data: np.ndarray) -> BurstSession:
    nchan, ntime = data.shape
    metadata = FilterbankMetadata(
        source_path=Path("masking.fil"),
        source_name="synthetic",
        tsamp=1e-3,
        freqres=1.0,
        start_mjd=60000.0,
        read_start_sec=0.0,
        sefd_jy=None,
        bandwidth_mhz=float(nchan),
        npol=1,
        freqs_mhz=np.linspace(1500.0, 1500.0 - nchan + 1, nchan),
        header_npol=1,
        telescope_id=None,
        machine_id=None,
        detected_preset_key="generic",
        detection_basis="synthetic",
    )
    return BurstSession(
        config=ObservationConfig.from_preset(dm=0.0, preset_key="generic"),
        metadata=metadata,
        data=data,
        crop_start=0,
        crop_end=ntime,
        event_start=ntime // 2 - 50,
        event_end=ntime // 2 + 50,
        spec_ex_lo=0,
        spec_ex_hi=nchan - 1,
        channel_mask=np.zeros(nchan, dtype=bool),
    )


def test_session_masks_interference_channels_and_keeps_the_old_name() -> None:
    data = _noise_with_bandpass(ntime=3000, nchan=128).T.copy()
    rng = np.random.default_rng(2)
    for channel in (10, 90):
        data[channel, rng.random(data.shape[1]) < 0.02] += 30.0

    session = _session(data)
    session.auto_mask("auto")

    masked = np.flatnonzero(session.channel_mask)
    assert {10, 90} <= set(masked.tolist()) and len(masked) <= 4
    assert session.last_auto_mask.test_used == "skew"

    renamed = _session(data)
    renamed.auto_mask_jess("auto")
    np.testing.assert_array_equal(renamed.channel_mask, session.channel_mask)
