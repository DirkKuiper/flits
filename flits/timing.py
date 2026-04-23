from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy import units as u
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers


# Seconds for pc cm^-3 MHz^2. This matches the convention already used by
# FLITS' integer-bin dedispersion path.
DISPERSION_CONSTANT_S_MHZ2 = 1.0 / (2.41e-4)
SUPPORTED_TIME_SCALES = frozenset({"utc", "tdb", "tt", "tai"})


@dataclass(frozen=True)
class ObservatoryLocation:
    name: str | None = None
    longitude_deg: float | None = None
    latitude_deg: float | None = None
    height_m: float | None = None
    basis: str | None = None

    @property
    def is_complete(self) -> bool:
        values = (self.longitude_deg, self.latitude_deg, self.height_m)
        return all(value is not None and np.isfinite(float(value)) for value in values)


@dataclass(frozen=True)
class TimingContext:
    dm: float
    reference_frequency_mhz: float | None = None
    reference_frequency_basis: str | None = None
    source_ra_deg: float | None = None
    source_dec_deg: float | None = None
    source_position_frame: str = "icrs"
    source_position_basis: str | None = None
    time_scale: str = "utc"
    time_reference_frame: str = "topocentric"
    barycentric_header_flag: bool | None = None
    pulsarcentric_header_flag: bool | None = None
    observatory: ObservatoryLocation | None = None


@dataclass(frozen=True)
class TimingChain:
    toa_inf_topo_mjd: float | None
    toa_inf_bary_mjd_tdb: float | None
    dispersion_to_infinite_frequency_ms: float | None
    barycentric_correction_ms: float | None
    status: str
    status_reason: str


def dispersion_delay_to_infinite_frequency_ms(dm: float, reference_frequency_mhz: float) -> float:
    dm = float(dm)
    reference_frequency_mhz = float(reference_frequency_mhz)
    if not np.isfinite(dm):
        raise ValueError("DM must be finite.")
    if not np.isfinite(reference_frequency_mhz) or reference_frequency_mhz <= 0.0:
        raise ValueError("Reference frequency must be positive and finite.")
    return float(DISPERSION_CONSTANT_S_MHZ2 * dm * reference_frequency_mhz**-2.0 * 1e3)


def _has_source_position(context: TimingContext) -> bool:
    return (
        context.source_ra_deg is not None
        and context.source_dec_deg is not None
        and np.isfinite(float(context.source_ra_deg))
        and np.isfinite(float(context.source_dec_deg))
    )


def _blocked_by_existing_non_topocentric_frame(context: TimingContext) -> str | None:
    if context.pulsarcentric_header_flag:
        return "Header marks the input times as pulsarcentric; FLITS will not apply another barycentric correction."
    if context.barycentric_header_flag:
        return "Header marks the input times as barycentric; FLITS will not apply another barycentric correction."
    if str(context.time_reference_frame or "").lower() not in {"", "topocentric"}:
        return f"Input time reference frame is {context.time_reference_frame!r}, not topocentric."
    return None


def compute_toa_timing_chain(
    toa_peak_topo_mjd: float | None,
    context: TimingContext | None,
) -> TimingChain:
    if toa_peak_topo_mjd is None or not np.isfinite(float(toa_peak_topo_mjd)):
        return TimingChain(None, None, None, None, "unavailable", "No peak-bin topocentric TOA is available.")
    if context is None:
        return TimingChain(None, None, None, None, "peak_topo_only", "No timing context was supplied.")

    blocked_reason = _blocked_by_existing_non_topocentric_frame(context)
    if blocked_reason is not None:
        return TimingChain(None, None, None, None, "blocked_existing_time_frame", blocked_reason)

    assumed_already_infinite = False
    if context.reference_frequency_mhz is None:
        if abs(float(context.dm)) > 0.0:
            return TimingChain(
                None,
                None,
                None,
                None,
                "peak_topo_only",
                "Missing finite dedispersion reference frequency for infinite-frequency correction.",
            )
        dispersion_ms = 0.0
        toa_inf_topo_mjd = float(toa_peak_topo_mjd)
        assumed_already_infinite = True
    else:
        try:
            dispersion_ms = dispersion_delay_to_infinite_frequency_ms(
                context.dm,
                float(context.reference_frequency_mhz),
            )
        except Exception as exc:
            return TimingChain(None, None, None, None, "peak_topo_only", str(exc))

        toa_inf_topo_mjd = float(toa_peak_topo_mjd) - (dispersion_ms / 1e3 / 86400.0)

    assumption_note = (
        "Assuming DM 0 input is already referenced to infinite frequency."
        if assumed_already_infinite
        else None
    )

    if not _has_source_position(context):
        return TimingChain(
            toa_inf_topo_mjd,
            None,
            dispersion_ms,
            None,
            "infinite_frequency_only",
            (
                f"{assumption_note} Missing source RA/Dec for barycentric correction."
                if assumption_note is not None
                else "Missing source RA/Dec for barycentric correction."
            ),
        )

    observatory = context.observatory
    if observatory is None or not observatory.is_complete:
        return TimingChain(
            toa_inf_topo_mjd,
            None,
            dispersion_ms,
            None,
            "infinite_frequency_only",
            (
                f"{assumption_note} Missing observatory location for barycentric correction."
                if assumption_note is not None
                else "Missing observatory location for barycentric correction."
            ),
        )

    time_scale = str(context.time_scale or "utc").lower()
    if time_scale not in SUPPORTED_TIME_SCALES:
        return TimingChain(
            toa_inf_topo_mjd,
            None,
            dispersion_ms,
            None,
            "infinite_frequency_only",
            (
                f"{assumption_note} Unsupported time scale {context.time_scale!r}."
                if assumption_note is not None
                else f"Unsupported time scale {context.time_scale!r}."
            ),
        )

    try:
        location = EarthLocation.from_geodetic(
            lon=float(observatory.longitude_deg) * u.deg,
            lat=float(observatory.latitude_deg) * u.deg,
            height=float(observatory.height_m) * u.m,
        )
        source = SkyCoord(
            ra=float(context.source_ra_deg) * u.deg,
            dec=float(context.source_dec_deg) * u.deg,
            frame=str(context.source_position_frame or "icrs").lower(),
        )
        with iers.conf.set_temp("auto_download", False):
            topo_time = Time(toa_inf_topo_mjd, format="mjd", scale=time_scale, location=location)
            light_travel_time = topo_time.light_travel_time(source, kind="barycentric")
            bary_time = topo_time.tdb + light_travel_time
    except Exception as exc:
        return TimingChain(
            toa_inf_topo_mjd,
            None,
            dispersion_ms,
            None,
            "infinite_frequency_only",
            f"Barycentric correction failed: {exc}",
        )

    return TimingChain(
        toa_inf_topo_mjd=toa_inf_topo_mjd,
        toa_inf_bary_mjd_tdb=float(bary_time.mjd),
        dispersion_to_infinite_frequency_ms=dispersion_ms,
        barycentric_correction_ms=float(light_travel_time.to_value(u.ms)),
        status="barycentric_tdb",
        status_reason=(
            "Assuming DM 0 input is already referenced to infinite frequency; barycentric infinite-frequency TDB TOA is available."
            if assumed_already_infinite
            else "Barycentric infinite-frequency TDB TOA is available."
        ),
    )
