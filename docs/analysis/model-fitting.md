# Model Fitting

FLITS exposes a selected-event 2D model-fitting workflow in the
Fitting tab. It uses the live session state: crop, event window, frequency
window, off-pulse regions, masks, display downsampling, and component
annotations. FLITS does not mirror command-line preprocessing flags from the
underlying solver because those choices are already represented by the
interactive session.

## The model

FLITS fits the dynamic-spectrum model of Fonseca et al. (2024), the model the
`fitburst` package implements. Each component is a Gaussian, convolved with a
one-sided exponential when the scattering timescale is positive, with a running
power-law spectrum. DM, DM index, scattering timescale, and scattering index are
shared by all components.

FLITS evaluates the model itself, so fitting needs no extra package. The
implementation is written from the paper's equations, evaluates every channel
and component at once, and has analytic derivatives. Parameter names, units,
and solution JSON follow `fitburst`, so a solution moves between the two
programs unchanged.

The test suite compares FLITS with `fitburst` in every model mode: time and
frequency upsampling, folded profiles, scintillation, an incoherent DM, and data
that are not dedispersed. On synthetic bursts fitted from the same starting
point, FLITS reaches `fitburst`'s minimum to within 0.001σ, its uncertainties
agree to within 0.02%, and the fit runs five to eight times faster. A
scintillation-mode fit runs about 50 times faster.

Where FLITS differs from `fitburst`:

- **Bounds.** Widths and the scattering timescale cannot go negative. When the
  scattering timescale ends at zero, the fit lists it in
  `parameters_at_bounds` and its uncertainty is one-sided: quote the value plus
  the uncertainty as an upper limit.
- **Unresolved scattering.** In `fitburst`'s profile normalization the
  amplitude grows without bound as the scattering timescale shrinks. The solver
  steps the amplitude of the Gaussian that the profile tends to instead, so a
  fit of an unscattered burst converges to the Gaussian fit. Results are
  reported in `fitburst`'s convention.
- **Covariance.** Like `fitburst`, FLITS inverts the observed Hessian of
  chi-squared. If that matrix is not positive definite, it inverts the
  Gauss-Newton matrix instead, and `covariance_basis` records which one it
  used. The full covariance matrix and its parameter labels are stored with the
  fit.
- **Scintillation mode.** Each channel's amplitudes are solved by linear least
  squares at every step, and the derivatives include how those amplitudes move
  with the other parameters (variable projection; Golub & Pereyra 1973). In 40
  simulated scintillating bursts, the scatter of FLITS's best fits matches its
  reported uncertainties to within 7%. In the same test, the scatter is 1.5 to
  4 times larger than the uncertainties `fitburst` reports.
- **Fit statistics** add AIC and BIC, and in scintillation mode the number of
  per-channel amplitudes, to `fitburst`'s chi-squared, reduced chi-squared, and
  S/N.
- **Before each scattered component**, `fitburst` holds the profile at a floor
  of about 10⁻⁵ of its peak. FLITS evaluates the profile as written.

Before fitting, FLITS puts the selected band in off-pulse S/N units: it
subtracts each channel's off-pulse mean and divides the whole band by the
median off-pulse noise of its channels. This assumes a bandpass-flattened
spectrum, which is what FLITS's readers produce. Per-channel noise enters
through the weights, not through the scaling.

The Fitting tab can display initial guesses and previous results before a fit
has run. When a fit cannot be constrained, FLITS stores a structured failure
diagnostic instead of raising an error.

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
pass. **Max Evaluations** controls the model-evaluation budget per pass; if
left blank, FLITS uses 100 times the number of fitted values, SciPy's default
for least squares.

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
