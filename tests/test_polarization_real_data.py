"""In-session polarimetry against real full-Stokes observatory data.

These run on NRT filterbanks of R147 (FRB 20240114A) that carry four
polarization products. They are opt-in (``pytest -m realdata``) because each
file is a quarter of a gigabyte and lives outside the repository.

The expected values are not invented for the test. The polarization basis is
the one the NRT preset declares, and the rotation measure is what FLITS
measures from a burst that is close to fully linearly polarized -- around
-373 rad/m^2, consistent with the published rotation measure of this source.
Changing the reader, the Stokes conversion, the normalization or the extraction
in a way that moves either of those numbers is a regression.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from flits.session import BurstSession

pytestmark = pytest.mark.realdata

REPO_ROOT = Path(__file__).resolve().parents[1]
NRT_DIR = REPO_ROOT / "data" / "R147_NRT"
BURST_TABLE = REPO_ROOT / "burst_properties_Lband.csv"

# A bright, well-characterised R147 burst. `_500ms` files place the burst 500 ms
# into the record; the rest place it at 100 ms.
BURST_NAME = "burst_60386.39684885931s3p1t86.0393"
BURST_DM = 528.0
BURST_TIME_MS = 100.9
EXPECTED_RM_RAD_M2 = -373.4


def _burst_row(name: str) -> dict[str, str]:
    with BURST_TABLE.open() as handle:
        for row in csv.DictReader(handle):
            if row["burst_name"] == name:
                return row
    raise KeyError(name)


def _masked_channels(row: dict[str, str]) -> list[int]:
    """Parse the RFI channel list the burst catalogue records, a numpy repr."""
    values = np.fromstring(row["mask"].strip().strip("[]"), sep=" ")
    return sorted({int(value) for value in values})


@pytest.fixture(scope="module")
def nrt_burst_path() -> Path:
    path = NRT_DIR / f"{BURST_NAME}.fil"
    if not path.exists():
        pytest.skip(f"Real NRT data not present at {path}")
    if not BURST_TABLE.exists():
        pytest.skip(f"Burst catalogue not present at {BURST_TABLE}")
    return path


@pytest.fixture(scope="module")
def nrt_session(nrt_burst_path: Path) -> BurstSession:
    session = BurstSession.from_file(str(nrt_burst_path), dm=BURST_DM)
    session.set_event_ms(BURST_TIME_MS - 3.5, BURST_TIME_MS + 3.5)
    burst_bin = session.ms_to_bin(BURST_TIME_MS)
    session.add_offpulse_ms(session.bin_to_ms(2000), session.bin_to_ms(burst_bin - 1000))
    session.add_offpulse_ms(session.bin_to_ms(burst_bin + 1000), session.bin_to_ms(75000))
    for channel in _masked_channels(_burst_row(BURST_NAME)):
        session.mask_channel_freq(float(session.freqs[channel]))
    return session


def test_the_nrt_preset_settles_the_basis_a_sigproc_header_cannot(nrt_session: BurstSession) -> None:
    capability = nrt_session.polarization_capability()
    assert capability["available"]
    assert capability["products"] == 4
    assert capability["basis"] == "coherency_linear"
    assert capability["basis_source"] == "preset:nrt"


def test_a_real_burst_yields_its_published_rotation_measure(nrt_session: BurstSession) -> None:
    result = nrt_session.run_polarization_analysis({"min_linear_snr": 3.0})
    rm = result.rm_synthesis

    assert rm["status"] == "ok"
    assert len(result.freqs_mhz) > 40, "most of the band should survive the mask and the S/N cut"
    assert rm["peak_rm_rad_m2"] == pytest.approx(EXPECTED_RM_RAD_M2, abs=3.0)
    assert rm["peak_snr"] > 50.0
    # R147 bursts are close to fully linearly polarized.
    assert result.linear_fraction > 0.8
    assert abs(result.circular_fraction) < 0.4
    # Nothing in this session established polarization calibration, so FLITS
    # must not present this as a source RM.
    assert result.calibration_status == "unknown"
    assert result.status == "calibration_required"
    assert "polarization_calibration_required" in result.warnings


def test_a_real_polarization_analysis_replays_from_its_snapshot(nrt_session: BurstSession) -> None:
    original = nrt_session.run_polarization_analysis({"min_linear_snr": 3.0})
    snapshot = nrt_session.snapshot_dict()

    restored = BurstSession.from_snapshot(snapshot)
    assert restored.polarization is not None
    assert restored.polarization.rm_synthesis["peak_rm_rad_m2"] == pytest.approx(
        original.rm_synthesis["peak_rm_rad_m2"]
    )

    recomputed = restored.run_polarization_analysis()
    assert recomputed.rm_synthesis["peak_rm_rad_m2"] == pytest.approx(original.rm_synthesis["peak_rm_rad_m2"], rel=1e-9)
    assert recomputed.masked_channels == original.masked_channels


def test_reading_the_products_in_the_wrong_basis_destroys_the_signal(nrt_session: BurstSession) -> None:
    linear = nrt_session.run_polarization_analysis({"min_linear_snr": 3.0})
    try:
        circular = nrt_session.run_polarization_analysis(
            {"min_linear_snr": 3.0, "polarization_basis": "coherency_circular"},
        )
        assert circular.linear_fraction < linear.linear_fraction
        assert circular.rm_synthesis["reduced_chi_square"] > linear.rm_synthesis["reduced_chi_square"]
    finally:
        # The session is module-scoped: hand it back on the basis it declares.
        nrt_session.set_polarization_settings({"polarization_basis": None})


def test_a_real_polarization_analysis_survives_an_export_bundle(nrt_session: BurstSession) -> None:
    """The science JSON forbids NaN, so real arrays are the test that matters."""
    import io
    import json

    from flits.exports import create_export_snapshot

    nrt_session.run_polarization_analysis({"min_linear_snr": 3.0})
    bundle = create_export_snapshot(
        nrt_session,
        session_id="nrt-polarization",
        include=["json", "csv", "npz", "plots"],
        plot_formats=["png"],
    )
    contents = bundle.contents
    names = {artifact.name: artifact for artifact in bundle.manifest.artifacts}

    science = json.loads(next(value for name, value in contents.items() if name.endswith("science.json")))
    assert science["polarization"]["polarization_basis"] == "coherency_linear"
    assert science["polarization"]["rm_synthesis"]["peak_rm_rad_m2"] == pytest.approx(EXPECTED_RM_RAD_M2, abs=3.0)

    csv_text = next(value for name, value in contents.items() if name.endswith("catalog.csv")).decode("utf-8")
    header, row = (line.split(",") for line in csv_text.splitlines()[:2])
    assert float(dict(zip(header, row, strict=True))["rm_rad_m2"]) == pytest.approx(EXPECTED_RM_RAD_M2, abs=3.0)

    with np.load(io.BytesIO(next(v for n, v in contents.items() if n.endswith("diagnostics.npz")))) as arrays:
        assert arrays["rm_phi_rad_m2"].size > 0
        assert np.all(np.isfinite(arrays["rm_polarized_amplitude"]))

    faraday = next(item for name, item in names.items() if "faraday_spectrum" in name)
    assert faraday.status == "ready"
