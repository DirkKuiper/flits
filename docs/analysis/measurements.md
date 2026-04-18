# Measurements

FLITS reports measurements from the current prepared session state.

## Core outputs

The measurement workflow can report:

- fluence in Jy ms, when an SEFD is available
- peak flux density in Jy, when an SEFD is available
- event duration in ms
- spectral extent in MHz
- peak MJD
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
- the off-pulse definition
- the selected spectral extent
- the applied channel mask
- the current DM and reduced analysis state

Interpret exported values together with the stored provenance, not as context-free
numbers.

## Event duration versus burst width

The event duration is the manually selected event-window span. It is useful
session metadata, but it is not automatically the same thing as a
model-independent burst-duration estimate or the same thing as a fitted
component width.
