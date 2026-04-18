# DM Optimization

FLITS can sweep trial DMs around the current value and score them using one of
the registered DM metrics.

## Available metrics

The software currently exposes two main approaches:

- integrated-event S/N
- DMphase

Integrated-event S/N is a direct, profile-based metric. DMphase is a
phase-coherent Fourier method implemented natively inside FLITS so it can work
against the current reduced grid, masking state, crop, and selection.

## What the DM sweep uses

The optimization operates on the current session state:

- current dynamic spectrum
- current crop
- event window
- spectral window
- channel mask
- reduced-resolution state

Changing those inputs can change the preferred DM.

## Outputs to inspect

After a sweep, the useful things to compare are:

- the score curve versus trial DM
- the preferred DM and its local fit status
- residual arrival-time behavior at the applied and optimized DMs

## Practical guidance

- Use a sensible event window before running the sweep.
- Restrict the spectral window to the part of the band you actually trust.
- Mask bad channels first.
- Treat the optimized DM as local to the chosen analysis state, not as a
  context-free universal value.
