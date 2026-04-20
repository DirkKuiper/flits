"""Format-matrix smoke test: BurstSession must produce a sane view and
run the basic analyses regardless of which input format produced the data.

This complements [test_session_smoke.py](test_session_smoke.py) (which hits a
real GBT filterbank) by exercising every supported reader against a synthetic
fixture, so CI can verify reader parity without needing committed sample data.
"""
from __future__ import annotations

import pytest

from flits.session import BurstSession


_ALL_FORMATS = ["sigproc", "chime_hdf5", "psrfits", "chime_bbdata_beamformed"]


@pytest.mark.parametrize("synthetic_waterfall", _ALL_FORMATS, indirect=True)
def test_session_from_file_opens_all_formats(synthetic_waterfall):
    session = BurstSession.from_file(
        str(synthetic_waterfall.path),
        dm=synthetic_waterfall.coherent_dm or 0.0,
        sefd_jy=1.0,
        telescope="generic",
    )
    view = session.get_view()
    assert view["meta"]["shape"][0] == synthetic_waterfall.data.shape[0]
    assert view["meta"]["shape"][1] > 0
    assert view["meta"]["preset_key"] == "generic"


@pytest.mark.parametrize("synthetic_waterfall", _ALL_FORMATS, indirect=True)
def test_session_compute_properties_on_all_formats(synthetic_waterfall):
    session = BurstSession.from_file(
        str(synthetic_waterfall.path),
        dm=synthetic_waterfall.coherent_dm or 0.0,
        sefd_jy=1.0,
        telescope="generic",
    )
    view = session.get_view()
    peak_ms = view["state"]["peak_ms"][0]
    session.set_event_ms(peak_ms - 2.0, peak_ms + 2.0)

    measurements = session.compute_properties()
    assert measurements.peak_flux_jy is not None
    assert measurements.fluence_jyms is not None
    payload = measurements.to_dict()
    assert "uncertainties" in payload
    assert "provenance" in payload
