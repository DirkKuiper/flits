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

## Fit window

The fit covers the event window plus padding on each side, so the baseline
around the burst and any scattering tail beyond the event window are part of
the fit. By default the padding is the event window's own length, at least 8
bins, and it shrinks if the window would exceed the interactive size limit.
**Fit Window Padding** sets the number of bins instead; 0 fits the event window
alone. Fit Details reports the window that was fitted.

## Time resolution and upsampling

The data in each time bin are an average over the bin, so the model has to be
averaged the same way. Evaluated only at bin centres, a component narrower than
a bin needs a wider Gaussian to put flux into the neighbouring bins, and its
fitted width comes out too large: on synthetic 0.3 ms bursts in 1 ms bins,
fits at bin centres return about 0.4 ms, while upsampled fits recover 0.3 ms.

**Time Upsampling** is the number of sub-samples per bin that the model is
averaged over. Left blank (auto), FLITS fits once at the data resolution and,
if the narrowest fitted width is under three bins, refits from that solution
with enough sub-samples that each is at most a third of that width (up to 16,
fewer on very large windows). Fit Details shows the factor used and whether it
was chosen automatically.

When the fitted width or scattering timescale is shorter than one time bin, the
results say so. An upsampled fit is constrained by how the burst's flux spreads
into the neighbouring bins, so expect large uncertainties or an upper limit. A
fit without upsampling offers to fit again with automatic upsampling.

## Solver setup

Advanced solver controls include weighting mode, optional manual weight range,
optimizer passes, maximum function evaluations, time and frequency upsampling,
fit-window padding, reference frequency, folded profile mode, exact-Jacobian
mode, and scintillation mode. The reference frequency, at which the scattering
timescale and the spectrum are quoted, defaults to the lowest frequency of the
selected band.

**Optimizer Passes** restarts the solver from the previous best fit for each
pass. **Max Evaluations** controls the model-evaluation budget per pass; if
left blank, FLITS allows at least 1,000 evaluations, or 100 times the number
of fitted values when that is larger. Correlated width, scattering and DM
parameters can need several hundred evaluations even for a small model. This
is a maximum, so a converged fit still stops early. An explicit value overrides
Auto, including a smaller budget.

When a pass reaches its limit, the result remains `evaluation_limit_exceeded`:
its width, scattering time and uncertainties are not accepted measurements.
The best parameters reached are retained in `bestfit_parameters`, including
in saved sessions. Increase the limit and select **Seed from Previous Fit**
to continue from that candidate instead of the original guesses. The Python
equivalent is `session.fit_model({"seed_from_previous_fit": True, "solver":
{"max_function_evaluations": 2000}})`. The resumed fit must converge before its
measurements are accepted. A larger budget cannot resolve an unconstrained
model or an inappropriate selection; inspect the residuals and parameter
correlations after convergence.

**Fit Details → Fitter** identifies the implementation that produced the
saved result. Older sessions can retain `fitburst` results; rerun the fit in
the current FLITS environment to compute a new native result. Restart a server
after upgrading its environment, and check that it imports the intended
checkout when running from source.

Weighting modes are:

- **None**: unweighted residuals.
- **Auto off-pulse**: derive per-channel weights from current off-pulse bins
  when possible, otherwise fall back to fit-window weighting.
- **Fit window**: estimate each channel's weight from its RMS over the fitted
  window.
- **Manual range**: estimate it over a range of time bins counted from the start
  of the event window.

In scintillation mode, amplitude, spectral index, and spectral running are not
optimizer parameters because per-channel amplitudes are derived from the data.

## Reading the results

The results start with the fit status, then the intrinsic width, the scattering
timescale at the reference frequency, and the reduced chi-squared. Notes follow
when something needs attention:

- **A value consistent with zero.** When the width or the scattering timescale
  ends at its lower bound of zero, only an upper limit is constrained. It is
  shown as `< value + 1σ`.
- **Shorter than one time bin.** See [Time resolution and upsampling](#time-resolution-and-upsampling).
- **Strongly correlated parameters** (|ρ| ≥ 0.8). Each 1σ uncertainty already
  allows for the other parameters adjusting, but a quantity that combines two
  correlated parameters needs their covariance, not the separate uncertainties.
  Correlations with the amplitude are left out of the note: in `fitburst`'s
  profile normalization the amplitude scales with the scattering timescale, so
  the two are always strongly correlated.

The parameter table lists each best-fit value with its uncertainty, or says
`fixed`, `upper limit`, or, in scintillation mode, `per channel`. **Fit
Details** holds the fitted window, time resolution, upsampling, weighting,
solver counts, AIC and BIC, which covariance was inverted, and the full
correlation matrix.

## Comparing component counts

**Compare Component Counts** fits the selection with one component, then two,
up to the chosen maximum (at most 5). Each fit starts from the one before it
with a component added at the largest positive residual of the event profile.
All the fits use the same samples, weights and time upsampling, so their
chi-squared values compare directly. The table reports, for each component
count, the reduced chi-squared, ΔBIC and ΔAIC relative to the best, and the
F-test p-value against one component fewer: the probability of an improvement
that large if the extra component were not needed. The fit with the lowest BIC
is marked. **Use This Fit** refits that row from its solution and makes it the
session's model fit.

A component the data do not support often fails to converge: nothing stops it
shrinking onto a noise spike. The comparison then stops there and compares the
fits that converged. Treat BIC and AIC as guidance. At high S/N, components are
rarely exactly Gaussian, so extra components keep lowering chi-squared even
when they do not describe separate emission; check the residuals before
adopting one.

From Python:

```python
results = session.compare_model_components(max_components=3)
comparison = results.diagnostics.model_comparison
best = next(row for row in comparison.rows if row.num_components == comparison.preferred_components)
session.fit_model({"num_components": best.num_components, "initial_parameters": best.bestfit_parameters})
```

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
