"""How FLITS describes its uncertainties.

Every uncertainty says what it is and what it includes, and when a known term
is missing, how to add it. None of them pass judgement on whether a number may
be published: that wording confused users, and the classification and flags
already carry the information.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest

from flits.models import UncertaintyDetail
from flits.session import BurstSession

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = re.compile(r"publishable|not a formal|diagnostic[ -]only|should not be (read|compared)", re.IGNORECASE)


@pytest.fixture
def measured_session(synthetic_waterfall) -> BurstSession:
    session = BurstSession.from_file(
        str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0, distance_mpc=100.0, redshift=0.02
    )
    session.set_event_ms(100.0, 160.0)
    session.add_offpulse_ms(0.0, 50.0)
    return session


def _all_details(session: BurstSession) -> list[tuple[str, UncertaintyDetail]]:
    from flits.models import DriftAnalysisSettings

    session.compute_widths()
    results = session.compute_properties()
    details = [(f"results.{key}", detail) for key, detail in results.uncertainty_details.items()]
    for width in session.width_analysis.results if session.width_analysis else []:
        details.extend((f"width.{width.method}.{key}", d) for key, d in width.uncertainty_details.items())
    for metric in ("integrated_event_snr", "dm_phase", "smoothed_structure"):
        sweep = session.optimize_dm(0.0, 60.0, 6.0, metric=metric)
        details.extend((f"dm.{metric}.{key}", d) for key, d in sweep.uncertainty_details.items())
    drift = session.run_drift_analysis(settings=DriftAnalysisSettings(monte_carlo_trials=8, random_seed=3))
    details.extend((f"drift.{key}", d) for key, d in drift.uncertainty_details.items())
    temporal = session.run_temporal_structure_analysis(16.0)
    details.extend((f"temporal.{key}", d) for key, d in temporal.uncertainty_details.items())
    return details


def _fit_details() -> list[tuple[str, UncertaintyDetail]]:
    """Texts from the PSD and burst-model fits, which a short synthetic burst may not reach."""
    from flits.analysis.fitting.model_fit import _model_fit_uncertainty_detail
    from flits.analysis.temporal.core import _psd_uncertainty_details

    psd = _psd_uncertainty_details(
        {"fit_status": "ok", "power_law_a_err": 1.0, "power_law_alpha_err": 0.2, "power_law_c_err": 0.1},
        {
            "crossover_frequency_hz": 100.0,
            "crossover_frequency_status": "ok",
            "crossover_frequency_hz_3sigma_low": 80.0,
            "crossover_frequency_hz_3sigma_high": 130.0,
        },
    )
    model_fit = [
        ("model_fit.width", _model_fit_uncertainty_detail(value=0.1, units="ms", parameter_label="Width")),
        (
            "model_fit.tau_gauss_newton_at_bound",
            _model_fit_uncertainty_detail(
                value=0.1,
                units="ms",
                parameter_label="Scattering timescale",
                covariance_basis="gauss_newton",
                at_lower_bound=True,
            ),
        ),
        (
            "model_fit.width_below_time_resolution",
            _model_fit_uncertainty_detail(
                value=0.01, units="ms", parameter_label="Intrinsic width", below_time_resolution=True
            ),
        ),
    ]
    return [*((f"psd.{key}", d) for key, d in psd.items()), *model_fit]


def test_no_uncertainty_passes_judgement(measured_session: BurstSession) -> None:
    details = _all_details(measured_session) + _fit_details()
    sources = {name.split(".")[0] for name, _ in details}

    assert {"results", "width", "dm", "drift", "psd", "model_fit"} <= sources
    assert any(name == "results.iso_e" for name, _ in details)
    for name, detail in details:
        for text in (detail.basis, detail.tooltip):
            assert not VERDICTS.search(text), f"{name}: {text!r}"
        assert detail.tooltip, f"{name} has no explanation"


def test_a_statistical_flux_error_is_reported_and_says_how_to_complete_it(measured_session: BurstSession) -> None:
    results = measured_session.compute_properties()
    fluence = results.uncertainty_details["fluence_jyms"]

    assert fluence.classification == "statistical_only"
    assert "1σ statistical" in fluence.tooltip
    assert "Supply the SEFD uncertainty" in fluence.tooltip
    # It is a 1-sigma statistical error, so the plain field carries it.
    assert results.uncertainties.fluence_jyms == pytest.approx(fluence.value)
    assert results.uncertainties.peak_flux_jy == pytest.approx(results.uncertainty_details["peak_flux_jy"].value)


def test_a_complete_flux_error_says_what_it_includes(synthetic_waterfall) -> None:
    session = BurstSession.from_file(
        str(synthetic_waterfall.path), dm=0.0, sefd_jy=10.0, sefd_fractional_uncertainty=0.2
    )
    session.set_event_ms(100.0, 160.0)
    session.add_offpulse_ms(0.0, 50.0)
    fluence = session.compute_properties().uncertainty_details["fluence_jyms"]

    assert fluence.classification == "formal_1sigma"
    assert "SEFD calibration uncertainty" in fluence.tooltip
    assert "Supply" not in fluence.tooltip


def test_resolution_limits_stay_out_of_the_plain_fields(measured_session: BurstSession) -> None:
    results = measured_session.compute_properties()

    # Half a time bin bounds the arrival time; it is not a 1-sigma error.
    assert results.uncertainty_details["toa_topo_mjd"].classification == "resolution_limit"
    assert results.uncertainties.toa_topo_mjd is None


def test_the_csv_export_carries_every_uncertainty_value(measured_session: BurstSession) -> None:
    results = measured_session.compute_properties()
    manifest = measured_session.export_results(session_id="language", include=["csv"])
    artifact = next(item for item in manifest.artifacts if item.name.endswith(".csv"))
    _, content = measured_session.get_export_artifact(manifest.export_id, artifact.name)
    row = next(csv.DictReader(io.StringIO(content.decode("utf-8"))))

    assert float(row["fluence_jyms_uncertainty_value"]) == pytest.approx(
        results.uncertainty_details["fluence_jyms"].value
    )
    assert row["fluence_jyms_uncertainty_class"] == "statistical_only"
    assert float(row["toa_topo_mjd_uncertainty_value"]) > 0


def test_the_interface_does_not_judge_uncertainties() -> None:
    for name in ("app.js", "index.html"):
        source = (ROOT / "flits" / "web_static" / name).read_text(encoding="utf-8")
        assert not VERDICTS.search(source), name
