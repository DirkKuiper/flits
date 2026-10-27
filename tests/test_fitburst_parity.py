"""FLITS's native fitburst model against fitburst itself.

fitburst is the reference implementation of the model (Fonseca et al. 2024).
These tests need it installed (``requirements-dev.txt`` pins the revision) and
skip otherwise. They check that both programs compute the same dynamic
spectrum in every model mode, and that a fit of the same data reaches the same
minimum with the same uncertainties.

fitburst clamps times earlier than five widths before arrival to that value
when scattering is on, which leaves a floor of about 1e-5 of the peak before
each component. FLITS evaluates the expression as written, so the comparison
allows for that floor everywhere and is exact after the clamp.
"""

from __future__ import annotations

import contextlib
import io
import itertools

import numpy as np
import pytest

from flits.analysis.fitting.burst_model import BurstModel, BurstParameters
from flits.analysis.fitting.least_squares import fit_burst_model

fitburst_model = pytest.importorskip("fitburst.analysis.model")
fitburst_fitter = pytest.importorskip("fitburst.analysis.fitter")

FREQS = np.linspace(1200.0, 1600.0, 48)
TIMES = np.arange(120, dtype=float) * 3e-4


def _parameters(count: int, tau: float, seed: int = 0) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    return {
        "amplitude": list(rng.uniform(-0.5, 0.5, count)),
        "arrival_time": list(0.012 + 0.006 * np.arange(count)),
        "burst_width": list(rng.uniform(0.0006, 0.0015, count)),
        "dm": [0.03] * count,
        "dm_index": [-2.1] * count,
        "ref_freq": [1450.0] * count,
        "scattering_timescale": [tau] * count,
        "scattering_index": [-3.7] * count,
        "spectral_index": list(rng.uniform(-2.0, 2.0, count)),
        "spectral_running": list(rng.uniform(-8.0, 0.0, count)),
    }


MODEL_CASES = [
    case
    for case in itertools.product(
        [1, 3],  # components
        [0.0, 0.0015],  # scattering timescale
        [1, 3],  # time upsampling
        [1, 2],  # frequency upsampling
        [False, True],  # folded
        [False, True],  # scintillation
        [0.0, 300.0],  # incoherent DM
        [False, True],  # a time axis per channel
    )
    # fitburst applies the incoherent DM only on a shared time axis.
    if not (case[6] and case[7])
]


@pytest.mark.parametrize(
    ("count", "tau", "time_up", "freq_up", "folded", "scintillation", "dm_incoherent", "per_channel_times"),
    MODEL_CASES,
)
def test_model_matches_fitburst(
    count: int,
    tau: float,
    time_up: int,
    freq_up: int,
    folded: bool,
    scintillation: bool,
    dm_incoherent: float,
    per_channel_times: bool,
) -> None:
    parameters = _parameters(count, tau)
    times = TIMES[None, :] + 2e-4 * np.arange(FREQS.size)[:, None] if per_channel_times else TIMES
    reference = fitburst_model.SpectrumModeler(
        FREQS,
        times,
        dm_incoherent=dm_incoherent,
        factor_freq_upsample=freq_up,
        factor_time_upsample=time_up,
        num_components=count,
        is_folded=folded,
        scintillation=scintillation,
    )
    reference.update_parameters(parameters)
    data = np.random.default_rng(1).normal(5.0, 1.0, (FREQS.size, TIMES.size))
    expected = reference.compute_model(data=data)

    model = BurstModel(
        FREQS,
        times,
        dm_incoherent=dm_incoherent,
        factor_freq_upsample=freq_up,
        factor_time_upsample=time_up,
        is_folded=folded,
        scintillation=scintillation,
    )
    actual = model.evaluate(BurstParameters.from_dict(parameters), data)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-5 * np.abs(expected).max())


def test_model_matches_fitburst_exactly_after_its_clamp() -> None:
    parameters = _parameters(1, 0.0015)
    reference = fitburst_model.SpectrumModeler(FREQS, TIMES, num_components=1)
    reference.update_parameters(parameters)
    expected = reference.compute_model()
    actual = BurstModel(FREQS, TIMES).evaluate(BurstParameters.from_dict(parameters))

    from flits.signal import DM_CONSTANT_S_MHZ2

    arrival = parameters["arrival_time"][0] + DM_CONSTANT_S_MHZ2 * 0.03 * (FREQS**-2.1 - 1450.0**-2.1)
    after_clamp = TIMES[None, :] > arrival[:, None] - 5 * parameters["burst_width"][0]
    np.testing.assert_allclose(actual[after_clamp], expected[after_clamp], rtol=1e-10)
    # Before it, fitburst's model sits on its constant floor.
    assert np.max(np.abs(actual - expected)) < 1e-5 * expected.max()


FIT_CASES = {
    "one component, default free set": (1, ("amplitude", "arrival_time", "burst_width", "scattering_timescale")),
    "one component with DM and spectrum": (
        1,
        (
            "amplitude",
            "arrival_time",
            "burst_width",
            "dm",
            "scattering_timescale",
            "spectral_index",
            "spectral_running",
        ),
    ),
    "two components with DM and spectrum": (
        2,
        (
            "amplitude",
            "arrival_time",
            "burst_width",
            "dm",
            "scattering_timescale",
            "spectral_index",
            "spectral_running",
        ),
    ),
}


def _fit_case(count: int, free: tuple[str, ...]) -> tuple[np.ndarray, dict[str, list[float]], dict[str, list[float]]]:
    freqs = np.linspace(1200.0, 1600.0, 128)
    times = np.arange(240, dtype=float) * 2.5e-4
    truth = {
        "amplitude": [0.6] * count,
        "arrival_time": list(0.03 + 0.004 * np.arange(count)),
        "burst_width": [0.0007] * count,
        "dm": [0.0] * count,
        "dm_index": [-2.0] * count,
        "ref_freq": [1400.0] * count,
        "scattering_timescale": [0.0012] * count,
        "scattering_index": [-4.0] * count,
        "spectral_index": [1.5] * count,
        "spectral_running": [-6.0] * count,
    }
    clean = BurstModel(freqs, times).evaluate(BurstParameters.from_dict(truth))
    data = clean + np.random.default_rng(1).normal(0.0, 1.0, clean.shape)
    start = {key: list(values) for key, values in truth.items()}
    start["amplitude"] = [0.4] * count
    start["arrival_time"] = [value + 0.0003 for value in truth["arrival_time"]]
    start["burst_width"] = [0.0009] * count
    start["scattering_timescale"] = [0.0016] * count
    if "dm" in free:
        start["dm"] = [0.05] * count
    if "spectral_index" in free:
        start["spectral_index"] = [0.5] * count
        start["spectral_running"] = [-3.0] * count
    return data, start, truth


@pytest.mark.parametrize("case", list(FIT_CASES))
def test_fit_reaches_fitburst_minimum_and_uncertainties(case: str) -> None:
    count, free = FIT_CASES[case]
    data, start, _truth = _fit_case(count, free)
    freqs = np.linspace(1200.0, 1600.0, 128)
    times = np.arange(240, dtype=float) * 2.5e-4

    reference_model = fitburst_model.SpectrumModeler(freqs, times, num_components=count, is_dedispersed=True)
    reference_model.update_parameters(start)
    with contextlib.redirect_stdout(io.StringIO()):
        reference = fitburst_fitter.LSFitter(
            data, reference_model, good_freq=np.ones(freqs.size, bool), weighted_fit=False
        )
        reference.fix_parameter([name for name in reference_model.parameters if name not in free])
        reference.fit(exact_jacobian=True)
    assert reference.results.success
    reference_values = np.asarray(reference.results.x)
    reference_sigma = np.array(
        [
            value
            for name in reference.fit_parameters
            for value in reference.fit_statistics["bestfit_uncertainties"][name]
        ]
    )

    fit = fit_burst_model(BurstModel(freqs, times), data, BurstParameters.from_dict(start), free)

    assert fit.success and fit.uncertainties is not None
    assert fit.covariance_basis == "observed_hessian"
    # Within a hundredth of a sigma of fitburst's minimum.
    np.testing.assert_allclose((fit.values - reference_values) / fit.uncertainties, 0.0, rtol=0, atol=1e-2)
    np.testing.assert_allclose(fit.uncertainties, reference_sigma, rtol=1e-2)
    assert fit.fit_statistics["chisq_final"] == pytest.approx(reference.fit_statistics["chisq_final"], rel=1e-6)
    for key in ("num_freq", "num_freq_good", "num_fit_parameters", "num_observations", "num_time"):
        assert fit.fit_statistics[key] == reference.fit_statistics[key], key
    assert fit.fit_statistics["snr"] == pytest.approx(reference.fit_statistics["snr"], rel=1e-6)


def test_scintillation_fit_reaches_at_least_fitburst_minimum() -> None:
    freqs = np.linspace(1200.0, 1600.0, 64)
    times = np.arange(160, dtype=float) * 2.5e-4
    truth = {
        "amplitude": [0.7],
        "arrival_time": [0.02],
        "burst_width": [0.0008],
        "ref_freq": [1400.0],
        "scattering_timescale": [0.0012],
        "spectral_index": [0.0],
        "spectral_running": [0.0],
        "dm": [0.0],
        "dm_index": [-2.0],
        "scattering_index": [-4.0],
    }
    gains = 1.0 + 0.8 * np.sin(np.arange(freqs.size) / 3.0)
    clean = BurstModel(freqs, times).evaluate(BurstParameters.from_dict(truth)) * gains[:, None]
    data = clean + np.random.default_rng(4).normal(0.0, 1.0, clean.shape)
    free = ("arrival_time", "burst_width", "scattering_timescale")

    reference_model = fitburst_model.SpectrumModeler(freqs, times, scintillation=True, is_dedispersed=True)
    reference_model.update_parameters(truth)
    with contextlib.redirect_stdout(io.StringIO()):
        reference = fitburst_fitter.LSFitter(
            data, reference_model, good_freq=np.ones(freqs.size, bool), weighted_fit=False
        )
        reference.fix_parameter(["dm", "dm_index", "scattering_index"])
        reference.fit(exact_jacobian=True)

    fit = fit_burst_model(BurstModel(freqs, times, scintillation=True), data, BurstParameters.from_dict(truth), free)

    assert fit.success
    # Both minimise the same chi-squared; FLITS's exact Jacobian gets at least as low.
    assert fit.fit_statistics["chisq_final"] <= reference.fit_statistics["chisq_final"] * (1 + 1e-9)
    assert fit.function_evaluations is not None and fit.function_evaluations < reference.results.nfev
