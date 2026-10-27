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

Use Python 3.11+ and FLITS 1.6+ in an environment that can access the burst files.

Run [scripts/check_environment.py](scripts/check_environment.py) with the Python
interpreter that will analyse the data. It reports the interpreter, FLITS
version and location, and missing capabilities as JSON. Exit code 0 means it
is ready. If setup is needed, select a Python 3.11+ environment and run:

```bash
python -m pip install --upgrade "flits>=1.6"
```

Run the check again after installation. Use `python -m flits` in place of
`flits` if the command on PATH belongs to a different environment. Installing
this skill does not install FLITS or give a remote agent access to local files.

If an old version is still imported after upgrading, check the reported module
path. A local source checkout can shadow the installed package; `pip` does not
update that checkout. Use an appropriate current checkout or run the installed
package from outside it. Do not switch or reset a research branch just to
upgrade an analysis environment.

Before reusing a browser server, verify its runtime as well: a process already
running can still use older code after a package upgrade. A saved fit records
its own fitter and software provenance, which may differ from the current
server. Check the provenance of a newly computed fit when validating an upgrade.

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
| Burst model fit (the fitburst model) | `fit_model`; `compare_model_components` to choose the number of components |
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
