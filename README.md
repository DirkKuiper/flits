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

## Run On SPIDER Via VS Code SSH

The recommended SPIDER workflow is:

- build the FLITS image on your workstation for `linux/amd64`
- copy the exported Docker archive to SPIDER once
- convert that archive to an Apptainer `.sif`
- launch FLITS inside an interactive Slurm job on a worker node
- open a second SSH tunnel from your laptop to the worker node port

This keeps the browser local, the web server on the worker node, and the software stack reproducible.

Reference docs used for this workflow:

- SPIDER compute guidance: <https://doc.spider.surfsara.nl/en/latest/Pages/compute_on_spider.html>
- SPIDER software guidance: <https://doc.spider.surfsara.nl/en/latest/Pages/software_on_spider.html>
- SPIDER notebook tunnel pattern: <https://doc.spider.surfsara.nl/en/latest/Pages/jupyter_notebooks.html>
- Apptainer Docker archive support: <https://apptainer.org/docs/user/latest/docker_and_oci.html>

### 1. Build A SPIDER-Compatible Image Locally

If your laptop is Apple Silicon, do not ship an `arm64` image to SPIDER. Build explicitly for `linux/amd64`.

Helper script:

```bash
scripts/spider/build_spider_image.sh
```

Equivalent raw commands:

```bash
docker buildx build --platform linux/amd64 --load -t flits:spider .
docker save flits:spider | gzip > dist/flits_spider.tar.gz
```

By default the helper writes `dist/flits_spider.tar.gz`.

### 2. Copy The Archive To SPIDER And Convert It To SIF

Copy the archive from your workstation:

```bash
scp dist/flits_spider.tar.gz <your-spider-ssh-target>:~/containers/
```

Then, on SPIDER:

```bash
scripts/spider/import_spider_sif.sh \
  --archive ~/containers/flits_spider.tar.gz \
  --output ~/containers/flits_spider.sif
```

Equivalent raw command:

```bash
apptainer build ~/containers/flits_spider.sif docker-archive:~/containers/flits_spider.tar.gz
```

The resulting `.sif` is a single portable file you can keep in `~/containers` for personal use.

### 3. Start FLITS On A SPIDER Worker Node

From your VS Code SSH terminal on SPIDER, launch FLITS through Slurm and Apptainer:

```bash
scripts/spider/run_flits_job.sh \
  --data-dir /project/<project>/filterbanks \
  --image ~/containers/flits_spider.sif \
  --ssh-target <your-spider-ssh-target>
```

Defaults:

- partition: `interactive`
- cores: `2`
- walltime: `04:00:00`
- port: `8123`

The script prints the worker hostname and the exact tunnel command you should run on your laptop. It then starts:

```bash
apptainer exec --bind /project/<project>/filterbanks:/data \
  ~/containers/flits_spider.sif \
  python -m flits --host 0.0.0.0 --port 8123
```

Inside the container, `FLITS_DATA_DIR` is set to `/data`, so relative `.fil` paths resolve against the bound SPIDER project directory.

### 4. Open The Tunnel From Your Laptop

Run this in a local terminal, not inside the SPIDER shell:

```bash
scripts/spider/open_local_tunnel.sh <your-spider-ssh-target> <worker-hostname> 8123
```

Equivalent raw command:

```bash
ssh -N -L 8123:<worker-hostname>:8123 <your-spider-ssh-target>
```

Then open `http://127.0.0.1:8123`.

### 5. Verify And Shut Down

On the SPIDER worker node:

```bash
curl http://127.0.0.1:8123/api/health
curl http://127.0.0.1:8123/api/files
```

Then load a known filterbank in the browser.

When you are done:

- stop FLITS with `Ctrl-C`
- exit the Slurm shell
- close the local tunnel terminal

### Notes

- This is intended for personal ad-hoc use, not a shared public service.
- FLITS currently has no authentication and allows permissive CORS, so do not expose the port beyond your SSH tunnel.
- If you later want faster day-to-day use, keep the `.sif` in `~/containers` and only repeat the Slurm launch plus tunnel steps.

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
