from __future__ import annotations

import unittest

import numpy as np

from flits.analysis import HaarExcessPowerResult
from flits.analysis.temporal.multiscale import (
    excess_power_fraction_below,
    haar_excess_power,
)


def _pulse(width_bins: float, *, amplitude: float, seed: int) -> tuple[np.ndarray, list[np.ndarray]]:
    rng = np.random.default_rng(seed)
    time = np.arange(512, dtype=float)
    event = rng.normal(0.0, 1.0, time.size)
    event += amplitude * np.exp(-0.5 * ((time - 256.0) / width_bins) ** 2)
    noise = [rng.normal(0.0, 1.0, 2048), rng.normal(0.0, 1.0, 2048)]
    return event, noise


class HaarExcessPowerTest(unittest.TestCase):
    def test_result_is_available_from_public_analysis_api(self) -> None:
        self.assertEqual(HaarExcessPowerResult.__name__, "HaarExcessPowerResult")

    def test_broader_pulse_moves_characteristic_scale_up(self) -> None:
        narrow, narrow_noise = _pulse(4.0, amplitude=12.0, seed=1)
        broad, broad_noise = _pulse(32.0, amplitude=12.0, seed=2)

        narrow_result = haar_excess_power(narrow, narrow_noise, tsamp_ms=0.1)
        broad_result = haar_excess_power(broad, broad_noise, tsamp_ms=0.1)

        self.assertEqual(narrow_result.status, "ok")
        self.assertEqual(broad_result.status, "ok")
        self.assertIsNotNone(narrow_result.characteristic_scale_ms)
        self.assertIsNotNone(broad_result.characteristic_scale_ms)
        assert narrow_result.characteristic_scale_ms is not None
        assert broad_result.characteristic_scale_ms is not None
        self.assertGreater(broad_result.characteristic_scale_ms, narrow_result.characteristic_scale_ms)

    def test_characteristic_scale_is_stable_to_amplitude_at_high_sn(self) -> None:
        rng = np.random.default_rng(3)
        time = np.arange(512, dtype=float)
        measurement_noise = rng.normal(0.0, 1.0, time.size)
        shape = np.exp(-0.5 * ((time - 256.0) / 10.0) ** 2)
        low = measurement_noise + 10.0 * shape
        high = measurement_noise + 20.0 * shape
        noise = [rng.normal(0.0, 1.0, 4096)]

        low_result = haar_excess_power(low, noise, tsamp_ms=0.05)
        high_result = haar_excess_power(high, noise, tsamp_ms=0.05)

        assert low_result.characteristic_scale_ms is not None
        assert high_result.characteristic_scale_ms is not None
        ratio = high_result.characteristic_scale_ms / low_result.characteristic_scale_ms
        self.assertAlmostEqual(ratio, 1.0, delta=0.12)

    def test_offpulse_runs_are_not_joined_across_boundaries(self) -> None:
        event, _ = _pulse(8.0, amplitude=10.0, seed=4)
        result = haar_excess_power(
            event,
            [np.zeros(64), np.full(64, 100.0)],
            tsamp_ms=1.0,
            scales_bins=[1, 2, 4, 8],
        )

        np.testing.assert_allclose(result.noise_power, 0.0)

    def test_fine_scale_fraction_and_serialization(self) -> None:
        event, noise = _pulse(6.0, amplitude=12.0, seed=5)
        result = haar_excess_power(event, noise, tsamp_ms=0.1)

        fraction = excess_power_fraction_below(result, 1.0)
        self.assertIsNotNone(fraction)
        assert fraction is not None
        self.assertGreaterEqual(fraction, 0.0)
        self.assertLessEqual(fraction, 1.0)
        self.assertEqual(result.to_dict()["scales_bins"], result.scales_bins.tolist())

    def test_input_validation_and_short_event(self) -> None:
        with self.assertRaises(ValueError):
            haar_excess_power([1, 2, 3, 4], [[0, 1, 0, 1]], tsamp_ms=0.0)
        with self.assertRaises(ValueError):
            haar_excess_power([1, 2, 3, 4], [[0, 1, 0, 1]], tsamp_ms=1.0, scales_bins=[0])

        result = haar_excess_power([1, 2, 3], [[0, 1, 0, 1]], tsamp_ms=1.0)
        self.assertEqual(result.status, "insufficient_event")

        result = haar_excess_power([1, 2, np.nan, 4], [[0, 1, 0, 1]], tsamp_ms=1.0)
        self.assertEqual(result.status, "nonfinite_event")


if __name__ == "__main__":
    unittest.main()
