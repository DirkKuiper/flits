# FLITS

[![Docs](https://img.shields.io/badge/docs-online-0A66C2.svg)](https://dirkkuiper.github.io/flits/)
[![PyPI](https://img.shields.io/pypi/v/flits.svg)](https://pypi.org/project/flits/)
[![Python](https://img.shields.io/pypi/pyversions/flits.svg)](https://pypi.org/project/flits/)
[![Tests](https://github.com/DirkKuiper/flits/actions/workflows/tests.yml/badge.svg)](https://github.com/DirkKuiper/flits/actions/workflows/tests.yml)
[![Install](https://img.shields.io/badge/pip%20install-flits-3775A9?logo=pypi&logoColor=white)](https://pypi.org/project/flits/)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](https://github.com/DirkKuiper/flits/blob/main/LICENSE)

Fast-Look Interactive Transient Suite.

FLITS is browser-based FRB analysis software for interactive burst
inspection, masking, measurement, DM optimization, temporal/spectral
diagnostics, and export. It reads SIGPROC filterbank (`.fil`),
PSRFITS search/fold data (`.fits`, `.sf`), and CHIME/FRB HDF5 (`.h5`, `.hdf5`)
including public catalog waterfalls and beamformed `BBData`
`tiedbeam_power` files. The I/O layer is pluggable — third parties can
register custom formats via `importlib` entry points without forking.

Every interactive decision is recorded in a JSON session snapshot, and
`flits replay` re-runs that snapshot headlessly to reproduce the exported
measurements without a browser.

## Why FLITS exists

Turning a detected burst into a number you can defend takes several steps —
tune the DM, mask interference, place the event and off-pulse windows, choose a
spectral extent, then measure — and every one of them is a judgement call that
moves the answer.

The tools that do these steps well do them separately. Search pipelines stop at
detection; format libraries read data without offering a workflow; and the
strong single-purpose tools each expect their own input conventions. So a burst
campaign accumulates per-instrument glue scripts, and the decisions that
produced the numbers survive only in someone's notebook.

That gets harder for repeating sources, where the same population is observed by
many telescopes. FLITS was built for a repeater campaign spanning GBT at L and P
band, Nançay, Westerbork, Onsala, Stockert and CHIME — three file formats,
sampling times differing by more than an order of magnitude, and instruments
with different bandwidths, polarization conventions and SEFDs.

FLITS makes the session, rather than the script, the unit of analysis. One
interface covers every supported instrument, the reader layer absorbs the format
differences, telescope presets carry the instrument-specific calibration, and
the snapshot makes the analysis inspectable and replayable afterwards.
Measurements state their own uncertainty basis: FLITS separates a formal 1-sigma
uncertainty from a statistical-only one, and will not mark a fluence publishable
when the SEFD systematic it needs was never supplied.

FLITS is not a search pipeline — it starts from a burst you already have.

## Quick Start

Install the published package:

```bash
pip install flits
flits --data-dir /path/to/filterbanks --host 127.0.0.1 --port 8123
```

Then open `http://127.0.0.1:8123`.

Optional model fitting uses `fitburst`, which is intentionally left out of the
PyPI dependency metadata because package indexes reject direct URL runtime
dependencies. To enable model fitting after installing
FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## Highlights

- Browser-based workflow for burst inspection on SIGPROC, PSRFITS, and CHIME/FRB HDF5 data.
- CHIME support includes public catalog waterfalls and beamformed `BBData` power products.
- Interactive crop, event, off-pulse, spectral-window, and masking controls.
- Automatic burst localization in time and frequency (matched-filter event window, spectral extent, and off-pulse placement in one click).
- Calibrated fluence and peak-flux outputs when an SEFD is available.
- DM optimization using integrated-event S/N and DMphase.
- Temporal-structure, PSD, ACF, and optional selected-event model fitting.
- Weighted Q/U RM synthesis with optional RM-CLEAN, significance and quality diagnostics, and JSON/CSV products (operates on an imported, calibrated Q/U spectrum — FLITS readers deliver Stokes I).
- Export bundles and JSON session snapshots for reproducible analysis.

## Documentation

- Full docs: [dirkkuiper.github.io/flits](https://dirkkuiper.github.io/flits/)
- Getting started: [Quickstart](https://dirkkuiper.github.io/flits/getting-started/)
- Guided example: [GBT Burst Workflow](https://dirkkuiper.github.io/flits/guided-workflow/)
- Installation and deployment: [Installation](https://dirkkuiper.github.io/flits/installation/)
- Developer testing: [Testing](https://dirkkuiper.github.io/flits/developer/testing/)
- Developer publishing: [Publishing](https://dirkkuiper.github.io/flits/developer/publishing/)

The docs cover Python installs, Docker, Apptainer, remote/HPC use, interactive
workflow guidance, measurements, DM optimization, temporal/spectral analysis,
exports, and release procedures.

## Release Channels

- PyPI and `ghcr.io/dirkkuiper/flits:latest` track stable releases.
- `ghcr.io/dirkkuiper/flits:<version>` pins an exact release.
- `ghcr.io/dirkkuiper/flits:edge` tracks the current `main` branch for snapshot testing.

## Citation

If you use FLITS in research, cite the software and link to the repository:

- PyPI package: `flits`
- Repository: `https://github.com/DirkKuiper/flits`
- Citation metadata: [CITATION.cff](./CITATION.cff)

## License

FLITS is released under the GNU GPLv3. See [LICENSE](./LICENSE).

The interface bundles [plotly.js](https://github.com/plotly/plotly.js) (MIT) so
it works without fetching anything from a CDN; its licence travels with the copy
in [`flits/web_static/vendor/`](./flits/web_static/vendor/).
