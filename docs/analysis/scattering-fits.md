# Scattering Fits

FLITS exposes an optional fitburst-backed scattering workflow.

## Dependency model

The fitting tab is available only when `fitburst` is installed. This dependency
is optional and must currently be installed separately from the main PyPI
package:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## What FLITS passes to fitburst

The adapter uses the current session state:

- selected frequency band
- current crop
- event window
- current mask
- off-pulse normalization

It fits only the selected event window and returns structured success or failure
results instead of hard import-time failures.

## How to use the results

Treat these outputs as model-based diagnostics:

- intrinsic component width
- scattering timescale
- parameter uncertainties
- fit diagnostics

They are useful cross-checks, but they should be interpreted alongside the
non-parametric measurements and temporal diagnostics rather than as a complete
replacement for them.
