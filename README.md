# FLITS

[![Docs](https://img.shields.io/badge/docs-online-0A66C2.svg)](https://dirkkuiper.github.io/flits/)
[![PyPI](https://img.shields.io/pypi/v/flits.svg)](https://pypi.org/project/flits/)
[![Python](https://img.shields.io/pypi/pyversions/flits.svg)](https://pypi.org/project/flits/)
[![Tests](https://github.com/DirkKuiper/flits/actions/workflows/tests.yml/badge.svg)](https://github.com/DirkKuiper/flits/actions/workflows/tests.yml)
[![Install](https://img.shields.io/badge/pip%20install-flits-3775A9?logo=pypi&logoColor=white)](https://pypi.org/project/flits/)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](https://github.com/DirkKuiper/flits/blob/main/LICENSE)

Fast-Look Interactive Transient Suite.

FLITS is browser-based FRB analysis software for interactive filterbank
inspection, masking, measurement, DM optimization, temporal/spectral
diagnostics, and export.

## Quick Start

Install the published package:

```bash
pip install flits
flits --data-dir /path/to/filterbanks --host 127.0.0.1 --port 8123
```

Then open `http://127.0.0.1:8123`.

Optional scattering fits use `fitburst`, which is intentionally left out of the
PyPI dependency metadata because package indexes reject direct URL runtime
dependencies. To enable the fitburst-backed fitting workflow after installing
FLITS:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## Highlights

- Browser-based workflow for burst inspection on filterbank data.
- Interactive crop, event, off-pulse, spectral-window, and masking controls.
- Calibrated fluence and peak-flux outputs when an SEFD is available.
- DM optimization using integrated-event S/N and DMphase.
- Temporal-structure, PSD, ACF, and optional fitburst-backed scattering tools.
- Export bundles and JSON session snapshots for reproducible analysis.

## Documentation

- Full docs: [dirkkuiper.github.io/flits](https://dirkkuiper.github.io/flits/)
- Getting started: [Quickstart](https://dirkkuiper.github.io/flits/getting-started/)
- Installation and deployment: [Installation](https://dirkkuiper.github.io/flits/installation/)
- Developer testing: [Testing](https://dirkkuiper.github.io/flits/developer/testing/)
- Developer publishing: [Publishing](https://dirkkuiper.github.io/flits/developer/publishing/)

The docs cover Python installs, Docker, Apptainer, remote/HPC use, interactive
workflow guidance, measurements, DM optimization, temporal/spectral analysis,
exports, and release procedures.

## Citation

If you use FLITS in research, cite the software and link to the repository:

- PyPI package: `flits`
- Repository: `https://github.com/DirkKuiper/flits`
- Citation metadata: [CITATION.cff](./CITATION.cff)

## License

FLITS is released under the GNU GPLv3. See [LICENSE](./LICENSE).
