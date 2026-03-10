# FLITS

Fast-Look Interactive Transient Suite.

Browser-based scientific software for interactive burst inspection, masking, and measurement on filterbank data.

Known presets can supply a default SEFD when the observing setup is identifiable from the file metadata and band coverage. Today that means:

- `NRT` uses its preset SEFD
- `GBT` auto-selects a default SEFD for common Gregorian bands (for example L-band files default to 10 Jy)

For data without a known default calibration, use the `Generic Filterbank` preset or provide an explicit `SEFD` override if you want calibrated flux and fluence values.

## Install With Python

Install from a local checkout:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .
```

You can also install directly from GitHub:

```bash
pip install "git+https://github.com/DirkKuiper/flits.git"
```

Then run FLITS against a directory of filterbanks:

```bash
flits --data-dir /path/to/filterbanks --host 127.0.0.1 --port 8123
```

Open `http://127.0.0.1:8123`.

Notes:

- Relative file paths in the UI are resolved against `FLITS_DATA_DIR` when set, otherwise against the current working directory.
- The `--data-dir` flag is the easiest way to point FLITS at a specific directory without exporting environment variables.
- The known-filterbanks dropdown lists `.fil` files recursively under that data directory.

## Run With Docker

The canonical container image is intended to live at `ghcr.io/dirkkuiper/flits`.

If you have a published image available, run:

```bash
docker run --rm -p 8123:8123 \
  -e FLITS_DATA_DIR=/data \
  -v /path/to/filterbanks:/data \
  ghcr.io/dirkkuiper/flits:latest
```

If you want to build locally instead:

```bash
docker build -t flits .
docker run --rm -p 8123:8123 \
  -e FLITS_DATA_DIR=/data \
  -v /path/to/filterbanks:/data \
  flits
```

Then open `http://127.0.0.1:8123`.

Notes:

- The container defaults `FLITS_DATA_DIR` to `/data`.
- Relative file paths in the UI are resolved against `FLITS_DATA_DIR`.
- The known-filterbanks dropdown lists `.fil` files recursively under `FLITS_DATA_DIR`.
- Absolute paths inside the container still work if you prefer to type them manually.

## Run With Apptainer

Apptainer users should consume the same OCI image rather than maintain a separate container recipe.

If the published GHCR image is available:

```bash
apptainer pull flits.sif docker://ghcr.io/dirkkuiper/flits:latest
APPTAINERENV_FLITS_DATA_DIR=/data \
  apptainer exec --bind /path/to/filterbanks:/data flits.sif \
  python -m flits --host 127.0.0.1 --port 8123
```

If you are on an HPC system where pulling from GHCR is inconvenient, you can still build or export the Docker image elsewhere and convert it to `.sif` with Apptainer. The SPIDER-specific version of that workflow is documented in [docs/spider.md](/Users/dirk/Desktop/PhD/Code/analyzer/docs/spider.md).

## Run With Docker Compose

If your filterbanks are in the current directory:

```bash
docker compose up --build
```

If your filterbanks live somewhere else, point `DATA_DIR` at that directory:

```bash
DATA_DIR=/absolute/path/to/filterbanks docker compose up --build
```

You can also choose a different host port:

```bash
DATA_DIR=/absolute/path/to/filterbanks FLITS_PORT=9000 docker compose up --build
```

Then open `http://127.0.0.1:8123` or `http://127.0.0.1:9000` respectively.

## Remote And HPC Use

FLITS is portable across local workstations, remote servers, and HPC systems:

- On a normal workstation, `pip install .`, `docker run`, or `docker compose` are all fine.
- On a remote Linux machine you access over SSH, start FLITS remotely and forward the port:

```bash
ssh -L 8123:127.0.0.1:8123 user@remote-host
```

Then open `http://127.0.0.1:8123` locally in your browser.

- On many HPC clusters, Docker itself is not permitted. Apptainer is usually the right runtime there.
- The SPIDER-specific workflow, helper scripts, and Slurm examples live in [docs/spider.md](/Users/dirk/Desktop/PhD/Code/analyzer/docs/spider.md).
- The GitHub Actions workflow in [.github/workflows/publish-image.yml](/Users/dirk/Desktop/PhD/Code/analyzer/.github/workflows/publish-image.yml) is the intended way to publish the canonical OCI image to GHCR.

## Code Structure

- `flits/settings.py`: observation presets and explicit overrides
- `flits/io/filterbank.py`: filterbank loading and Stokes-I extraction
- `flits/signal.py`: shared numerical utilities
- `flits/models.py`: typed metadata and measurement containers
- `flits/session.py`: interactive burst state and measurements
- `flits/web/app.py`: FastAPI server for the browser UI
- `flits/web_static/`: packaged frontend assets served by the app
- `tests/test_session_smoke.py`: smoke tests on a real local filterbank file

## Measurements

- Fluence (Jy ms), when an SEFD is provided
- Peak flux density (Jy), when an SEFD is provided
- Event duration (ms)
- Spectral extent (MHz)
- Peak MJD
- 1D Gaussian fits to selected burst regions

Flux and fluence are computed from the burst-only time series using the radiometer equation with the effective unmasked bandwidth inside the selected spectral extent.
