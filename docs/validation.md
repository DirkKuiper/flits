# Scientific validation and tested limits

FLITS uses numerical comparisons, injected signals, file-format fixtures and
reproducible workflows to check its calculations. These checks answer different
questions. Agreement with another implementation checks the calculation;
replaying a saved session checks reproducibility. Neither establishes an
instrument's calibration or that an automatic selection found the intended FRB.

## Independent implementation comparisons

| Calculation | Evidence and acceptance criterion | Scope |
| --- | --- | --- |
| Native burst model | [`test_fitburst_parity.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_fitburst_parity.py) compares against the pinned upstream fitburst implementation. Model arrays agree to an absolute tolerance of `1e-5 × max(abs(reference))`; the common region after fitburst's exponential clamp agrees to relative `1e-10`. | One or multiple components, dispersion, scattering, scintillation, folded profiles and time/frequency upsampling. The clamp comparison documents a deliberate difference in the extreme tail. |
| Least-squares fitting | The same test compares fit minima within `0.01` reported standard deviations, covariance-derived uncertainties within relative `1e-2`, and final chi-square/S/N within relative `1e-6`. | The seeded synthetic cases and matching parameter/weight conventions in that test. It is not a global-optimum guarantee for arbitrary bursts. |
| DM_phase metric | [`test_dm_optimization.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_dm_optimization.py) compares selected DMs with upstream DM_phase. The ordinary cases allow one 0.5 pc cm⁻³ grid step; the reduced-resolution case allows 1.5 pc cm⁻³. | Single and complex synthetic bursts, masking and downsampling. The comparison skips when upstream DM_phase is unavailable. Integer-bin dedispersion and grid resolution limit the result. |
| RM synthesis | The [RM-Tools worked comparison](analysis/rm-synthesis.md) uses its official synthetic spectrum and reference values at a recorded upstream revision. The checked-in example differs by 0.00173 rad m⁻² in RM, `4.42e-6` in peak amplitude and `7.54e-4` in peak S/N. | 288 channels spanning 800–1088 MHz; the test requires RM agreement within 0.5 rad m⁻² and peak S/N within 0.1. The RM-Tools and FLITS RM uncertainties and RMSF widths differ; peak agreement does not establish identical uncertainty estimators or CLEAN behavior. |
| Barycentric timing | [`test_timing.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_timing.py) compares the correction with a direct Astropy calculation within `1e-12` MJD. | Synthetic timing inputs with specified source position, observatory and time scale. Missing metadata blocks the unsupported correction. This does not calibrate an observatory clock or establish an upstream file's absolute reference. |

The upstream fitburst revision is recorded in `requirements-dev.txt`.
RM-Tools input/reference checksums and the revision are in
[`rmtools-reference-summary.json`](https://github.com/DirkKuiper/flits/blob/main/docs/examples/rmtools-reference-summary.json).
The suite does not silently replace missing reference implementations with a
claim of parity; inspect skipped-test reasons when assessing a run.

## Injected signals and format checks

| Area | What is checked |
| --- | --- |
| Flux and fluence | [`test_measurements.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_measurements.py) checks radiometer calibration, bandwidth/polarization factors, masking and uncertainty terms. These tests validate the supplied calibration inputs' propagation, not their observational accuracy. |
| Widths and scattering | [`test_model_fitting.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_model_fitting.py) recovers a synthetic 1.2 ms intrinsic width within 0.25 ms and a 2.4 ms scattering time within 0.35 ms; the injected values also lie within four reported standard deviations. [`test_width_analysis.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_width_analysis.py) checks width definitions and the Gaussian sigma/FWHM conversion. |
| Drift | [`test_drift_analysis.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_drift_analysis.py) recovers noiseless injected rates of −12, −3, 0, 5 and 15 MHz ms⁻¹ to three decimal places. It also tests frequency order, masks, noise, DM sensitivity, component regression and refusal of insufficient/unusable selections. |
| Polarization | [`test_polarization_workflow.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_polarization_workflow.py) recovers an injected RM from channelized Stokes data and refuses an unknown calibration/basis where interpretation requires it. Export and replay tests preserve the analysis. |
| Readers | [`test_io_readers.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_io_readers.py) checks metadata and pulse recovery in SIGPROC, search/fold PSRFITS and supported CHIME HDF5 schemas, including coherent-DM conventions. It rejects unrelated FITS/HDF5 containers, missing metadata and corrupt inputs. See [Supported Formats](user-guide/supported-formats.md) for actual dialect support. |
| Historical sessions | [`test_historical_compatibility.py`](https://github.com/DirkKuiper/flits/blob/main/tests/test_historical_compatibility.py) loads recorded snapshots from earlier releases and checks their behavior. This is format compatibility, separately from numerical equality after an algorithm change. |

The [synthetic multi-format walkthrough](format-workflows.md) creates folded
PSRFITS, a dedispersed CHIME catalog waterfall and beamformed BBData power.
It inspects each format, uses the correct loading DM, measures, saves a session
and checks replay. Its test asserts on the known pulse's position and width.

## Real-data regression workflow

The [public GBT tutorial](guided-workflow.md) has a downloadable, checksum-fixed
input, explicit selections and checked-in numerical outputs. Run:

```bash
python examples/gbt_reference_workflow.py --source /path/to/flits-tutorial-gbt-frb20240114a-v1.fil
flits replay docs/examples/gbt-reference-session.json --data-dir /path/to --check
```

The separate [reference workflow](https://github.com/DirkKuiper/flits/actions/workflows/reference-workflows.yml)
tests the pinned published package and the current checkout against those
measurements and the residual-DM sweep. The numerical comparison uses relative
tolerance `1e-6` and absolute tolerance `1e-8` for the fields selected by the
script. The reference is a regression target produced by FLITS; it is not an
independent astrophysical ground truth. Its SEFD is an assumed preset value,
the flux errors omit an unknown calibration systematic, and the example does
not validate absolute arrival times.

Some tests marked `realdata` use local observatory files and skip when they are
absent. Their presence in the repository does not mean every CI run exercised
them. Check the selected tests, skips, environment and data availability when
citing a validation result.

## What to check for a new analysis

Public CHIME catalog `calibrated_wfall` inputs have an additional limit: the
current reader normalizes their amplitudes, and FLITS subsequently derives
flux and fluence from the configured SEFD. It does not preserve and directly
integrate the input's absolute Jy scale. A successful reader or replay test
does not validate agreement with the catalog's native flux calibration. See
[Supported Formats](user-guide/supported-formats.md#chimefrb-public-catalog-hdf5).

- Review the selected event, off-pulse windows, band and mask. Automatic
  localization follows the strongest usable signal and can select interference.
- Keep `low_sn`, `no_detection`, failed fits and warning flags in batch results.
  A solver's successful termination alone does not identify a physically unique
  or well-constrained solution.
- Give each uncertainty together with its classification and included terms.
  `statistical_only` omits a missing systematic; `resolution_limit` is not a
  statistical standard deviation. See [Uncertainties](user-guide/uncertainties.md).
- Treat intrinsic width, scattering, residual DM and frequency drift as coupled
  quantities. Compare resolution, parameter bounds and the selected model
  before interpreting them. See [Model Fitting](analysis/model-fitting.md) and
  [Sub-Burst Drift](analysis/drift.md).
- Establish the polarization basis, calibration status and absolute timing
  metadata from the data provenance. A file extension or successful read is
  insufficient evidence for those conventions.

The documented tests cover particular inputs and regimes. They do not claim
universal file support, observational calibration, population-level uncertainty
coverage, or automatic scientific interpretation of every burst.
