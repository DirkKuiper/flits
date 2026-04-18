# Temporal and Spectral Analysis

The temporal workflow in FLITS combines several related diagnostics on the
selected-band event profile.

## What is included

- ACF diagnostics
- a raw periodogram
- an averaged PSD
- minimum-structure scale scans
- a simple power-law-plus-constant PSD fit
- an inferred crossover frequency when the fit is well constrained

## Segment length trade-off

Segment length is one of the most important controls:

- longer segments improve frequency resolution
- shorter segments increase the number of independent averages

There is no universally correct choice; it depends on event length and the
timescales you care about.

## How to interpret the scale scan

The minimum-structure scan is not the same thing as full burst duration. It is
asking for the smallest statistically significant structure visible in the
current event profile.

If the response keeps rising at the largest tested scale, that usually indicates
broad emission or the absence of a turnover within the tested range.

## ACF versus duration

ACF widths are self-similarity scales. They are useful for comparing coherence
structure in time or frequency, but they are not automatically identical to the
full burst duration.

## PSD model caveat

The PSD fit is intentionally simple. When the fit is poorly constrained, the
stored fit status matters more than the nominal parameter values.
