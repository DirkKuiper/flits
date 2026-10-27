# Synthetic PSRFITS and CHIME workflows

This walkthrough generates three small files containing the same invented
Gaussian pulse: a folded PSRFITS profile, a dedispersed CHIME catalog waterfall
and coherently dedispersed BBData power. It demonstrates file conventions,
measurement, snapshots and replay without downloading telescope data.

Start from a source checkout with the
[development environment](developer/testing.md#full-local-environment):

```bash
python examples/format_workflows.py --output /tmp/flits-formats
```

On Windows, use a new path under your temporary directory instead of `/tmp`.
The script refuses an existing destination. It reuses the synthetic writers
in `tests/conftest.py`, so the source checkout and `pytest` are needed to
generate the inputs. Measuring the generated files uses the ordinary FLITS
runtime. Nothing is installed or downloaded by the script.

The output directory contains:

- `folded.sf`, `catalog.h5`, `power.h5`: the invented inputs.
- `results.json`: inspection metadata, measurements, selections and input SHA-256
  checksums. Each record has an `id` matching its input's stem.
- `snapshots/`: one portable session per input; each is replayed with `--check`
  before the script finishes.

## Inspect first, then choose the loading DM

The example uses the same inspection API that the browser's detection panel
calls:

```python
from flits.io import inspect_filterbank

info = inspect_filterbank("/tmp/flits-formats/power.h5")
print(info.schema_version, info.coherent_dm, info.stokes_available)
```

| Input | Loading DM | Reason |
| --- | --- | --- |
| `folded.sf` | 0 | This fixture contains an undisplaced folded profile. Its bins form a pseudo-time axis from the stored period; it is not an original stream of telescope arrival times. |
| `catalog.h5` | 0 | Catalog waterfalls are already dedispersed. Applying the astrophysical DM again would disperse them. |
| `power.h5` | 50 | The header records a coherent DM of 50 pc cm⁻³. FLITS aligns the per-channel time origins and applies only residual dispersion relative to that value. |

The catalog fixture needs a date-shaped `tns_name` for the reader's time axis.
Its label is synthetic and is not evidence of an observed event. Raw complex
BBData voltages are outside this example; `power.h5` stores beamformed power.
It contains two power streams, which do not establish full Stokes I/Q/U/V.

## Measure and review

For example, measure the catalog waterfall:

```bash
python -m flits measure /tmp/flits-formats/catalog.h5 --dm 0 \
  --telescope generic --sefd 10 --time-factor 1 --freq-factor 1 \
  --snapshot-dir /tmp/flits-formats/catalog-review
```

Masking and localization are enabled. The source script applies the same
settings to all three files, changing only the loading DM. `--telescope generic`
and the resolution factors keep the exercise independent of instrument-preset
defaults; use the correct instrument settings for actual observations.

The injected profile peaks at 128 ms, has Gaussian sigma 2.5 ms, and therefore
FWHM about 5.89 ms. The test requires each automatic event window to contain
128 ms and its ACF FWHM to be within 0.4 ms of 5.89 ms. The folded file stores
rounded integer samples while the HDF5 files store floating-point data; their
measurements need not be identical.

The current FLITS catalog reader normalizes `calibrated_wfall`; its subsequent
flux and fluence use SEFD-based radiometer calibration. This example therefore
does not demonstrate preservation of a real catalog's original Jy calibration.
Use the original calibrated data and its documented calibration workflow when
that scale is required; see [Supported Formats](user-guide/supported-formats.md#chimefrb-public-catalog-hdf5).

The assumed SEFD of 10 Jy is a teaching input, not a calibration of CHIME or
another telescope. No SEFD uncertainty is supplied, so flux and fluence errors
are classified `statistical_only`. BBData's power combination and format
normalization can also change scaling; compare the pulse's recovered width
and position rather than treating this fixture as an absolute flux standard.

Inspect each record's `status`, `localization.warning_flags`,
`measurements.measurement_flags`, and uncertainty details. Real data can carry
interference, time offsets, calibration systematics and ambiguous components
absent from this simple signal. [Scientific Validation](validation.md) describes
what the tests establish and where they stop.

## Open or replay a saved selection

```bash
python -m flits --data-dir /tmp/flits-formats
python -m flits replay /tmp/flits-formats/snapshots/power_flits_session.json \
  --data-dir /tmp/flits-formats --check
```

In the browser, **Session → Saved Sessions** lists the three snapshots under
the data directory. Opening one restores its recorded selections; replay
recomputes its supported analyses from the corresponding input file. Preserve
the original inputs and checksums alongside the sessions.
