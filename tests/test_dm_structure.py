"""Tests for the smoothed-structure DM metric (Sutinjo et al. 2023).

The maths is checked against a direct time-domain computation, the interval
against simulated bursts of known DM, and the metric end to end through the
session, snapshots, the web API, and the CSV export.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from scipy.fft import dct, idct

from flits.analysis.dm_optimization.structure import (
    MIN_EVENT_BINS,
    automatic_cutoff,
    consistent_interval,
    dct_envelope,
    structure_curve,
)
from flits.models import DmOptimizationResult
from flits.session import BurstSession
from flits.signal import dedisperse, dedispersion_bin_resolution

K_DM = 1.0 / 2.41e-4


def _smooth(curve: np.ndarray, cutoff: int, order: int = 3) -> np.ndarray:
    k = np.arange(1, curve.size + 1, dtype=float)
    lowpass = 1.0 / (1.0 + (k / cutoff) ** (2 * order))
    return idct(dct(curve, norm="ortho") * lowpass, norm="ortho")


def test_scores_are_the_derivative_norm_of_the_smoothed_curve() -> None:
    rng = np.random.default_rng(1)
    curves = rng.normal(size=(7, 64)) + np.exp(-0.5 * ((np.arange(64) - 30) / 3.0) ** 2) * 8
    curve = structure_curve(curves, cutoff=12)

    # The DCT-II diagonalizes the first-difference operator, so the DCT-domain
    # score must equal the plain finite-difference norm of the smoothed curve.
    expected = [np.linalg.norm(np.diff(_smooth(row, 12))) for row in curves]
    np.testing.assert_allclose(curve.scores, expected, rtol=1e-10)


def test_noise_terms_follow_eq_28_and_eq_15() -> None:
    rng = np.random.default_rng(2)
    curves = rng.normal(size=(9, 48)) + np.exp(-0.5 * ((np.arange(48) - 20) / 2.0) ** 2) * 6
    curve = structure_curve(curves, cutoff=10)

    detrended = curves - np.vstack([_smooth(row, 10) for row in curves])
    relative = detrended - detrended[curve.best_index]
    expected = [np.linalg.norm(np.diff(_smooth(row, 10))) for row in relative]
    np.testing.assert_allclose(curve.noise_terms, expected, rtol=1e-10, atol=1e-12)
    np.testing.assert_array_equal(curve.consistent, curve.scores + curve.noise_terms >= curve.scores.max())
    assert curve.noise_terms[curve.best_index] == pytest.approx(0.0, abs=1e-12)
    assert curve.consistent[curve.best_index]


def test_automatic_cutoff_finds_where_signal_meets_noise() -> None:
    envelope = np.concatenate([np.full(20, 50.0), np.full(80, 1.0)])
    assert automatic_cutoff(envelope) == 25

    # Pure noise is at its floor from the start: the smallest cutoff the
    # running window allows.
    assert automatic_cutoff(np.ones(64)) == 5


def test_a_sweep_without_an_automatic_cutoff_says_how_to_proceed(monkeypatch, structure_session) -> None:
    monkeypatch.setattr("flits.analysis.dm_optimization.core.automatic_cutoff", lambda envelope: None)
    with pytest.raises(ValueError, match="Set k_c by hand"):
        _sweep(structure_session)


def test_cutoff_and_order_are_validated() -> None:
    curves = np.zeros((3, 20))
    with pytest.raises(ValueError, match="between 1 and 19"):
        structure_curve(curves, cutoff=20)
    with pytest.raises(ValueError, match="order"):
        structure_curve(curves, cutoff=5, order=0)


def test_interval_warns_about_edges_and_gaps() -> None:
    dms = np.arange(7, dtype=float)
    assert consistent_interval(dms, np.array([0, 0, 1, 1, 1, 0, 0], dtype=bool)) == (2.0, 4.0, [])
    _, _, flags = consistent_interval(dms, np.array([1, 1, 0, 0, 0, 0, 0], dtype=bool))
    assert flags == ["structure_interval_on_sweep_edge"]
    _, _, flags = consistent_interval(dms, np.array([0, 1, 0, 1, 0, 0, 0], dtype=bool))
    assert flags == ["structure_interval_has_gaps"]


def test_interval_covers_the_true_dm_of_simulated_bursts() -> None:
    """Render bursts with sub-sample dispersion and check the interval contains the truth.

    Over about a thousand simulated bursts at several S/N levels and burst
    shapes, the interval contained the true DM in 98-100% of cases and was 4-7
    times the rms error of the best DM. This keeps a smaller version of that
    check.
    """
    rng = np.random.default_rng(2023)
    nchan, ntime, tsamp = 32, 1024, 1e-4
    freqs = np.linspace(1500.0, 1100.0, nchan)
    step = dedispersion_bin_resolution(freqs, tsamp)
    trial_dms = np.arange(-25, 26) * step
    t = np.arange(ntime) * tsamp
    window = slice(int(0.047 / tsamp), int(0.058 / tsamp))

    hits, errors, half_widths = 0, [], []
    for _ in range(30):
        true_dm = rng.uniform(-3 * step, 3 * step)
        delays = K_DM * true_dm * (freqs**-2 - freqs.max() ** -2)
        data = rng.normal(size=(nchan, ntime))
        for row, delay in zip(data, delays, strict=True):
            row += 1.5 * np.exp(-0.5 * ((t - 0.050 - delay) / 0.4e-3) ** 2)
            row += 1.0 * np.exp(-0.5 * ((t - 0.052 - delay) / 0.6e-3) ** 2)
        curves = np.vstack(
            [dedisperse(data, float(dm), freqs, tsamp, fill_value=0.0)[:, window].sum(axis=0) for dm in trial_dms]
        )
        cutoff = automatic_cutoff(dct_envelope(curves))
        assert cutoff is not None
        curve = structure_curve(curves, cutoff=cutoff)
        low, high, flags = consistent_interval(trial_dms, curve.consistent)
        assert "structure_interval_on_sweep_edge" not in flags
        hits += low <= true_dm <= high
        errors.append(trial_dms[curve.best_index] - true_dm)
        half_widths.append(0.5 * (high - low))

    assert hits / 30 >= 0.9
    rms_error = float(np.sqrt(np.mean(np.square(errors))))
    # Conservative: wider than the scatter of the best DM, not narrower.
    assert np.median(half_widths) > 2 * rms_error


@pytest.fixture
def structure_session(synthetic_waterfall) -> BurstSession:
    """The synthetic burst, aligned at DM 0, with an event window around it."""
    session = BurstSession.from_file(str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0)
    session.set_event_ms(100.0, 160.0)
    session.add_offpulse_ms(0.0, 50.0)
    return session


def _sweep(session: BurstSession, **kwargs) -> DmOptimizationResult:
    # The synthetic band is narrow, so one DM bin is about 6 pc cm^-3.
    return session.optimize_dm(0.0, 60.0, 6.0, metric="smoothed_structure", **kwargs)


def test_session_reports_a_range_around_the_true_dm(structure_session: BurstSession) -> None:
    result = _sweep(structure_session)

    assert result.snr_metric == "smoothed_structure"
    assert result.structure is not None
    low, high = result.structure.interval_low_dm, result.structure.interval_high_dm
    assert low <= 0.0 <= high
    assert low <= result.best_dm <= high
    assert result.structure.cutoff_source == "automatic"
    assert result.settings is not None
    assert result.settings.structure_cutoff is None
    assert result.settings.structure_filter_order == 3

    detail = result.uncertainty_details["best_dm"]
    assert detail.classification == "conservative_bound"
    assert not detail.is_formal_1sigma
    half_width = max(result.best_dm - low, high - result.best_dm)
    if half_width > 0:
        assert detail.value == pytest.approx(half_width)
    else:
        # Only the best trial passes: report one trial spacing and say why.
        assert detail.value == pytest.approx(6.0)
        assert "structure_interval_within_one_step" in detail.warning_flags
    # Not a formal 1-sigma error, so it must not appear as the scalar uncertainty.
    assert result.best_dm_uncertainty is None


def test_a_manual_cutoff_is_used_and_recorded(structure_session: BurstSession) -> None:
    result = _sweep(structure_session, structure_cutoff=8)

    assert result.structure is not None
    assert result.structure.cutoff == 8
    assert result.structure.cutoff_source == "manual"
    assert result.settings is not None and result.settings.structure_cutoff == 8


def test_a_short_event_window_is_refused_clearly(structure_session: BurstSession) -> None:
    structure_session.set_event_ms(125.0, 135.0)
    with pytest.raises(ValueError, match=f"at least {MIN_EVENT_BINS} time samples"):
        _sweep(structure_session)


def test_components_carry_their_own_range(structure_session: BurstSession) -> None:
    structure_session.add_region_ms(100.0, 128.0)
    structure_session.add_region_ms(128.0, 160.0)
    result = _sweep(structure_session)

    assert len(result.component_results) == 2
    for component in result.component_results:
        assert component.structure is not None
        assert component.structure.interval_low_dm <= component.best_dm <= component.structure.interval_high_dm


def test_the_range_survives_a_snapshot_round_trip(structure_session: BurstSession) -> None:
    result = _sweep(structure_session)
    payload = json.loads(json.dumps(structure_session.snapshot_dict()))
    restored = DmOptimizationResult.from_dict(payload["dm_optimization"])

    assert restored.structure is not None
    assert restored.structure.interval_low_dm == result.structure.interval_low_dm
    assert restored.structure.interval_high_dm == result.structure.interval_high_dm
    assert restored.structure.cutoff == result.structure.cutoff
    assert restored.settings == result.settings
    np.testing.assert_array_equal(restored.structure.consistent, result.structure.consistent)


def test_other_metrics_serialize_as_before(structure_session: BurstSession) -> None:
    payload = structure_session.optimize_dm(0.0, 60.0, 6.0, metric="dm_phase").to_dict()

    assert "structure" not in payload
    assert set(payload["settings"]) == {"center_dm", "half_range", "step", "metric"}


def test_the_web_api_passes_the_cutoff(structure_session: BurstSession) -> None:
    from flits.web.app import SESSIONS, ActionRequest, session_action

    SESSIONS["structure"] = structure_session
    try:
        response = session_action(
            "structure",
            ActionRequest(
                type="optimize_dm",
                payload={
                    "center_dm": 0.0,
                    "half_range": 60.0,
                    "step": 6.0,
                    "metric": "smoothed_structure",
                    "structure_cutoff": 9,
                },
            ),
        )
    finally:
        SESSIONS.pop("structure", None)

    optimization = response["view"]["dm_optimization"]
    assert optimization["structure"]["cutoff"] == 9
    assert optimization["structure"]["cutoff_source"] == "manual"
    assert "smoothed_structure" in [item["key"] for item in response["view"]["meta"]["dm_metrics"]]


def test_the_csv_export_lists_the_range(structure_session: BurstSession) -> None:
    import csv
    import io

    result = _sweep(structure_session)
    structure_session.compute_properties()
    manifest = structure_session.export_results(session_id="structure", include=["csv"])
    artifact = next(item for item in manifest.artifacts if item.name.endswith(".csv"))
    _, content = structure_session.get_export_artifact(manifest.export_id, artifact.name)
    row = next(csv.DictReader(io.StringIO(content.decode("utf-8"))))

    assert float(row["dm_range_low"]) == result.structure.interval_low_dm
    assert float(row["dm_range_high"]) == result.structure.interval_high_dm
    assert row["dm_uncertainty_class"] == "conservative_bound"


@pytest.mark.realdata
def test_tutorial_burst_range_agrees_with_dmphase(monkeypatch) -> None:
    """On the guided-workflow burst, the range brackets the DMphase best DM."""
    from flits.demo import DEMO_FILE_NAME, default_demo_dir

    source = default_demo_dir() / DEMO_FILE_NAME
    if not source.exists():
        pytest.skip(f"Tutorial burst not found in {source.parent}; set FLITS_DEMO_DIR")

    session = BurstSession.from_file(str(source), dm=0.0, telescope="gbt", sefd_jy=10.0)
    session.set_time_factor(32)
    session.set_event_ms(235.0, 250.0)
    session.clear_offpulse()
    session.add_offpulse_ms(221.0, 233.0)
    session.add_offpulse_ms(260.0, 280.0)
    session.set_spectral_extent_freq(1300.0, 1750.0)

    dmphase = session.optimize_dm(0.0, 10.0, 0.5, metric="dm_phase")
    structure = session.optimize_dm(0.0, 10.0, 0.5, metric="smoothed_structure")

    assert structure.structure is not None
    assert structure.structure.warning_flags == ["integer_bin_dedispersion"]
    assert structure.structure.interval_low_dm <= dmphase.best_dm <= structure.structure.interval_high_dm
    # A residual DM of about 5 with a range of about +-1 pc cm^-3.
    assert structure.best_dm == pytest.approx(5.0, abs=0.5)
    assert 0.5 <= structure.uncertainty_details["best_dm"].value <= 1.5
