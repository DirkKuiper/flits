# FLITS

Fast-Look Interactive Transient Suite.

Browser-based scientific software for interactive burst inspection, masking, and measurement on filterbank data.

## Run Locally

```bash
.venv_test/bin/python -m flits --host 127.0.0.1 --port 8123
```

Then open `http://127.0.0.1:8123`.

Known presets can supply a default SEFD when the observing setup is identifiable from the file metadata and band coverage. Today that means:

- `NRT` uses its preset SEFD
- `GBT` auto-selects a default SEFD for common Gregorian bands (for example L-band files default to 10 Jy)

For data without a known default calibration, use the `Generic Filterbank` preset or provide an explicit `SEFD` override if you want calibrated flux and fluence values.

## Run With Docker

Build the image:

```bash
docker build -t flits .
```

Run it with a directory of filterbanks mounted into the container:

```bash
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

This setup is portable across machines that have a compatible container runtime and can run the Python dependencies for the target CPU architecture. In practice:

- On a normal workstation or server with Docker, `docker run` or `docker compose` is enough.
- On a remote Linux machine you access over SSH, start the container remotely and forward the port:

```bash
ssh -L 8123:127.0.0.1:8123 user@remote-host
```

Then open `http://127.0.0.1:8123` locally in your browser.

- On many HPC clusters, Docker itself is not permitted because it requires a daemon with elevated privileges.
- If the cluster provides Apptainer/Singularity or rootless Podman, the Docker image is still useful as the container source, but you will usually run it through the cluster-supported runtime instead of `docker`.
- If the cluster does not allow any container runtime, you will need a standard Python environment there instead.

## Code Structure

- `flits/settings.py`: observation presets and explicit overrides
- `flits/io/filterbank.py`: filterbank loading and Stokes-I extraction
- `flits/signal.py`: shared numerical utilities
- `flits/models.py`: typed metadata and measurement containers
- `flits/session.py`: interactive burst state and measurements
- `flits/web/app.py`: FastAPI server for the browser UI
- `tests/test_session_smoke.py`: smoke tests on a real local filterbank file

## Measurements

- Fluence (Jy ms), when an SEFD is provided
- Peak flux density (Jy), when an SEFD is provided
- Event duration (ms)
- Spectral extent (MHz)
- Peak MJD
- 1D Gaussian fits to selected burst regions

Flux and fluence are computed from the burst-only time series using the radiometer equation with the effective unmasked bandwidth inside the selected spectral extent.
