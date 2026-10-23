# Measurements

FLITS reports measurements from the current prepared session state.

![Prepare tab showing selected windows, headline measurements, signal metrics, and uncertainty flags for the GBT tutorial burst.](../assets/guided-workflow/measurement-summary.png)

*The Prepare tab groups selection readiness, headline measurements, timing
details, and uncertainty flags. These values come from the [GBT guided
workflow](../guided-workflow.md); its absolute TOA fields are not part of the
validated reference calculation.*

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

## Uncertainties

Every reported quantity carries its uncertainty together with what that
uncertainty includes. Flux-like uncertainties always include radiometer noise,
as a 1σ statistical error (`statistical_only`). Supply the fractional
uncertainty on the SEFD and FLITS combines the two in quadrature into a
complete 1σ uncertainty (`formal_1sigma`):

```python
session = BurstSession.from_file(
    "burst.fil",
    dm=527.65,
    sefd_jy=10.0,
    sefd_fractional_uncertainty=0.1,  # 10% SEFD uncertainty
)
```

Without it, the uncertainty is the radiometer noise alone. That is a genuine
1σ statistical error, but the SEFD term usually dominates, so quote it as
statistical and state the calibration uncertainty separately, or supply the
SEFD uncertainty. [Uncertainties](../user-guide/uncertainties.md) describes every
kind of uncertainty FLITS reports.

Each quantity also records the *basis* of its uncertainty in words: which noise
reference was used, and which terms were combined. That text is carried into
exports, so a number in a table can be traced back to how it was derived
without rerunning anything.

Distance-dependent quantities behave the same way: isotropic energy includes
the distance term once `distance_fractional_uncertainty` is supplied as well.

!!! tip "Check the flags before quoting a number"
    `measurement_flags` records what was missing or suspect —
    `missing_sefd`, `missing_sefd_fractional_uncertainty`, `edge_clipped` and
    others. They are the fastest way to see whether a value is ready to use.

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

Width methods normally run the configured off-pulse Monte Carlo uncertainty
trials. Large population pipelines that calibrate uncertainties end-to-end can
set `WidthAnalysisSettings(uncertainty_trials=0)` to compute the width values
without this per-burst bootstrap. Such results carry the
`uncertainty_disabled` quality flag and do not report a width uncertainty.
