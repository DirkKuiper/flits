from __future__ import annotations

import pytest
from astropy import units as u
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers

from flits.timing import (
    ObservatoryLocation,
    TimingContext,
    compute_toa_timing_chain,
    dispersion_delay_to_infinite_frequency_ms,
)


def test_dispersion_delay_to_infinite_frequency_has_expected_sign_and_magnitude() -> None:
    delay_ms = dispersion_delay_to_infinite_frequency_ms(100.0, 1000.0)

    assert delay_ms > 0.0
    assert delay_ms == pytest.approx(414.9377593360996)


def test_barycentric_correction_matches_direct_astropy_call() -> None:
    observatory = ObservatoryLocation(
        name="Green Bank Telescope",
        longitude_deg=-79.839722,
        latitude_deg=38.433056,
        height_m=807.0,
        basis="test",
    )
    context = TimingContext(
        dm=10.0,
        reference_frequency_mhz=1400.0,
        source_ra_deg=180.0,
        source_dec_deg=30.0,
        time_scale="utc",
        observatory=observatory,
    )

    chain = compute_toa_timing_chain(60000.0, context)

    location = EarthLocation.from_geodetic(
        lon=observatory.longitude_deg * u.deg,
        lat=observatory.latitude_deg * u.deg,
        height=observatory.height_m * u.m,
    )
    source = SkyCoord(ra=180.0 * u.deg, dec=30.0 * u.deg, frame="icrs")
    topo_inf = 60000.0 - (chain.dispersion_to_infinite_frequency_ms / 1000.0 / 86400.0)
    with iers.conf.set_temp("auto_download", False):
        topo_time = Time(topo_inf, format="mjd", scale="utc", location=location)
        expected = (topo_time.tdb + topo_time.light_travel_time(source, kind="barycentric")).mjd

    assert chain.status == "barycentric_tdb"
    assert chain.toa_inf_bary_mjd_tdb == pytest.approx(expected, abs=1e-12)
    assert chain.barycentric_correction_ms is not None


def test_missing_source_position_stops_after_infinite_frequency() -> None:
    context = TimingContext(
        dm=100.0,
        reference_frequency_mhz=1000.0,
        observatory=ObservatoryLocation(longitude_deg=0.0, latitude_deg=0.0, height_m=0.0),
    )

    chain = compute_toa_timing_chain(60000.0, context)

    assert chain.status == "infinite_frequency_only"
    assert chain.toa_inf_topo_mjd is not None
    assert chain.toa_inf_bary_mjd_tdb is None
    assert "RA/Dec" in chain.status_reason


def test_dm_zero_without_reference_frequency_assumes_infinite_frequency() -> None:
    context = TimingContext(
        dm=0.0,
        reference_frequency_mhz=None,
        source_ra_deg=180.0,
        source_dec_deg=30.0,
        time_scale="utc",
        observatory=ObservatoryLocation(
            name="Green Bank Telescope",
            longitude_deg=-79.839722,
            latitude_deg=38.433056,
            height_m=807.0,
            basis="test",
        ),
    )

    chain = compute_toa_timing_chain(60000.0, context)

    location = EarthLocation.from_geodetic(
        lon=context.observatory.longitude_deg * u.deg,
        lat=context.observatory.latitude_deg * u.deg,
        height=context.observatory.height_m * u.m,
    )
    source = SkyCoord(ra=180.0 * u.deg, dec=30.0 * u.deg, frame="icrs")
    with iers.conf.set_temp("auto_download", False):
        topo_time = Time(60000.0, format="mjd", scale="utc", location=location)
        expected = (topo_time.tdb + topo_time.light_travel_time(source, kind="barycentric")).mjd

    assert chain.status == "barycentric_tdb"
    assert chain.toa_inf_topo_mjd == pytest.approx(60000.0, abs=1e-12)
    assert chain.dispersion_to_infinite_frequency_ms == pytest.approx(0.0, abs=1e-12)
    assert chain.toa_inf_bary_mjd_tdb == pytest.approx(expected, abs=1e-12)
    assert "Assuming DM 0 input is already referenced to infinite frequency" in chain.status_reason


def test_missing_observatory_stops_after_infinite_frequency() -> None:
    context = TimingContext(
        dm=100.0,
        reference_frequency_mhz=1000.0,
        source_ra_deg=1.0,
        source_dec_deg=2.0,
    )

    chain = compute_toa_timing_chain(60000.0, context)

    assert chain.status == "infinite_frequency_only"
    assert chain.toa_inf_topo_mjd is not None
    assert chain.toa_inf_bary_mjd_tdb is None
    assert "observatory" in chain.status_reason


def test_unknown_time_scale_reports_status_without_raising() -> None:
    context = TimingContext(
        dm=100.0,
        reference_frequency_mhz=1000.0,
        source_ra_deg=1.0,
        source_dec_deg=2.0,
        time_scale="local_clock",
        observatory=ObservatoryLocation(longitude_deg=0.0, latitude_deg=0.0, height_m=0.0),
    )

    chain = compute_toa_timing_chain(60000.0, context)

    assert chain.status == "infinite_frequency_only"
    assert "Unsupported time scale" in chain.status_reason


def test_existing_barycentric_header_blocks_second_correction() -> None:
    context = TimingContext(
        dm=100.0,
        reference_frequency_mhz=1000.0,
        source_ra_deg=1.0,
        source_dec_deg=2.0,
        barycentric_header_flag=True,
        observatory=ObservatoryLocation(longitude_deg=0.0, latitude_deg=0.0, height_m=0.0),
    )

    chain = compute_toa_timing_chain(60000.0, context)

    assert chain.status == "blocked_existing_time_frame"
    assert chain.toa_inf_topo_mjd is None
    assert "barycentric" in chain.status_reason
