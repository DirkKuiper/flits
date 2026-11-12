"""Tests for the peak-S/N DM metric and the Gaussian fitted to its curve.

The fit is checked against exact curves, the score and the fitted centre against
simulated bursts of known DM rendered with sub-sample dispersion, and the metric
end to end through the session, snapshots, the web API, and the CSV export.
"""

from __future__ import annotations

import csv
import io
import itertools
import json

import numpy as np
import pytest

from flits.analysis.dm_optimization.core import _peak_snr_uncertainty_detail
from flits.analysis.dm_optimization.peak_snr import (
    BOXCAR,
    PEAK_BIN,
    boxcar_widths,
    fit_gaussian_curve,
    profile_peak_snr,
)
from flits.models import DmOptimizationResult
from flits.session import BurstSession
from flits.signal import dedisperse, dedispersion_bin_resolution

K_DM = 1.0 / 2.41e-4


def _gaussian(x: np.ndarray, amplitude: float, center: float, sigma: float, baseline: float) -> np.ndarray:
    return amplitude * np.exp(-0.5 * ((x - center) / sigma) ** 2) + baseline


def test_the_fit_recovers_an_exact_gaussian() -> None:
    dms = np.linspace(-1.0, 1.0, 41)
    fit = fit_gaussian_curve(dms, _gaussian(dms, 30.0, 0.12, 0.2, 4.0))

    assert fit.status == "gaussian_fit"
    assert fit.warning_flags == []
    assert fit.amplitude == pytest.approx(30.0, rel=1e-6)
    assert fit.center == pytest.approx(0.12, abs=1e-8)
    assert fit.sigma == pytest.approx(0.2, rel=1e-6)
    assert fit.baseline == pytest.approx(4.0, abs=1e-6)
    assert fit.peak_value == pytest.approx(34.0, rel=1e-6)
    assert fit.residual_rms == pytest.approx(0.0, abs=1e-6)

    # The fitted curve is exactly 1 below its peak at the drop-one distance.
    drop = fit.drop_one_half_width
    assert drop is not None
    assert _gaussian(np.array([0.12 + drop]), 30.0, 0.12, 0.2, 4.0)[0] == pytest.approx(33.0, rel=1e-9)


def test_a_peak_no_higher_than_one_has_no_drop_one_width() -> None:
    dms = np.linspace(-1.0, 1.0, 41)
    fit = fit_gaussian_curve(dms, _gaussian(dms, 0.8, 0.0, 0.2, 0.0))

    assert fit.succeeded
    assert fit.drop_one_half_width is None


@pytest.mark.parametrize(
    ("dms", "curve", "flag"),
    [
        # The peak sits at the start of the sweep: the far side was not sampled.
        (
            np.linspace(0.0, 1.0, 21),
            _gaussian(np.linspace(0.0, 1.0, 21), 30.0, 0.0, 0.2, 0.0),
            "gaussian_center_on_sweep_edge",
        ),
        # The sweep covers only the top of a broad peak.
        (
            np.linspace(-0.2, 0.2, 21),
            _gaussian(np.linspace(-0.2, 0.2, 21), 30.0, 0.0, 2.0, 0.0),
            "gaussian_width_exceeds_sweep",
        ),
        # The peak is narrower than the step.
        (
            np.linspace(-1.0, 1.0, 21),
            _gaussian(np.linspace(-1.0, 1.0, 21), 30.0, 0.0, 0.06, 0.0),
            "gaussian_width_below_step",
        ),
    ],
)
def test_the_fit_flags_what_the_sweep_could_not_constrain(dms: np.ndarray, curve: np.ndarray, flag: str) -> None:
    fit = fit_gaussian_curve(dms, curve)

    assert fit.status == "gaussian_fit"
    assert flag in fit.warning_flags


def test_curves_that_cannot_be_fitted_say_why() -> None:
    dms = np.linspace(-1.0, 1.0, 21)
    flat = fit_gaussian_curve(dms, np.full(dms.size, 7.0))
    assert flat.status == "gaussian_fit_flat_curve"
    assert not flat.succeeded and flat.peak_value is None and flat.drop_one_half_width is None

    sparse = fit_gaussian_curve(dms[:4], np.array([1.0, 3.0, 2.0, np.nan]))
    assert sparse.status == "gaussian_fit_insufficient_trials"


def test_an_unfitted_curve_is_described_without_a_width() -> None:
    fit = fit_gaussian_curve(np.linspace(-1.0, 1.0, 21), np.zeros(21))
    detail = _peak_snr_uncertainty_detail(fit, peak_snr_filter=PEAK_BIN, trial_count=21)

    assert detail.value is None
    assert detail.classification == "heuristic_local_fit"
    assert "best sampled trial" in detail.basis
    assert {"gaussian_fit_flat_curve", "uncertainty_unavailable"} <= set(detail.warning_flags)


def test_boxcar_widths_grow_to_the_event_window() -> None:
    widths = boxcar_widths(10)
    assert widths[0] == 1 and widths[-1] == 10
    assert all(later > earlier for earlier, later in itertools.pairwise(widths))
    assert boxcar_widths(1) == [1]


def test_a_boxcar_sums_a_wide_burst_in_white_noise_units() -> None:
    profile = np.zeros(1000)
    profile[500:540] = 0.8
    offpulse = np.r_[0:400, 600:1000]

    single, single_width = profile_peak_snr(profile, 450, 600, offpulse_bins=offpulse, peak_snr_filter=PEAK_BIN)
    boxed, boxed_width = profile_peak_snr(profile, 450, 600, offpulse_bins=offpulse, peak_snr_filter=BOXCAR)

    assert (single, single_width) == (pytest.approx(0.8), 1)
    # The ladder runs 32, 48: the 48-sample boxcar holds the whole burst.
    assert boxed_width == 48
    assert boxed == pytest.approx(0.8 * 40 / np.sqrt(48.0))


def test_a_boxcar_finds_a_wide_faint_burst_that_single_samples_miss() -> None:
    hits = 0
    for seed in range(20):
        profile = np.random.default_rng(seed).normal(size=1000)
        profile[500:540] += 1.0
        offpulse = np.r_[0:400, 600:1000]
        single, _ = profile_peak_snr(profile, 450, 600, offpulse_bins=offpulse, peak_snr_filter=PEAK_BIN)
        boxed, width = profile_peak_snr(profile, 450, 600, offpulse_bins=offpulse, peak_snr_filter=BOXCAR)
        hits += boxed > single + 1.0 and width > 10
    # A matched boxcar collects about sqrt(40) = 6.3 sigma; single samples about 1 sigma over the noise peak.
    assert hits >= 18


def test_the_peak_is_searched_only_inside_the_event_window() -> None:
    profile = np.zeros(200)
    profile[20] = 50.0
    profile[120] = 6.0

    assert profile_peak_snr(profile, 100, 150, offpulse_bins=np.arange(0, 80))[0] == pytest.approx(6.0)


def test_an_unknown_filter_is_refused() -> None:
    with pytest.raises(ValueError, match="Valid filters: peak_bin, boxcar"):
        profile_peak_snr(np.zeros(10), 0, 10, offpulse_bins=np.arange(5), peak_snr_filter="matched")


@pytest.mark.parametrize("peak_snr_filter", [PEAK_BIN, BOXCAR])
def test_the_fit_finds_the_true_dm_of_simulated_bursts(peak_snr_filter: str) -> None:
    """Render bursts with sub-sample dispersion and fit their peak-S/N curves.

    With about a hundred bursts per S/N level the fitted sigma contained the
    true DM in every detected case and was 1-10 times the rms error of the
    best DM (docs/analysis/dm-optimization.md). This keeps a smaller version.
    """
    rng = np.random.default_rng(160)
    nchan, ntime, tsamp = 64, 1024, 1e-4
    freqs = np.linspace(1500.0, 1100.0, nchan)
    step = dedispersion_bin_resolution(freqs, tsamp)
    trial_dms = np.arange(-30, 31) * step
    t = np.arange(ntime) * tsamp
    offpulse = np.r_[0:350, 750:1024]

    errors, sigmas = [], []
    for _ in range(15):
        true_dm = rng.uniform(-3 * step, 3 * step)
        delays = K_DM * true_dm * (freqs**-2 - freqs.max() ** -2)
        data = rng.normal(size=(nchan, ntime))
        for row, delay in zip(data, delays, strict=True):
            row += 0.8 * np.exp(-0.5 * ((t - 0.050 - delay) / 0.3e-3) ** 2)
        scores = []
        for dm in trial_dms:
            profile = dedisperse(data, float(dm), freqs, tsamp, fill_value=0.0).sum(axis=0) / np.sqrt(nchan)
            profile = (profile - np.median(profile[offpulse])) / np.std(profile[offpulse])
            scores.append(
                profile_peak_snr(profile, 400, 700, offpulse_bins=offpulse, peak_snr_filter=peak_snr_filter)[0]
            )
        fit = fit_gaussian_curve(trial_dms, np.asarray(scores))
        assert fit.status == "gaussian_fit"
        assert fit.center is not None and fit.sigma is not None
        errors.append(fit.center - true_dm)
        sigmas.append(fit.sigma)

    errors_array, sigmas_array = np.asarray(errors), np.asarray(sigmas)
    rms_error = float(np.sqrt(np.mean(errors_array**2)))
    assert rms_error < 4 * step
    assert np.mean(np.abs(errors_array) <= sigmas_array) >= 0.9
    # Sigma is the curve's width, not the scatter of the best DM: it is wider.
    assert np.median(sigmas_array) > rms_error


@pytest.fixture
def peak_session(synthetic_waterfall) -> BurstSession:
    """The synthetic burst, aligned at DM 0, with an event window around it."""
    session = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0)
    session.set_event_ms(100.0, 160.0)
    session.add_offpulse_ms(0.0, 50.0)
    return session


def _sweep(session: BurstSession, **kwargs) -> DmOptimizationResult:
    # The synthetic band is narrow, so one DM bin is about 6 pc cm^-3.
    return session.optimize_dm(0.0, 60.0, 6.0, metric="peak_snr", **kwargs)


@pytest.mark.parametrize("peak_snr_filter", [PEAK_BIN, BOXCAR])
def test_session_reports_the_gaussian_fit(peak_session: BurstSession, peak_snr_filter: str) -> None:
    result = _sweep(peak_session, peak_snr_filter=peak_snr_filter)

    assert result.snr_metric == "peak_snr"
    assert result.settings is not None and result.settings.peak_snr_filter == peak_snr_filter
    fit = result.gaussian_fit
    assert fit is not None
    assert fit.peak_snr_filter == peak_snr_filter
    assert fit.fit_status == "gaussian_fit" == result.fit_status
    assert fit.center_dm is not None and fit.sigma_dm is not None
    assert result.best_dm == pytest.approx(fit.center_dm)
    # The burst is at DM 0; sigma, about 30 here, is the curve's width.
    assert abs(result.best_dm) < fit.sigma_dm
    assert result.best_sn == pytest.approx(fit.amplitude + fit.baseline)
    assert fit.matched_width_ms.shape == result.trial_dms.shape
    if peak_snr_filter == PEAK_BIN:
        np.testing.assert_allclose(fit.matched_width_ms, 1.0)
    else:
        assert np.all(fit.matched_width_ms >= 1.0)

    detail = result.uncertainty_details["best_dm"]
    assert detail.classification == "heuristic_local_fit"
    assert detail.value == pytest.approx(fit.sigma_dm)
    assert "integer_bin_dedispersion" in detail.warning_flags
    # Not a 1-sigma error, so it stays out of the plain field.
    assert result.best_dm_uncertainty is None


def test_peak_bin_is_the_default_filter(peak_session: BurstSession) -> None:
    result = _sweep(peak_session)
    assert result.settings is not None and result.settings.peak_snr_filter == PEAK_BIN


def test_an_unknown_filter_is_refused_by_the_session(peak_session: BurstSession) -> None:
    with pytest.raises(ValueError, match="Unsupported peak S/N filter"):
        _sweep(peak_session, peak_snr_filter="matched")


def test_components_carry_their_own_fit(peak_session: BurstSession) -> None:
    peak_session.add_region_ms(100.0, 128.0)
    peak_session.add_region_ms(128.0, 160.0)
    result = _sweep(peak_session, peak_snr_filter=BOXCAR)

    assert len(result.component_results) == 2
    for component in result.component_results:
        assert component.gaussian_fit is not None
        assert component.gaussian_fit.peak_snr_filter == BOXCAR


def test_the_fit_survives_a_snapshot_round_trip(peak_session: BurstSession) -> None:
    result = _sweep(peak_session, peak_snr_filter=BOXCAR)
    payload = json.loads(json.dumps(peak_session.snapshot_dict()))
    restored = DmOptimizationResult.from_dict(payload["dm_optimization"])

    assert restored.gaussian_fit is not None and result.gaussian_fit is not None
    assert restored.gaussian_fit.sigma_dm == pytest.approx(result.gaussian_fit.sigma_dm)
    assert restored.gaussian_fit.center_dm == pytest.approx(result.gaussian_fit.center_dm)
    assert restored.gaussian_fit.warning_flags == result.gaussian_fit.warning_flags
    np.testing.assert_allclose(restored.gaussian_fit.matched_width_ms, result.gaussian_fit.matched_width_ms)
    assert restored.settings == result.settings


def test_other_metrics_serialize_as_before(peak_session: BurstSession) -> None:
    payload = peak_session.optimize_dm(0.0, 60.0, 6.0, metric="integrated_event_snr").to_dict()

    assert "gaussian_fit" not in payload
    assert set(payload["settings"]) == {"center_dm", "half_range", "step", "metric"}


def test_a_sweep_saved_by_the_old_peak_snr_metric_still_loads() -> None:
    """FLITS before March 2026 had a peak_snr metric with no Gaussian fit."""
    restored = DmOptimizationResult.from_dict(
        {
            "center_dm": 0.0,
            "step": 1.0,
            "best_dm": 0.5,
            "snr_metric": "peak_snr",
            "settings": {"center_dm": 0.0, "half_range": 5.0, "step": 1.0, "metric": "peak_snr"},
        }
    )

    assert restored.gaussian_fit is None
    assert restored.settings is not None and restored.settings.peak_snr_filter is None


def test_the_web_api_passes_the_filter(peak_session: BurstSession) -> None:
    from flits.web.app import SESSIONS, ActionRequest, session_action

    SESSIONS["peak-snr"] = peak_session
    try:
        response = session_action(
            "peak-snr",
            ActionRequest(
                type="optimize_dm",
                payload={
                    "center_dm": 0.0,
                    "half_range": 60.0,
                    "step": 6.0,
                    "metric": "peak_snr",
                    "peak_snr_filter": "boxcar",
                },
            ),
        )
    finally:
        SESSIONS.pop("peak-snr", None)

    optimization = response["view"]["dm_optimization"]
    assert optimization["settings"]["peak_snr_filter"] == "boxcar"
    assert optimization["gaussian_fit"]["peak_snr_filter"] == "boxcar"
    assert "peak_snr" in [item["key"] for item in response["view"]["meta"]["dm_metrics"]]


def test_the_csv_export_lists_the_fit(peak_session: BurstSession) -> None:
    result = _sweep(peak_session, peak_snr_filter=BOXCAR)
    peak_session.compute_properties()
    manifest = peak_session.export_results(session_id="peak-snr", include=["csv"])
    artifact = next(item for item in manifest.artifacts if item.name.endswith(".csv"))
    _, content = peak_session.get_export_artifact(manifest.export_id, artifact.name)
    row = next(csv.DictReader(io.StringIO(content.decode("utf-8"))))

    assert result.gaussian_fit is not None
    assert row["dm_snr_metric"] == "peak_snr"
    assert row["dm_peak_snr_filter"] == "boxcar"
    assert float(row["dm_snr_drop_one_half_width"]) == pytest.approx(result.gaussian_fit.snr_drop_one_half_width)
    assert row["dm_uncertainty_class"] == "heuristic_local_fit"
