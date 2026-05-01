# Scattering Fits

FLITS exposes an optional fitburst-backed model-fitting workflow in the
Fitting tab. The workflow is intentionally a FLITS selected-event fit, not a
raw `fitburst_pipeline.py` clone: it uses the current crop, event window,
frequency window, off-pulse regions, masks, and reduced display grid from the
live session.

## Dependency model

The fitting tab can display guesses and previous results without fitburst, but
running the 2D model fit requires the optional fitburst dependency. Install it
in the environment used to launch FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

When fitburst is unavailable or a fit cannot be constrained, FLITS stores a
structured failure diagnostic instead of failing at import time.

## What FLITS passes to fitburst

FLITS builds a selected dynamic spectrum from the current session state:

- selected spectral window and channel mask
- current crop and event window
- off-pulse normalization bins
- current time and frequency reduction factors
- optional component regions or manual peak markers

Only the selected event window is fit. Off-pulse bins are used for
normalization, and, when weighted fitting is enabled, for per-channel weights.

## Fit profiles

The default profile is **FLITS scattering**. It preserves the historical FLITS
behavior: one fit iteration, unweighted residuals, and fixed DM, DM index,
scattering index, spectral index, and spectral running. The fitted parameters
are amplitude, arrival time, intrinsic width, and shared scattering timescale.

The **Advanced fitburst-like** profile enables weighted, iterative fitting by
default. It runs three fit iterations, weights residuals by per-channel noise,
and leaves spectral terms free while keeping DM index and scattering index
fixed. You can still edit the fixed-parameter list before running the fit.

For multi-component fits, FLITS labels global fitburst parameters as shared:
DM, DM index, scattering index, and scattering tau are represented by shared
model values, while arrival time, intrinsic width, amplitude, spectral index,
and spectral running are per-component values unless scintillation mode changes
the amplitude/spectral semantics.

## Advanced options

**Seed from Previous Fit** initializes the next run from the previous successful
FLITS fit in the same session. FLITS sanitizes the previous best-fit parameters
against the current component count, current event window, and selected band.
If the previous fit is not compatible with the current selection, FLITS falls
back to the current selected-event guesses. Importing external fitburst
solution JSON is out of scope for this workflow.

**Scintillation Mode** passes `scintillation=True` to fitburst's
`SpectrumModeler`. In this mode fitburst derives per-channel amplitudes from
the data, so amplitude, spectral index, and spectral running are not optimizer
parameters. Use this only when amplitude-independent per-channel modeling is
scientifically appropriate for the burst.

Weighted fitting uses per-channel noise estimates. If no explicit weight range
is supplied, FLITS derives weights from the current off-pulse bins and records
the weight-range basis in diagnostics.

Iterations run consecutive fitburst optimizer passes. After each successful
iteration, the next iteration is initialized from the previous best-fit
parameters. If a later iteration fails, FLITS preserves the last successful
fit values while recording the failed diagnostic status and sanitized fitburst
stdout/stderr.

Upsampling and reference-frequency controls are available through the API
payload. The browser defaults keep both upsampling factors at 1 and use the
minimum selected frequency as the reference frequency unless the API provides
`ref_freq_mhz`.

## Diagnostics and interpretation

Treat fitburst-derived width, scattering time, parameter uncertainties, and
chi-squared values as model-based diagnostics. They are useful cross-checks,
but the non-parametric FLITS measurements, DM diagnostics, and temporal
structure analysis remain the primary session measurements.

Saved sessions and exports include the fit profile, initialization source,
weighted-fit status, weight-range basis, scintillation flag, iteration counts,
model-control values, fixed/fitted parameter lists, best-fit parameters,
fitburst uncertainties, and residual dynamic spectra when available.

The historical fitburst integration audit, when present in a development
branch, should be treated as planning history. This page is the live user-facing
reference for the implemented behavior.
