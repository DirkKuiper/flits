# Measurements

FLITS reports measurements from the current prepared session state.

## Core outputs

The measurement workflow can report:

- fluence in Jy ms, when an SEFD is available
- peak flux density in Jy, when an SEFD is available
- event duration in ms
- spectral extent in MHz
- peak-bin topocentric/source-header TOA (`toa_peak_topo_mjd`)
- infinite-frequency topocentric TOA (`toa_inf_topo_mjd`) when FLITS knows the
  dedispersion reference frequency
- barycentric infinite-frequency TDB TOA (`toa_inf_bary_mjd_tdb`) when source
  position, observatory location, and time-scale metadata are complete
- one-dimensional Gaussian fits for selected burst regions

## Calibration note

Flux and fluence require calibration information. If FLITS can identify a known
observing setup, it may provide a default SEFD. Otherwise use the generic preset
or specify an SEFD explicitly.

Without an SEFD, FLITS can still report selection- and timing-related outputs,
but not calibrated flux-density values.

## Provenance matters

Measurements depend on:

- the event window
- manual peaks, when they fall inside the selected event window
- the off-pulse definition
- the selected spectral extent
- the applied channel mask
- the current DM and reduced analysis state
- timing metadata: source position, observatory location, time scale, time
  reference frame, and dedispersion reference frequency

Interpret exported values together with the stored provenance, not as context-free
numbers.

## TOA Timing Chain

FLITS V1 still uses a peak-bin estimator for TOA. The primary value is the time
of the strongest event-window bin, or a manual peak only when that manual peak is
inside the current event window. `toa_topo_mjd` and `mjd_at_peak` are retained as
compatibility aliases for `toa_peak_topo_mjd`.

The infinite-frequency correction subtracts the cold-plasma dispersion delay
from the finite dedispersion reference frequency to infinity:

`delay_ms = 1000 * DM / (2.41e-4) * reference_frequency_mhz^-2`

This value is reported as `dispersion_to_infinite_frequency_ms`. The correction
is only applied when FLITS can identify a trustworthy reference frequency. For
ordinary FLITS integer-bin dedispersion this is the highest channel frequency.
Public CHIME catalog waterfalls are treated conservatively: their public file
format does not by itself prove the reference frequency, so FLITS leaves the
infinite-frequency TOA unavailable unless stronger metadata are supplied.

The barycentric value uses Astropy `Time`, `EarthLocation`, and `SkyCoord` to add
the Solar-System barycentric light-travel-time correction to the infinite-
frequency topocentric TOA, then reports the result in TDB. It requires:

- source RA and Dec in ICRS decimal degrees
- a complete observatory longitude, latitude, and height
- a supported input time scale: UTC, TDB, TT, or TAI
- topocentric input times

If a header says the data are already barycentric or pulsarcentric, FLITS does
not apply another barycentric correction. The measurement payload records
`toa_status` and `toa_status_reason` so exports show whether the chain stopped at
the peak-bin value, reached infinite-frequency topocentric timing, or reached
barycentric TDB timing.

These corrected TOAs are useful for consistent reporting, but they are still
resolution-limited because the V1 estimator is the selected time bin, not a
centroid, template, or likelihood-model TOA with a formal statistical
uncertainty.

## Event duration versus burst width

The event duration is the manually selected event-window span. It is useful
session metadata, but it is not automatically the same thing as a
model-independent burst-duration estimate or the same thing as a fitted
component width.
