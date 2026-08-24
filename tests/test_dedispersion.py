"""Tests for the dedispersion primitive and the session's dispersion state."""

from __future__ import annotations

import numpy as np
import pytest

from flits.session import BurstSession
from flits.signal import (
    dedisperse,
    dedispersion_bin_resolution,
    dedispersion_edge_bins,
    dedispersion_shift_bins,
    shift_channels,
)


FREQS_MHZ = np.linspace(1500.0, 1200.0, 64)
TSAMP_SEC = 1e-3


class TestShiftAccounting:
    def test_shifts_are_zero_at_zero_dm(self) -> None:
        shifts = dedispersion_shift_bins(0.0, FREQS_MHZ, TSAMP_SEC)
        assert np.all(shifts == 0)

    def test_top_of_band_is_the_reference_channel(self) -> None:
        shifts = dedispersion_shift_bins(100.0, FREQS_MHZ, TSAMP_SEC)
        # The highest frequency defines the reference, so it never moves.
        assert shifts[int(np.argmax(FREQS_MHZ))] == 0

    def test_lower_channels_move_further(self) -> None:
        shifts = dedispersion_shift_bins(100.0, FREQS_MHZ, TSAMP_SEC)
        assert abs(shifts[-1]) > abs(shifts[len(shifts) // 2]) > 0

    def test_shifts_negate_with_dm_sign(self) -> None:
        positive = dedispersion_shift_bins(50.0, FREQS_MHZ, TSAMP_SEC)
        negative = dedispersion_shift_bins(-50.0, FREQS_MHZ, TSAMP_SEC)
        np.testing.assert_array_equal(positive, -negative)

    def test_bin_resolution_is_the_one_sample_dm_step(self) -> None:
        resolution = dedispersion_bin_resolution(FREQS_MHZ, TSAMP_SEC)
        assert resolution > 0

        # A DM step of half the resolution leaves every channel where it was.
        below = dedispersion_shift_bins(resolution * 0.4, FREQS_MHZ, TSAMP_SEC)
        assert np.all(below == 0)

        # A full step moves the lowest channel by exactly one sample.
        at_resolution = dedispersion_shift_bins(resolution, FREQS_MHZ, TSAMP_SEC)
        assert abs(at_resolution[int(np.argmin(FREQS_MHZ))]) == 1


class TestEdgeHandling:
    """Samples shifted in from beyond the read window are not real data."""

    @staticmethod
    def _burst_at(ntime: int, index: int) -> np.ndarray:
        data = np.zeros((FREQS_MHZ.size, ntime), dtype=np.float32)
        data[:, index] = 10.0
        return data

    def test_wrapping_is_the_default_and_is_reversible(self) -> None:
        data = self._burst_at(256, 4)
        out = dedisperse(data, 200.0, FREQS_MHZ, TSAMP_SEC)
        back = dedisperse(out, -200.0, FREQS_MHZ, TSAMP_SEC)
        np.testing.assert_array_equal(back, data)

    def test_fill_value_keeps_power_from_reappearing_at_the_far_edge(self) -> None:
        # Dedispersing to the top of the band moves lower channels earlier, so
        # a burst near the start of the window is pushed off the front. Wrapping
        # folds that power into the trailing samples, which is exactly where
        # off-pulse statistics are usually taken.
        data = self._burst_at(256, 2)

        wrapped = dedisperse(data, 20.0, FREQS_MHZ, TSAMP_SEC)
        filled = dedisperse(data, 20.0, FREQS_MHZ, TSAMP_SEC, fill_value=0.0)

        leading, trailing = dedispersion_edge_bins(20.0, FREQS_MHZ, TSAMP_SEC)
        assert trailing > 2, "the burst must be pushed past the front of the window"

        assert wrapped[:, -trailing:].max() > 0, "wrapping should fold power to the far edge"
        assert filled[:, -trailing:].max() == 0, "fill_value should leave the far edge empty"

    def test_total_power_is_conserved_when_wrapping(self) -> None:
        data = self._burst_at(256, 128)
        out = dedisperse(data, 150.0, FREQS_MHZ, TSAMP_SEC)
        assert out.sum() == pytest.approx(data.sum())

    def test_edge_bins_match_the_applied_shift(self) -> None:
        shifts = dedispersion_shift_bins(300.0, FREQS_MHZ, TSAMP_SEC)
        leading, trailing = dedispersion_edge_bins(300.0, FREQS_MHZ, TSAMP_SEC)
        assert leading == max(0, int(shifts.max()))
        assert trailing == max(0, -int(shifts.min()))

    def test_shift_larger_than_the_window_fills_everything(self) -> None:
        data = np.ones((2, 8), dtype=np.float32)
        out = shift_channels(data, np.array([100, -100]), fill_value=0.0)
        assert out.sum() == 0


class TestSessionDmRetuning:
    """Retuning the DM must not accumulate rounding error."""

    @staticmethod
    def _session(synthetic_waterfall) -> BurstSession:
        return BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0)

    def test_dm_round_trip_restores_the_data_exactly(self, synthetic_waterfall) -> None:
        session = self._session(synthetic_waterfall)
        original = session.data.copy()

        session.set_dm(527.65)
        assert not np.array_equal(session.data, original)

        session.set_dm(0.0)
        np.testing.assert_array_equal(session.data, original)

    def test_many_small_steps_match_one_large_step(self, synthetic_waterfall) -> None:
        """A DM sweep visits many trials; the endpoint must not depend on the path.

        Each step is far smaller than one sample shift, so rounding each step
        independently -- as an incremental implementation does -- loses the
        motion entirely and lands somewhere different from a single jump.
        """
        stepped = self._session(synthetic_waterfall)
        for index in range(1, 21):
            stepped.set_dm(index * 2.5)

        direct = self._session(synthetic_waterfall)
        direct.set_dm(50.0)

        assert stepped.dm == direct.dm == 50.0
        np.testing.assert_array_equal(stepped.data, direct.data)

    def test_walking_a_sweep_and_returning_is_lossless(self, synthetic_waterfall) -> None:
        session = self._session(synthetic_waterfall)
        original = session.data.copy()

        for dm in (10.0, 25.0, 60.0, 125.0, 60.0, 25.0, 10.0, 0.0):
            session.set_dm(dm)

        np.testing.assert_array_equal(session.data, original)

    def test_setting_the_same_dm_is_a_no_op(self, synthetic_waterfall) -> None:
        session = self._session(synthetic_waterfall)
        session.set_dm(30.0)
        snapshot = session.data.copy()
        session.set_dm(30.0)
        np.testing.assert_array_equal(session.data, snapshot)
