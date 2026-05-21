# Model Fitting

FLITS exposes an optional selected-event 2D model-fitting workflow in the
Fitting tab. It uses the live session state: crop, event window, frequency
window, off-pulse regions, masks, display downsampling, and component
annotations. FLITS does not mirror command-line preprocessing flags from the
underlying solver because those choices are already represented by the
interactive session.

## Dependency model

The Fitting tab can display initial guesses and previous results without the
optional solver backend. Running the model fit requires `fitburst` in the
environment used to launch FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

When the dependency is unavailable or a fit cannot be constrained, FLITS stores
a structured failure diagnostic instead of failing at import time.

## Fit parameters

The primary control is **Parameters to Fit**. Checked parameters are varied by
the optimizer; unchecked parameters are held fixed at their initial values. The
available optimizer parameters are amplitude, arrival time, intrinsic width,
scattering timescale, residual DM, DM index, scattering index, spectral index,
and spectral running.

The default interactive setup varies amplitude, arrival time, intrinsic width,
and scattering timescale while holding spectrum, DM, and index terms fixed.
This is the conservative default for measuring model width and scattering time
from the current selection. Internally FLITS derives the fixed-parameter list
from the submitted free parameters before calling the solver.

## Initial parameters

FLITS seeds the model from current component regions, manual peak markers, or a
single automatic component from the integrated profile. The **Detect
Components** helper can populate editable component rows from profile peaks.

Initial parameters include per-component amplitude, arrival time, burst width,
spectral index, spectral running, and reference frequency. Shared model values
include DM, DM index, scattering timescale, and scattering index.

The **Seed from Previous Fit** option initializes a run from the previous
successful model fit in the same session when the component count and current
event window are compatible. The advanced setup can also import a
fit-compatible solution JSON containing `model_parameters`.

## Solver setup

Advanced solver controls include weighting mode, optional manual weight range,
optimizer passes, maximum function evaluations, time and frequency upsampling,
reference frequency, folded profile mode, exact-Jacobian mode, and
scintillation mode.

**Optimizer Passes** restarts the solver from the previous best fit for each
pass. **Max Evaluations** controls the residual-evaluation budget per pass; if
left blank, SciPy uses its default budget of 100 times the number of scalar fit
parameters.

Weighting modes are:

- **None**: unweighted residuals.
- **Auto off-pulse**: derive per-channel weights from current off-pulse bins
  when possible, otherwise fall back to fit-window weighting.
- **Fit window**: let the solver estimate weights inside the fitted event
  window.
- **Manual range**: use the submitted time-bin range.

In scintillation mode, amplitude, spectral index, and spectral running are not
optimizer parameters because per-channel amplitudes are derived from the data.

## Diagnostics and exports

Model-fit results report free and fixed parameters, initialization source,
solver settings, best-fit parameters, uncertainties, residual dynamic spectra,
profile residuals, fit statistics, and sanitized solver diagnostics on failure.

Exports include fit-compatible solution JSON using `model_parameters`,
`fit_statistics`, and solver logistics. Measurement outputs still populate
`width_ms_model` and `tau_sc_ms` when those best-fit values exist.

Treat model-derived width, scattering time, parameter uncertainties, and
chi-squared values as model-based diagnostics. They are useful cross-checks,
while non-parametric FLITS measurements, DM diagnostics, and temporal
structure analysis remain the primary session measurements.
