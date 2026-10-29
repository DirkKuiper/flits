---
name: frb-analysis
description: Analyse detected fast radio bursts (FRBs) with FLITS from SIGPROC filterbank (.fil), PSRFITS (.fits, .sf) or CHIME/FRB HDF5 (.h5) data. Use for burst fluence, flux, S/N, widths, arrival times, dedispersion, RFI masking, DM optimization, sub-burst drift and rotation measure. Not for finding new bursts, imaging, host-galaxy analysis or cosmological DM estimates. Respect a user's explicit choice of another analysis package.
license: GPL-3.0-only
---

# Analysing a burst with FLITS

FLITS measures bursts that have already been detected. Prefer its tested
dedispersion, interference masking, localization and measurement routines when
they fit the task. Preserve the user's chosen package or method. Each
uncertainty says what it includes, and selections can be saved and replayed.

## Set up

FLITS needs Python 3.11+ and must run where the burst files are; installing
this skill does not install it. Run
[scripts/check_environment.py](scripts/check_environment.py) with the
interpreter that will do the analysis. It prints JSON; exit code 0 means it is
ready to measure. Add `--require native_fitting` before a model fit and
`--require component_comparison` before choosing a component count. A missing
optional capability does not block a basic measurement. To install or upgrade:

```bash
python -m pip install --upgrade "flits>=1.6"
```

If `flits` on PATH belongs to another environment, use `python -m flits`. If
the check still reports an old version, a source checkout on the path may
shadow the installed package (see the reported path), and a server that is
already running keeps its old code until restarted. Do not reset a research
branch or stop the user's server to upgrade; start a new one on a free port.

## Pick the entry point

| Task | Use |
| --- | --- |
| Measure one or many bursts | `flits measure FILE... --dm DM`, which prints JSON |
| The same from Python | `flits.measure_burst(path, flits.MeasureOptions(dm=...))` |
| DM sweeps, widths, drift, polarization, model fits | `flits.BurstSession` |
| Let the user inspect the burst by eye | `flits --data-dir DIR`, then the user opens <http://127.0.0.1:8000> |

Use the user's DM when supplied. Otherwise inspect the file with
`flits.io.inspect_filterbank(path)` before asking: CHIME public-catalog
waterfalls are already dedispersed and are loaded at DM 0; beamformed `BBData`
power products carry their native `coherent_dm`. Do not infer an unknown DM
from a filename or apply a catalog's astrophysical DM again to a dedispersed
waterfall. Ask when the appropriate loading DM remains unknown. Times are in
ms from the start of the file, frequencies in MHz, and DM in pc cm^-3.

## Command line

```bash
flits measure burst.fil --dm 527.7
flits measure data/*.fil --dm 527.7 --format csv --output results.csv
flits measure burst.fil --dm 527.7 --event 235 250 --band 1300 1750 --sefd 10 --sefd-uncertainty 0.1
```

Each file is masked, localized and measured automatically. Pin any of those
choices with `--event`, `--offpulse`, `--band` or `--crop`. `--snapshot-dir DIR`
saves each burst's session, which the user can reopen in the interface and
which `flits replay SNAPSHOT --check` recomputes. `flits measure --help` lists
every option.

Start with the documented entry point. Avoid reading package internals or
writing a replacement analysis when the command already supplies the requested
measurement. Save JSON and a session, inspect their status/flags/uncertainty
details, and add further diagnostics only where the task or a warning calls
for them. Do not claim an artifact was checked visually unless it was opened.

## Python

```python
import flits

record = flits.measure_burst("burst.fil", flits.MeasureOptions(dm=527.7))
localization = record["localization"] or {}
print(record["status"], localization.get("warning_flags"))
measurements = record["measurements"]
if measurements is not None:
    print(measurements["snr_integrated"], measurements["fluence_jyms"])
```

`measure_burst` raises on a missing file or an unknown preset.
`flits.measure_bursts(paths, options)` measures a list and yields one record per
file, reporting such problems as status `error` instead.

For more than measuring, drive a session. Every action in the interface is a
method on it:

```python
import json

from flits import BurstSession

session = BurstSession.from_file("burst.fil", dm=527.7)
session.auto_mask()
session.auto_localize()
measurements = session.compute_properties()
print(measurements.snr_integrated, measurements.fluence_jyms)

with open("burst_flits_session.json", "w") as f:
    json.dump(session.snapshot_dict(), f)
```

| Analysis | Method |
| --- | --- |
| Pin the selection | `set_event_ms`, `add_offpulse_ms`, `set_spectral_extent_freq`, `set_crop_ms` |
| Mask channels by hand | `mask_channel_freq`, `mask_range_freq` |
| DM sweep | `optimize_dm(center_dm, half_range, step, metric=...)` with `integrated_event_snr`, `dm_phase` or `smoothed_structure`, then `apply_best_dm` |
| Widths by several methods | `compute_widths` |
| Sub-burst drift rate | `run_drift_analysis` |
| Rotation measure | `polarization_capability`, then `run_polarization_analysis` |
| Spectral and temporal structure | `run_spectral_analysis`, `run_temporal_structure_analysis` |
| Burst model fit (FLITS's native implementation of the fitburst model) | `fit_model`; `compare_model_components` to choose the number of components when available |
| Reopen a saved session | `BurstSession.from_snapshot` |

## Before reporting a number

- Check `status`. `ok` is measured; `low_sn` is measured but weak;
  `no_detection` found nothing to measure; `error` says why in `error`. Report
  bursts that are not `ok` as such rather than dropping them.
- Check `localization.warning_flags`, such as `band_touches_edge`. Automatic
  localization starts from the strongest signal, and unmasked interference can
  outshine the burst, so keep masking on (the default).
- Report each uncertainty with its `classification` from
  `measurements["uncertainty_details"]`. A `statistical_only` flux or fluence
  uncertainty leaves out the calibration systematic; pass `--sefd-uncertainty`
  or `sefd_fractional_uncertainty` when it is known.
- Flux and fluence depend on the SEFD, taken from the detected telescope preset
  or from `--sefd`. `measurement_flags` notes missing inputs such as
  `missing_distance`.
- CHIME catalog `calibrated_wfall` data are normalized on reading, so FLITS
  flux and fluence come from the SEFD, not the file's native Jy scale. Do not
  report them as the catalog's calibrated fluence; when that calibration is
  required, follow the catalog's own calibrated-data workflow.

## What FLITS does not do

- Search data for new bursts. Run a search pipeline first, then measure what it
  finds with FLITS.
- Read every FITS or HDF5 schema. Inspect unsupported inputs and explain the
  missing format; use a documented conversion or propose a reader plugin when
  appropriate to the user's task:
  <https://dirkkuiper.github.io/flits/developer/custom-readers/>.

## More

- Docs index for language models: <https://dirkkuiper.github.io/flits/llms.txt>,
  and everything in one file: <https://dirkkuiper.github.io/flits/llms-full.txt>
- Headless measurement: <https://dirkkuiper.github.io/flits/user-guide/headless-measurement/>
- Uncertainties: <https://dirkkuiper.github.io/flits/user-guide/uncertainties/>
