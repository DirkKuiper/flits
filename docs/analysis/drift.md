# Sub-Burst Drift

Repeating FRBs drift downward in frequency: later emission arrives at lower
frequencies. FLITS reports that drift as
\( \mathrm{d}\nu/\mathrm{d}t \) in **MHz per millisecond**, negative for the
usual downward "sad trombone" and positive for the rarer upward drift.

Everything on this page runs on the current selection — crop, event window,
off-pulse windows, channel mask, spectral window, component regions and applied
DM. Changing any of them invalidates the measurement, and FLITS discards the
cached result rather than showing a stale one.

## Two estimators, two questions

| Estimator | What it measures | What it needs |
| --- | --- | --- |
| `acf_2d` | The **sub-burst slope**: how the emission centroid moves in frequency as one component proceeds. | A single event window with enough time bins and channels. |
| `component_centroid` | The **inter-component drift**: the step in centre frequency from one sub-burst to the next. | At least two component regions (or two manual peaks). |

Editing the component regions or the manual peaks discards a cached drift
result, because the second estimator is built from them.

They are not the same number, and neither is wrong when they disagree. A burst
whose components each drift by the same amount as the gaps between them will
give one answer; a burst of separately drifting sub-bursts stacked at a
different overall slope will give two. Report which one you mean.

The autocorrelation estimator is the primary result and populates
`drift_rate_mhz_per_ms`. The component estimator is reported separately through
`component_drift_rate_mhz_per_ms` and never blocks it.

## How the autocorrelation estimator works

1. Each channel's off-pulse mean is subtracted, and the event window is cut out
   of the selected band.
2. The two-dimensional autocorrelation is computed by FFT and **corrected for the
   channel mask**: each lag is divided by the fraction of valid channel pairs it
   actually had, relative to what an unmasked window would have given. With
   nothing masked this is exactly the conventional autocorrelation; with
   channels zapped it removes the notch pattern the gaps would otherwise imprint
   on the surface. The finite-window taper is deliberately left alone, because a
   burst localised inside its window is not a stationary process.
3. A rotated two-dimensional Gaussian is fitted to the central peak, over the
   lags inside `max_lag_fraction` of the full range that survive
   `min_overlap_fraction` of their possible channel pairs. The zero-lag pixel is
   excluded, because it carries the noise variance as a delta function. FLITS
   refuses a fit whose width consumes 80% or more of either fitted lag span,
   because that width is being set by the boundary rather than measured from
   the peak; increase the lag fraction or improve the selections in that case.
4. The drift rate is the **conditional-mean slope** of the fitted ellipse,
   \( \Sigma_{\nu t} / \Sigma_{tt} \) — the rate at which the centre frequency
   moves with time.

Fitting for a covariance rather than an angle is what makes the reported number
well defined. The slope of the ellipse's *major axis* is a different quantity
that depends on how you scale the two axes against each other; FLITS reports it
too, as `acf_major_axis_slope_mhz_per_ms`, because that is the convention
`frbgui` uses and comparisons need it — but it is a diagnostic, not the drift
rate. The two converge only for a very elongated ellipse.

The fitted widths also give the burst extent, after dividing out the factor
\( \sqrt{2} \) between a Gaussian and its autocorrelation:
`acf_sigma_time_ms` and `acf_sigma_freq_mhz`.

## Where the uncertainty comes from

The statistical term is a **seeded Monte Carlo**, not the fit covariance. Each
trial adds an independent noise realisation at the measured per-channel
off-pulse sigma, recomputes the autocorrelation and refits; the scaled median
absolute deviation of the refitted drift rates is the 1σ statistical error.

This is deliberate. Neighbouring autocorrelation pixels are strongly
correlated, so the covariance of a least-squares fit that treats them as
independent understates the error, sometimes by a large factor. The covariance
value is still recorded, as `drift_rate_fit_covariance_mhz_per_ms`, so the two
can be compared — but it is a diagnostic.

The Monte Carlo assumes the estimator responds to an added noise realisation the
way it responds to the noise already present, which holds while the burst
dominates the autocorrelation peak. When it does not, the `low_acf_contrast`
flag is raised.

The seed is stored in the session snapshot, so `flits replay` reproduces the
error bar exactly rather than approximately.

## Drift and DM are degenerate

This is the part that matters for anything you publish.

A dispersion-measure error tilts a burst in the time-frequency plane in exactly
the way intrinsic drift does. Over a band narrow enough for the sweep to look
linear, nothing in the dynamic spectrum distinguishes the two. A drifting burst
therefore has no single "correct" DM: maximising structure and maximising S/N
give different answers, and both are defensible.

FLITS does not resolve this. It states it, in four numbers:

| Field | Meaning |
| --- | --- |
| `dm_pc_cm3` | The DM the measurement was made at. A drift rate without this is not interpretable. |
| `acf_slope_ms_per_mhz` | The time-on-frequency slope of the fitted ellipse. This is the quantity a DM error moves linearly. |
| `dm_equivalent_pc_cm3` | The DM offset that would, on its own, produce the whole measured slope. Adding it to the applied DM flattens the burst. |
| `dm_sensitivity_mhz_per_ms_per_pc_cm3` | How much the drift rate moves per pc cm⁻³ of DM error. |

The one to read first is `dm_equivalent_pc_cm3`. **If it is smaller than your DM
uncertainty, the drift is not resolved** — the burst is equally well described
as undrifting at a slightly different DM. FLITS raises
`drift_consistent_with_dm_error` and says so in the result message when that is
the case. The component estimator reports its own
`component_dm_equivalent_pc_cm3` on the same footing.

### Why the slope and not the drift rate

A dedispersion error of \( \delta \) adds
\( g\,(\nu - \nu_\mathrm{ref}) \) to every arrival time, with

$$
g = \frac{\partial}{\partial \mathrm{DM}}
      \left(\frac{\mathrm{d}t}{\mathrm{d}\nu}\right)\delta
  = -\frac{2k}{\nu^{3}}\,\delta ,
$$

which is a shear of the burst covariance in the time-frequency plane. Under that
shear \( \Sigma_{\nu\nu} \) is untouched and \( \Sigma_{t\nu} \) picks up
\( g\,\Sigma_{\nu\nu} \), so the **time-on-frequency** regression slope

$$
\frac{\mathrm{d}t}{\mathrm{d}\nu}
  = \frac{\Sigma_{t\nu}}{\Sigma_{\nu\nu}}
  = \rho\,\frac{\sigma_t}{\sigma_\nu}
$$

simply shifts by \( g \). The drift rate is the *other* regression,
\( \Sigma_{t\nu} / \Sigma_{tt} = \rho\,\sigma_\nu/\sigma_t \), and the two
are reciprocals only when \( \rho = 1 \): in general their product is
\( \rho^{2} \). Treating \( 1/(\mathrm{d}\nu/\mathrm{d}t) \) as the
DM-linear quantity is a mistake that gets both the size and, near
\( \rho^2 = 1/2 \), the sign of the DM sensitivity wrong. FLITS therefore
converts through the slope:

$$
\delta_\mathrm{equiv} = -\frac{\nu^{3}}{2k}\,
  \frac{\mathrm{d}t}{\mathrm{d}\nu},
\qquad
\frac{\partial}{\partial \mathrm{DM}}
  \left(\frac{\mathrm{d}\nu}{\mathrm{d}t}\right)
  = -\frac{2k}{\nu^{3}}
    \left(\frac{\sigma_\nu}{\sigma_t}\right)^{2}
    \left(1 - 2\rho^{2}\right),
$$

evaluated at the mean frequency of the unmasked selected channels.

## When the drift rate is publishable

FLITS classifies this measurement the way it classifies every other one.

- **Without a DM uncertainty** the drift rate is `statistical_only` and not
  publishable, and carries the `missing_dm_uncertainty` flag. The number is
  real; what is missing is the systematic that usually dominates it.
- **With a DM uncertainty** the DM systematic is propagated through the
  sensitivity above and combined in quadrature with the statistical term. The
  result is `formal_1sigma` and publishable, unless one of the blocking flags
  is raised.

`drift_rate_status` reports significance against the bar FLITS would actually
quote, not against the statistical term alone: `ok` when the drift rate exceeds
its combined uncertainty, `unconstrained` when it does not, and `unquantified`
when there is no uncertainty at all — which is what running with the Monte Carlo
disabled and no DM uncertainty gives you.

The blocking flags are `heavily_masked` (a quarter or more of the selected
channels are gone), `low_acf_contrast`, `monte_carlo_unavailable`, and
`implicit_offpulse` (no explicit off-pulse window, so the noise reference is a
guess).

Monte Carlo requests are bounded to 512 trials in the browser, API, and replay
path so an imported snapshot cannot accidentally turn one measurement into an
unbounded computation. Non-finite controls fall back to their recorded
defaults, and random seeds are normalized to the non-negative range accepted
by NumPy.

The DM uncertainty comes from the DM sweep when the sweep still describes the
applied DM; retune the DM by hand and FLITS drops it rather than reusing a
number that now describes something else. Supply one explicitly to override.

An operator-supplied value is stored in the drift settings and travels in the
session snapshot, so `flits replay` reproduces the same classification and not
just the same number. Clear the field to go back to the sweep's own value.

## The component estimator

Each component window contributes an intensity-weighted spectral centroid and an
intensity-weighted arrival time, with uncertainties propagated from the
per-channel off-pulse noise. Centroid frequency is then regressed on arrival
time with weights \( 1/(\sigma_\nu^2 + d^2\sigma_t^2) \), iterated so the
effective variance uses the fitted slope.

With exactly two components the fit is exact: the status is
`exactly_two_components` and no \( R^2 \) is reported, because there is no
residual to explain. Overlapping components bias their centroids toward each
other, which flattens the regression; the sign survives but the magnitude is a
lower bound.

## Practical sequence

1. Dedisperse and record how. The drift rate is meaningless without the DM it
   was measured at, and a structure-maximised DM and an S/N-maximised DM will
   give different drift rates for the same burst.
2. Mask interference and set the spectral window. The autocorrelation is
   mask-corrected, but a band that is mostly gone still gives a weak fit.
3. Set the event window to the burst and the off-pulse windows to genuinely
   burst-free data — the per-channel sigma from those windows is what sets the
   error bar.
4. Mark component regions if the burst has more than one, to get the second
   estimator.
5. Run the measurement, then read `dm_equivalent_pc_cm3` before the drift rate.
6. Supply a DM uncertainty before quoting the number anywhere.

## Related

- [DM Optimization](dm-optimization.md) — where the DM and its uncertainty come
  from, and why the structure-maximising metric partly addresses the degeneracy.
- [Measurements](measurements.md) — how FLITS classifies uncertainties in
  general.
- [Model Fitting](model-fitting.md) — `fitburst` fits the scattering timescale,
  which is a different asymmetry from drift and is easy to confuse with it in a
  single component.
