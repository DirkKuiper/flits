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

### Windows virtual environment

For an isolated install on Windows, call the virtual environment's Python
directly from PowerShell; activation is optional:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install flits
.\.venv\Scripts\python.exe -m flits --data-dir "C:\data\bursts"
```

On Linux or macOS, the equivalent interpreter is `.venv/bin/python`.
Using the environment's Python with `-m flits` also ensures that the command
uses the same installation as your scripts.

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

## Installation checks

The `wheel-platforms` CI job builds and installs a wheel with the package's
runtime dependency ranges, then runs outside the source checkout. It uses
Python 3.11 and 3.14 on these hosted runners:

| Platform | Runner |
| --- | --- |
| Linux x86-64 | `ubuntu-latest` |
| Linux ARM64 | `ubuntu-24.04-arm` |
| macOS ARM64 | `macos-latest` |
| macOS Intel | `macos-15-intel` |
| Windows x86-64 | `windows-latest` |

Each job records the actual OS, CPU architecture, Python version and imported
package path. It reads synthetic SIGPROC, folded PSRFITS and CHIME HDF5 files,
checks measurements and Gaussian-width recovery with the native fitter, writes
PNG export artifacts, replays a saved session, and starts the server over real
HTTP to check the interface assets. Results and server logs are retained as CI
artifacts. The script reuses `tests/conftest.py`; no observatory data is needed.

The full numerical unit suite separately runs on Linux with Python 3.11–3.14,
and browser tests run in Chromium on Linux. The wheel checks exercise specific
synthetic formats and workflows; they do not establish compatibility with every
instrument's file dialect or every optional reader dependency. Windows ARM64
is outside this matrix.

Runner labels follow [GitHub's hosted-runner documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job).

## Docker

Image builds target `linux/amd64` and `linux/arm64`. CI builds each architecture
on a native runner and exercises the installed image with the same analysis,
export, replay and HTTP checks as the wheel. The image's runtime dependencies
use `requirements.txt`; wheel checks use the compatible package ranges.
The CI-only `pytest` installation supports fixture generation in a disposable
test container and is not added to the published image.

The published image is available at `ghcr.io/dirkkuiper/flits`.

Recommended tags:

- `latest` for the newest stable release
- a minor version such as `1.7` for its newest patch release
- an exact tag such as `1.7.0` when you want a reproducible deployment
- `edge` for snapshots built from `main`

Every release image is built for `linux/amd64` and `linux/arm64`.

Run the latest stable release:

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
  ghcr.io/dirkkuiper/flits:1.7.0
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

### On a Slurm worker node

Clusters usually expect interactive work to run in a job on a worker node, not
on the login node. Start an interactive job, then run FLITS from the Apptainer
image inside it:

```bash
srun --time=04:00:00 --cpus-per-task=2 --pty bash -l
hostname -s    # the worker's short name, for example wn-01
APPTAINERENV_FLITS_DATA_DIR=/data \
  apptainer exec --bind /path/to/filterbanks:/data flits.sif \
  flits --data-dir /data --host 127.0.0.1 --port 8123
```

From your own computer, tunnel through the login node to the worker:

```bash
ssh -N -L 8123:127.0.0.1:8123 -J user@login-node user@wn-01
```

and open `http://127.0.0.1:8123`. This needs SSH access from the login node to
a worker where you have a running job, which most clusters allow. If yours
does not, FLITS has to listen on the worker's network interface
(`--host 0.0.0.0`) and the tunnel ends there instead:

```bash
ssh -N -L 8123:wn-01:8123 user@login-node
```

FLITS has no authentication. With `--host 0.0.0.0`, anyone who can reach that
worker node can use the interface and read the data directory, so prefer the
first form on shared clusters, and stop FLITS when you are done. Check that it
is serving with `curl http://127.0.0.1:8123/api/health` on the worker.

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
