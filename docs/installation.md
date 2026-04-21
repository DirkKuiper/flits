# Installation and Deployment

FLITS can be used from a local Python install, a Git checkout, Docker,
Apptainer, or a remote/HPC deployment.

## Python package

Install the published package:

```bash
pip install flits
```

Run it against a directory of filterbanks:

```bash
flits --data-dir /path/to/filterbanks --host 127.0.0.1 --port 8123
```

## Optional fitburst support

`fitburst` is intentionally not included in the PyPI runtime metadata because
public package indexes do not accept direct URL runtime dependencies. If you
want FLITS's optional scattering-fit workflow:

```bash
pip install "fitburst @ https://github.com/CHIMEFRB/fitburst/archive/3c76da8f9e3ec7bc21951ce1b4a26a0255096b69.tar.gz"
```

## Local checkout

If you are developing locally or want to run from a source checkout:

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

## Docker

The canonical image is intended to live at `ghcr.io/dirkkuiper/flits`.

Recommended tags:

- `latest` for the newest stable release
- an exact tag such as `0.2.0` when you want a reproducible deployment
- `edge` for snapshots built from `main`

If an image is already published:

```bash
docker run --rm -p 8123:8123 \
  -e FLITS_DATA_DIR=/data \
  -v /path/to/filterbanks:/data \
  ghcr.io/dirkkuiper/flits:latest
```

To pin a specific stable release instead:

```bash
docker run --rm -p 8123:8123 \
  -e FLITS_DATA_DIR=/data \
  -v /path/to/filterbanks:/data \
  ghcr.io/dirkkuiper/flits:0.1.1
```

To build locally instead:

```bash
docker build -t flits .
docker run --rm -p 8123:8123 \
  -e FLITS_DATA_DIR=/data \
  -v /path/to/filterbanks:/data \
  flits
```

## Docker Compose

If your filterbanks are in the current directory:

```bash
docker compose up --build
```

If they are elsewhere:

```bash
DATA_DIR=/absolute/path/to/filterbanks docker compose up --build
```

To use a different host port:

```bash
DATA_DIR=/absolute/path/to/filterbanks FLITS_PORT=9000 docker compose up --build
```

## Apptainer

Apptainer users should normally consume the OCI image rather than maintain a
separate recipe:

```bash
apptainer pull flits.sif docker://ghcr.io/dirkkuiper/flits:latest
APPTAINERENV_FLITS_DATA_DIR=/data \
  apptainer exec --bind /path/to/filterbanks:/data flits.sif \
  flits --data-dir /data --host 127.0.0.1 --port 8123
```

For a reproducible Apptainer build, prefer an exact container tag over
`latest`.

## Remote and HPC use

FLITS works well on remote Linux systems where you forward a local browser port:

```bash
ssh -L 8123:127.0.0.1:8123 user@remote-host
```

Then start FLITS on the remote host and open `http://127.0.0.1:8123` locally.

On clusters where Docker is unavailable, Apptainer is usually the right runtime.

## Data-directory behavior

- Relative paths in the UI are resolved against `FLITS_DATA_DIR` when it is set.
- Otherwise, relative paths are resolved against the current working directory.
- The file browser lists every recognized input file recursively under
  that base directory. Extensions owned by any registered reader are
  picked up automatically — out of the box this covers `.fil` (SIGPROC),
  `.fits` / `.sf` (PSRFITS), and `.h5` / `.hdf5` (CHIME/FRB HDF5). See
  [Supported Formats](user-guide/supported-formats.md) for the full
  matrix and [Custom Readers](developer/custom-readers.md) for adding
  new formats via entry points.
