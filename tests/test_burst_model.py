"""The native fitburst model and its least-squares fitter.

None of these tests need fitburst. They check the model against closed-form
properties of its equations, the analytic Jacobian against finite differences,
and the fitter against known truths and against the scatter of repeated fits.
tests/test_fitburst_parity.py compares FLITS with fitburst itself.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

import flits.analysis.fitting.burst_model as burst_model
from flits.analysis.fitting.burst_model import (
    PARAMETER_NAMES,
    SCINTILLATION_LINEAR_PARAMETERS,
    BurstModel,
    BurstParameters,
    ParameterLayout,
)
from flits.analysis.fitting.least_squares import channel_weights, compare_fits, fit_burst_model
from flits.signal import DM_CONSTANT_S_MHZ2

FREQS = np.linspace(1200.0, 1600.0, 24)
TIMES = np.arange(160, dtype=float) * 2.5e-4


def _parameters(**overrides: object) -> BurstParameters:
    values: dict[str, object] = {
        "amplitude": [0.4],
        "arrival_time": [0.02],
        "burst_width": [0.0008],
        "ref_freq": [1400.0],
        "scattering_timescale": [0.0011],
        "spectral_index": [1.2],
        "spectral_running": [-4.0],
    }
    values.update(overrides)
    return BurstParameters.from_dict(values)


def _noisy(model: BurstModel, parameters: BurstParameters, seed: int, sigma: float = 1.0) -> np.ndarray:
    clean = model.evaluate(parameters, np.ones(model.shape) if model.scintillation else None)
    return clean + np.random.default_rng(seed).normal(0.0, sigma, size=model.shape)


# ----------------------------------------------------------------------
# The model against its equations


def test_unscattered_component_is_a_gaussian_with_a_running_power_law_spectrum() -> None:
    parameters = _parameters(scattering_timescale=[0.0], dm=[0.02], dm_index=[-2.0])
    model = BurstModel(FREQS, TIMES).evaluate(parameters)

    log_ratio = np.log(FREQS / 1400.0)
    spectrum = 10**0.4 * np.exp(1.2 * log_ratio - 4.0 * log_ratio**2)
    arrival = 0.02 + DM_CONSTANT_S_MHZ2 * 0.02 * (FREQS**-2.0 - 1400.0**-2.0)
    expected = spectrum[:, None] * np.exp(-0.5 * ((TIMES[None, :] - arrival[:, None]) / 0.0008) ** 2)
    np.testing.assert_allclose(model, expected, rtol=1e-12, atol=1e-15)


def test_a_dm_offset_shifts_each_channel_by_the_cold_plasma_delay() -> None:
    delay = DM_CONSTANT_S_MHZ2 * 0.05 * (FREQS**-2.0 - 1400.0**-2.0)
    shifted_times = TIMES[None, :] - delay[:, None]

    dispersed = BurstModel(FREQS, TIMES).evaluate(_parameters(dm=[0.05]))
    aligned = BurstModel(FREQS, shifted_times).evaluate(_parameters())
    np.testing.assert_allclose(dispersed, aligned, rtol=1e-10, atol=1e-14)


def test_scattered_profile_is_a_gaussian_convolved_with_a_one_sided_exponential() -> None:
    fine = np.arange(-0.01, 0.04, 2e-6)
    width, tau = 0.0008, 0.0011
    gaussian = np.exp(-0.5 * (fine / width) ** 2)
    kernel_times = np.arange(0.0, 0.03, 2e-6)
    kernel = np.exp(-kernel_times / tau) / tau * 2e-6
    convolved = np.convolve(gaussian, kernel)[: fine.size]

    parameters = _parameters(arrival_time=[0.0], spectral_index=[0.0], spectral_running=[0.0], amplitude=[0.0])
    normalized = BurstModel([1400.0, 1401.0], fine, normalize_pbf=True).evaluate(parameters)[0]
    np.testing.assert_allclose(normalized, convolved, atol=2e-3 * convolved.max())

    # fitburst's default prefactor is tau_ref / tau(nu) instead of sqrt(pi/2) width / tau(nu).
    default = BurstModel([1400.0, 1401.0], fine).evaluate(parameters)[0]
    np.testing.assert_allclose(default, normalized * tau / (np.sqrt(np.pi / 2) * width), rtol=1e-10)


def test_scattering_tail_follows_the_scattering_index() -> None:
    times = np.arange(0.0, 0.08, 1e-4)
    parameters = _parameters(arrival_time=[0.01], burst_width=[0.0002], scattering_index=[-4.4])
    model = BurstModel(FREQS[[0, -1]], times).evaluate(parameters)

    late = times > 0.03
    for row, freq in zip(model, FREQS[[0, -1]], strict=True):
        slope = np.polyfit(times[late], np.log(row[late]), 1)[0]
        assert -1.0 / slope == pytest.approx(0.0011 * (freq / 1400.0) ** -4.4, rel=1e-6)


def test_profile_stays_finite_far_from_the_burst_and_for_tiny_scattering() -> None:
    times = np.linspace(-1.0, 1.0, 400)
    for tau in (1e-12, 1e-3, 10.0):
        model = BurstModel(FREQS, times).evaluate(_parameters(arrival_time=[0.0], scattering_timescale=[tau]))
        assert np.all(np.isfinite(model))
        assert np.all(model >= 0.0)


def test_time_upsampling_averages_the_model_over_each_bin() -> None:
    factor = 4
    resolution = TIMES[1] - TIMES[0]
    sub_times = (TIMES[:, None] + resolution * ((np.arange(factor) + 0.5) / factor - 0.5)).ravel()
    fine = BurstModel(FREQS, sub_times).evaluate(_parameters())
    coarse = BurstModel(FREQS, TIMES, factor_time_upsample=factor).evaluate(_parameters())
    np.testing.assert_allclose(coarse, fine.reshape(FREQS.size, TIMES.size, factor).mean(axis=-1), rtol=1e-12)


def test_frequency_upsampling_averages_the_model_over_each_channel() -> None:
    factor = 3
    width = FREQS[1] - FREQS[0]
    sub_freqs = (FREQS[:, None] + width * ((np.arange(factor) + 0.5) / factor - 0.5)).ravel()
    parameters = _parameters(dm=[0.04])
    fine = BurstModel(sub_freqs, TIMES, channel_width_mhz=width / factor).evaluate(parameters)
    coarse = BurstModel(FREQS, TIMES, factor_freq_upsample=factor).evaluate(parameters)
    np.testing.assert_allclose(coarse, fine.reshape(FREQS.size, factor, TIMES.size).mean(axis=1), rtol=1e-12)


def test_incoherent_dm_adds_smearing_within_each_channel_only() -> None:
    parameters = _parameters()
    plain = BurstModel(FREQS, TIMES).evaluate(parameters)
    # Without sub-channels the incoherent DM cancels at each channel centre.
    np.testing.assert_allclose(BurstModel(FREQS, TIMES, dm_incoherent=500.0).evaluate(parameters), plain)
    smeared = BurstModel(FREQS, TIMES, dm_incoherent=500.0, factor_freq_upsample=8).evaluate(parameters)
    # Smearing spreads the pulse: lower peaks, the same total in each channel.
    assert np.all(
        smeared.max(axis=1) < BurstModel(FREQS, TIMES, factor_freq_upsample=8).evaluate(parameters).max(axis=1)
    )


def test_folded_profile_averages_this_period_and_the_next() -> None:
    period = TIMES.size * (TIMES[1] - TIMES[0])
    parameters = _parameters(arrival_time=[0.036], scattering_timescale=[0.004])
    folded = BurstModel(FREQS, TIMES, is_folded=True).evaluate(parameters)
    plain = BurstModel(FREQS, TIMES).evaluate(parameters)
    next_period = BurstModel(FREQS, TIMES + period).evaluate(parameters)
    np.testing.assert_allclose(folded, 0.5 * (plain + next_period), rtol=1e-12)


def test_components_add() -> None:
    two = _parameters(
        amplitude=[0.4, 0.1],
        arrival_time=[0.015, 0.024],
        burst_width=[0.0008, 0.0005],
        ref_freq=[1400.0, 1400.0],
        spectral_index=[1.2, -2.0],
        spectral_running=[-4.0, 0.0],
    )
    model = BurstModel(FREQS, TIMES)
    components = model.components(two)
    first = model.evaluate(_parameters(arrival_time=[0.015]))
    np.testing.assert_allclose(components[0], first, rtol=1e-12)
    np.testing.assert_allclose(model.evaluate(two), components.sum(axis=0), rtol=1e-12)


# ----------------------------------------------------------------------
# Derivatives


JACOBIAN_CASES = list(
    itertools.product(
        [False, True],  # scintillation
        [0.0, 0.0011],  # scattering timescale
        [1, 3],  # time upsampling
        [1, 2],  # frequency upsampling
        [False, True],  # folded
        [False, True],  # normalize_pbf
    )
)


@pytest.mark.parametrize(("scintillation", "tau", "time_up", "freq_up", "folded", "normalize"), JACOBIAN_CASES)
def test_jacobian_matches_finite_differences(
    scintillation: bool, tau: float, time_up: int, freq_up: int, folded: bool, normalize: bool
) -> None:
    parameters = _parameters(
        amplitude=[0.4, 0.1],
        arrival_time=[0.015, 0.024],
        burst_width=[0.0008, 0.0005],
        ref_freq=[1400.0, 1400.0],
        spectral_index=[1.2, -2.0],
        spectral_running=[-4.0, 0.0],
        scattering_timescale=[tau],
        dm=[0.03],
        dm_index=[-2.05],
        scattering_index=[-3.8],
    )
    model = BurstModel(
        FREQS[:12],
        TIMES[:128],
        dm_incoherent=200.0,
        factor_time_upsample=time_up,
        factor_freq_upsample=freq_up,
        is_folded=folded,
        scintillation=scintillation,
        normalize_pbf=normalize,
    )
    names = [name for name in PARAMETER_NAMES if not (scintillation and name in SCINTILLATION_LINEAR_PARAMETERS)]
    if tau == 0.0:
        names = [name for name in names if name not in ("scattering_timescale", "scattering_index")]
    layout = ParameterLayout(tuple(names), 2)
    data = _noisy(model, parameters, seed=1, sigma=0.3) if scintillation else None

    _model, jacobian = model.evaluate_with_jacobian(parameters, layout, data)
    vector = layout.vector(parameters)
    for column, (name, _component) in enumerate(layout.entries):
        step = 1e-6 * max(
            abs(vector[column]), 1e-3 if name in ("arrival_time", "burst_width", "scattering_timescale") else 1.0
        )
        up, down = vector.copy(), vector.copy()
        up[column] += step
        down[column] -= step
        numeric = (
            model.evaluate(layout.with_vector(parameters, up), data)
            - model.evaluate(layout.with_vector(parameters, down), data)
        ) / (2 * step)
        scale = max(float(np.max(np.abs(numeric))), 1e-300)
        assert float(np.max(np.abs(numeric - jacobian[column]))) / scale < 1e-5, layout.labels[column]


@pytest.mark.parametrize("normalize", [False, True])
def test_scattering_derivatives_reach_their_limits_as_tau_vanishes(normalize: bool) -> None:
    layout = ParameterLayout(("amplitude", "arrival_time", "burst_width", "scattering_timescale"), 1)
    model = BurstModel(FREQS, TIMES, normalize_pbf=normalize)
    scattering_scale = (FREQS / 1400.0) ** -4.0
    for tau in (1e-9, 1e-12):
        prediction, jacobian = model.evaluate_with_jacobian(_parameters(scattering_timescale=[tau]), layout)
        d_amplitude, d_arrival, d_width, d_tau = jacobian
        if normalize:
            # The profile tends to the Gaussian delayed by tau(nu).
            expected_tau = scattering_scale[:, None] * d_arrival
            gaussian = BurstModel(FREQS, TIMES).evaluate_with_jacobian(_parameters(scattering_timescale=[0.0]), layout)
            expected_width = gaussian[1][2]
        else:
            # fitburst's profile tends to sqrt(2/pi) tau_ref / width times the Gaussian.
            expected_tau = prediction / tau
            z = (TIMES[None, :] - 0.02) / 0.0008
            expected_width = prediction * (z**2 - 1.0) / 0.0008
        np.testing.assert_allclose(d_width, expected_width, atol=1e-5 * np.abs(expected_width).max())
        np.testing.assert_allclose(d_tau, expected_tau, atol=1e-5 * np.abs(expected_tau).max())
        np.testing.assert_allclose(d_amplitude, np.log(10.0) * prediction, rtol=1e-12)


def test_normal_equations_match_the_explicit_jacobian() -> None:
    parameters = _parameters()
    model = BurstModel(FREQS, TIMES)
    layout = ParameterLayout(("amplitude", "arrival_time", "burst_width", "scattering_timescale", "dm"), 1)
    data = _noisy(model, parameters, seed=2)
    weights = np.linspace(0.5, 1.5, FREQS.size)

    prediction, jacobian = model.evaluate_with_jacobian(parameters, layout)
    residual = (weights[:, None] * (data - prediction)).ravel()
    weighted = -(weights[None, :, None] * jacobian).reshape(layout.size, -1).T
    chisq, normal, gradient = model.normal_equations(parameters, layout, data, weights)

    assert chisq == pytest.approx(float(residual @ residual), rel=1e-12)
    np.testing.assert_allclose(normal, weighted.T @ weighted, rtol=1e-10)
    np.testing.assert_allclose(gradient, weighted.T @ residual, rtol=1e-8, atol=1e-8 * np.abs(gradient).max())


@pytest.mark.parametrize("scintillation", [False, True])
def test_threaded_blocks_give_the_single_block_result(monkeypatch: pytest.MonkeyPatch, scintillation: bool) -> None:
    parameters = _parameters()
    names = ("arrival_time", "burst_width", "scattering_timescale", "dm")
    if not scintillation:
        names = ("amplitude", *names, "spectral_index")
    layout = ParameterLayout(names, 1)
    data = _noisy(BurstModel(FREQS, TIMES, scintillation=scintillation), parameters, seed=3)
    single = BurstModel(FREQS, TIMES, scintillation=scintillation, threads=1)
    expected = single.evaluate_with_jacobian(parameters, layout, data)

    monkeypatch.setattr(burst_model, "PARALLEL_MIN_SAMPLES", 0)
    monkeypatch.setattr(burst_model, "MAX_BLOCK_SAMPLES", 3 * TIMES.size)
    threaded = BurstModel(FREQS, TIMES, scintillation=scintillation, threads=4)
    actual = threaded.evaluate_with_jacobian(parameters, layout, data)
    np.testing.assert_allclose(actual[0], expected[0], rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose(actual[1], expected[1], rtol=1e-12, atol=1e-12 * np.abs(expected[1]).max())


# ----------------------------------------------------------------------
# Parameters


def test_parameter_layout_uses_fitburst_order_and_labels() -> None:
    layout = ParameterLayout(("scattering_timescale", "burst_width", "dm", "amplitude"), 2)

    assert layout.fit_parameters == ("amplitude", "burst_width", "dm", "scattering_timescale")
    assert layout.labels == ("amplitude1", "amplitude2", "burst_width1", "burst_width2", "dm", "scattering_timescale")
    parameters = _parameters(
        amplitude=[0.4, 0.1], arrival_time=[0.015, 0.024], burst_width=[0.0008, 0.0005], ref_freq=[1400.0, 1400.0]
    )
    vector = layout.vector(parameters)
    assert layout.grouped(vector) == {
        "amplitude": [0.4, 0.1],
        "burst_width": [0.0008, 0.0005],
        "dm": [0.0],
        "scattering_timescale": [0.0011],
    }
    moved = layout.with_vector(parameters, vector + 1e-4)
    np.testing.assert_allclose(layout.vector(moved), vector + 1e-4)


def test_parameters_read_fitburst_dictionaries() -> None:
    parameters = BurstParameters.from_dict(
        {
            "amplitude": [0.1, 0.2],
            "arrival_time": [0.01, 0.02],
            "burst_width": [0.001],
            "ref_freq": 1400.0,
            "dm": [0.3, 0.3],
        }
    )
    assert parameters.num_components == 2
    np.testing.assert_array_equal(parameters.burst_width, [0.001, 0.001])
    assert parameters.dm == 0.3
    assert parameters.dm_index == -2.0 and parameters.scattering_index == -4.0
    assert parameters.to_dict()["dm"] == [0.3, 0.3]

    with pytest.raises(ValueError, match="missing burst_width"):
        BurstParameters.from_dict({"amplitude": [0.0], "arrival_time": [0.0], "ref_freq": [1400.0]})
    with pytest.raises(ValueError, match="3 values for 2 components"):
        BurstParameters.from_dict(
            {"amplitude": [0, 0, 0], "arrival_time": [0.0, 0.1], "burst_width": [0.001], "ref_freq": [1400.0]}
        )
    with pytest.raises(ValueError, match="Unknown model parameters"):
        ParameterLayout(("width",), 1)


# ----------------------------------------------------------------------
# Fits


FREE = ("amplitude", "arrival_time", "burst_width", "scattering_timescale")


def test_fit_recovers_a_two_component_burst_with_dm_and_spectrum() -> None:
    truth = _parameters(
        amplitude=[0.5, 0.2],
        arrival_time=[0.016, 0.022],
        burst_width=[0.0007, 0.0005],
        ref_freq=[1400.0, 1400.0],
        spectral_index=[1.5, -1.0],
        spectral_running=[-5.0, -2.0],
        dm=[0.02],
    )
    model = BurstModel(FREQS, TIMES)
    data = _noisy(model, truth, seed=5)
    start = _parameters(
        amplitude=[0.3, 0.3],
        arrival_time=[0.0163, 0.0217],
        burst_width=[0.0009, 0.0007],
        ref_freq=[1400.0, 1400.0],
        scattering_timescale=[0.0015],
        spectral_index=[0.5, 0.0],
        spectral_running=[-2.0, -1.0],
        dm=[0.0],
    )
    free = (*FREE, "dm", "spectral_index", "spectral_running")
    fit = fit_burst_model(model, data, start, free)

    assert fit.success
    assert fit.covariance_basis == "observed_hessian"
    assert fit.uncertainties is not None
    pulls = (fit.values - ParameterLayout(free, 2).vector(truth)) / fit.uncertainties
    assert np.all(np.abs(pulls) < 4.0), dict(zip(fit.labels, np.round(pulls, 2), strict=True))
    assert fit.fit_statistics["chisq_final_reduced"] == pytest.approx(1.0, abs=0.06)
    assert fit.parameters_at_bounds == ()


@pytest.mark.parametrize("scintillation", [False, True])
def test_reported_uncertainties_match_the_scatter_of_repeated_fits(scintillation: bool) -> None:
    freqs, times = FREQS[::2], TIMES[:100]
    truth = _parameters(spectral_index=[0.0], spectral_running=[0.0], amplitude=[0.6])
    clean = BurstModel(freqs, times).evaluate(truth)
    if scintillation:
        clean = clean * (1.0 + 0.8 * np.sin(np.arange(freqs.size) / 2.0))[:, None]
    model = BurstModel(freqs, times, scintillation=scintillation)
    free = FREE[1:] if scintillation else FREE
    values, sigmas = [], []
    for seed in range(40):
        data = clean + np.random.default_rng(100 + seed).normal(0.0, 1.0, clean.shape)
        fit = fit_burst_model(model, data, truth, free)
        assert fit.success and fit.uncertainties is not None
        values.append(fit.values)
        sigmas.append(fit.uncertainties)
    ratio = np.std(values, axis=0, ddof=1) / np.median(sigmas, axis=0)
    # 40 fits measure a standard deviation to about 11 per cent.
    assert np.all((ratio > 0.7) & (ratio < 1.35)), ratio


def test_scintillation_mode_recovers_the_channel_gains() -> None:
    gains = 1.0 + 0.7 * np.cos(np.arange(FREQS.size) / 1.7)
    truth = _parameters(spectral_index=[0.0], spectral_running=[0.0], amplitude=[0.0])
    clean = BurstModel(FREQS, TIMES).evaluate(truth) * gains[:, None]
    data = clean + np.random.default_rng(6).normal(0.0, 0.02, clean.shape)
    model = BurstModel(FREQS, TIMES, scintillation=True)
    start = _parameters(burst_width=[0.001], scattering_timescale=[0.0014])
    fit = fit_burst_model(model, data, start, FREE[1:], weights=np.full(FREQS.size, 50.0))

    assert fit.success and fit.channel_amplitudes is not None and fit.uncertainties is not None
    pulls = (fit.values - np.array([0.02, 0.0008, 0.0011])) / fit.uncertainties
    assert np.all(np.abs(pulls) < 4.0), pulls
    # In fitburst's convention a scattered profile's area grows with tau_ref,
    # so the solved amplitudes carry the factor tau_true / tau_fit.
    expected = gains * 0.0011 / fit.parameters.scattering_timescale
    np.testing.assert_allclose(fit.channel_amplitudes[:, 0], expected, rtol=0.08)
    assert np.corrcoef(fit.channel_amplitudes[:, 0], expected)[0, 1] > 0.998
    with pytest.raises(ValueError, match="solved per channel"):
        fit_burst_model(model, data, truth, FREE)


@pytest.mark.parametrize("normalize", [False, True])
def test_unresolved_scattering_ends_at_the_bound_with_the_gaussian_fit(normalize: bool) -> None:
    truth = _parameters(scattering_timescale=[0.0], amplitude=[1.0], spectral_index=[0.0], spectral_running=[0.0])
    data = _noisy(BurstModel(FREQS, TIMES), truth, seed=3)
    gaussian = fit_burst_model(BurstModel(FREQS, TIMES), data, truth, FREE[:3])
    scattered = fit_burst_model(
        BurstModel(FREQS, TIMES, normalize_pbf=normalize),
        data,
        _parameters(scattering_timescale=[0.0005], amplitude=[1.0], spectral_index=[0.0], spectral_running=[0.0]),
        FREE,
    )

    assert scattered.success
    assert "scattering_timescale" in scattered.parameters_at_bounds
    assert scattered.parameters.scattering_timescale < 1e-6
    assert scattered.parameters.burst_width[0] == pytest.approx(gaussian.parameters.burst_width[0], rel=1e-3)
    assert scattered.fit_statistics["chisq_final"] == pytest.approx(gaussian.fit_statistics["chisq_final"], rel=1e-6)
    assert scattered.uncertainties is not None
    # A one-sided scale on tau of a few hundredths of a millisecond at 0.25 ms resolution.
    assert 1e-5 < scattered.uncertainties[3] < 1e-4


def test_compare_fits_finds_the_number_of_components() -> None:
    two = _parameters(
        amplitude=[0.5, 0.3],
        arrival_time=[0.016, 0.021],
        burst_width=[0.0007, 0.0006],
        ref_freq=[1400.0, 1400.0],
        spectral_index=[0.0, 0.0],
        spectral_running=[0.0, 0.0],
    )
    model = BurstModel(FREQS, TIMES)
    data = _noisy(model, two, seed=8)
    single = fit_burst_model(
        model, data, _parameters(arrival_time=[0.017], spectral_index=[0.0], spectral_running=[0.0]), FREE
    )
    double = fit_burst_model(model, data, two, FREE)

    comparison = compare_fits([single, double])
    assert [row.index for row in comparison] == [0, 1]
    assert comparison[1].delta_bic == 0.0 and comparison[0].delta_bic > 10.0
    assert comparison[1].f_test_p_value is not None and comparison[1].f_test_p_value < 1e-6

    one = _parameters(spectral_index=[0.0], spectral_running=[0.0])
    data = _noisy(model, one, seed=9)
    single = fit_burst_model(model, data, one, FREE)
    double = fit_burst_model(model, data, two, FREE)
    comparison = compare_fits([double, single])
    assert [row.index for row in comparison] == [1, 0]
    assert comparison[0].delta_bic == 0.0
    assert comparison[1].f_test_p_value is not None and comparison[1].f_test_p_value > 1e-3


def test_channel_weights_follow_fitburst() -> None:
    data = np.random.default_rng(10).normal(0.0, 2.0, size=(5, 40))
    good = np.array([True, True, False, True, True])
    weighted = channel_weights(data, good)
    expected = 1.0 / np.sqrt(np.mean(data[:, :-1] ** 2, axis=1))
    np.testing.assert_allclose(weighted[good], expected[good])
    assert weighted[2] == 0.0
    ranged = channel_weights(data, good, weight_range=[5, 20])
    np.testing.assert_allclose(ranged[good], (1.0 / np.sqrt(np.mean(data[:, 5:20] ** 2, axis=1)))[good])
    np.testing.assert_array_equal(channel_weights(data, good, weighted=False), good.astype(float))


def test_finite_difference_jacobian_reaches_the_same_minimum() -> None:
    truth = _parameters()
    model = BurstModel(FREQS, TIMES)
    data = _noisy(model, truth, seed=11)
    exact = fit_burst_model(model, data, truth, FREE)
    numeric = fit_burst_model(model, data, truth, FREE, exact_jacobian=False)
    assert exact.uncertainties is not None
    np.testing.assert_allclose(numeric.values, exact.values, atol=1e-3 * float(exact.uncertainties.min()))


def test_the_evaluation_budget_ends_the_fit_with_status_zero() -> None:
    truth = _parameters()
    model = BurstModel(FREQS, TIMES)
    fit = fit_burst_model(
        model, _noisy(model, truth, seed=12), _parameters(burst_width=[0.002]), FREE, max_function_evaluations=2
    )
    assert not fit.success
    assert fit.status == 0
    assert fit.function_evaluations == 2
    assert fit.message == "The maximum number of function evaluations is exceeded."


def test_fit_rejects_invalid_requests() -> None:
    model = BurstModel(FREQS, TIMES)
    data = np.zeros(model.shape)
    with pytest.raises(ValueError, match="Start scattering_timescale above zero"):
        fit_burst_model(model, data, _parameters(scattering_timescale=[0.0]), FREE)
    with pytest.raises(ValueError, match="At least one"):
        fit_burst_model(model, data, _parameters(), ())
    with pytest.raises(ValueError, match="does not match"):
        fit_burst_model(model, data[:, :10], _parameters(), FREE)
    masked = fit_burst_model(model, data, _parameters(), FREE, weights=np.zeros(FREQS.size))
    assert not masked.success and "masked" in masked.message
