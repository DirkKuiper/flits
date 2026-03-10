# FLITS On SPIDER

This document keeps the SPIDER-specific workflow separate from the general install path in the main [README.md](/Users/dirk/Desktop/PhD/Code/analyzer/README.md).

Reference docs used for this workflow:

- SPIDER compute guidance: <https://doc.spider.surfsara.nl/en/latest/Pages/compute_on_spider.html>
- SPIDER software guidance: <https://doc.spider.surfsara.nl/en/latest/Pages/software_on_spider.html>
- SPIDER notebook tunnel pattern: <https://doc.spider.surfsara.nl/en/latest/Pages/jupyter_notebooks.html>
- Apptainer Docker archive support: <https://apptainer.org/docs/user/latest/docker_and_oci.html>

## Recommended Path

For personal use on SPIDER:

- keep Docker as the canonical image build format
- build or export the image off-cluster
- convert it to a `.sif` once on SPIDER
- launch FLITS inside an interactive Slurm job on a worker node
- reach it through a local SSH tunnel from your laptop

If `ghcr.io/dirkkuiper/flits:latest` is already published and SPIDER can pull from GHCR, you can skip the archive-copy step and pull directly with Apptainer.

## Option 1: Pull The Published OCI Image Directly

On SPIDER:

```bash
mkdir -p ~/containers
apptainer pull ~/containers/flits_latest.sif docker://ghcr.io/dirkkuiper/flits:latest
```

Then jump to [Run FLITS On A Worker Node](#run-flits-on-a-worker-node).

## Option 2: Build Locally And Import On SPIDER

If your laptop is Apple Silicon, do not ship an `arm64` image to SPIDER. Build explicitly for `linux/amd64`.

On your workstation:

```bash
scripts/spider/build_spider_image.sh
scp dist/flits_spider.tar.gz <your-spider-ssh-target>:~/containers/
```

On SPIDER:

```bash
scripts/spider/import_spider_sif.sh \
  --archive ~/containers/flits_spider.tar.gz \
  --output ~/containers/flits_spider.sif
```

## Run FLITS On A Worker Node

From your VS Code SSH terminal on SPIDER:

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

The launcher prints the worker hostname and the exact SSH tunnel command to run from your laptop.

## Open The Tunnel From Your Laptop

Run this in a local terminal, not inside the SPIDER shell:

```bash
scripts/spider/open_local_tunnel.sh <your-spider-ssh-target> <worker-hostname> 8123
```

Equivalent raw command:

```bash
ssh -N -L 8123:<worker-hostname>:8123 <your-spider-ssh-target>
```

Then open `http://127.0.0.1:8123`.

## Verify And Shut Down

On the SPIDER worker node:

```bash
curl http://127.0.0.1:8123/api/health
curl http://127.0.0.1:8123/api/files
```

When you are done:

- stop FLITS with `Ctrl-C`
- exit the Slurm shell
- close the local tunnel terminal

## Notes

- This workflow is intended for personal ad-hoc use, not a shared public service.
- FLITS currently has no authentication and allows permissive CORS, so do not expose the port beyond your SSH tunnel.
- Once a GHCR image is routinely published, direct `apptainer pull` is the simplest SPIDER path.
