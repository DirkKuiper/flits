#!/usr/bin/env bash

set -euo pipefail

image_path="${HOME}/containers/flits_spider.sif"
data_dir=""
port="8123"
partition="interactive"
time_limit="04:00:00"
cpus="2"
job_name="flits-web"
ssh_target="${SPIDER_SSH_TARGET:-}"
account=""

usage() {
  cat <<'EOF'
Usage: scripts/spider/run_flits_job.sh --data-dir PATH [options]

Allocate a SPIDER Slurm worker node and run FLITS inside Apptainer.

Options:
  --data-dir PATH    Host directory containing .fil files to bind at /data
  --image PATH       SIF image to run (default: ~/containers/flits_spider.sif)
  --port VALUE       FLITS port on the worker node (default: 8123)
  --partition NAME   Slurm partition to request (default: interactive)
  --time HH:MM:SS    Slurm walltime (default: 04:00:00)
  --cpus VALUE       CPU cores to request (default: 2)
  --job-name NAME    Slurm job name (default: flits-web)
  --account NAME     Slurm account to charge, if required
  --ssh-target HOST  SSH host or alias for the login node, used in printed tunnel hints
  -h, --help         Show this help text
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir)
      data_dir="${2:?missing value for --data-dir}"
      shift 2
      ;;
    --image)
      image_path="${2:?missing value for --image}"
      shift 2
      ;;
    --port)
      port="${2:?missing value for --port}"
      shift 2
      ;;
    --partition)
      partition="${2:?missing value for --partition}"
      shift 2
      ;;
    --time)
      time_limit="${2:?missing value for --time}"
      shift 2
      ;;
    --cpus)
      cpus="${2:?missing value for --cpus}"
      shift 2
      ;;
    --job-name)
      job_name="${2:?missing value for --job-name}"
      shift 2
      ;;
    --account)
      account="${2:?missing value for --account}"
      shift 2
      ;;
    --ssh-target)
      ssh_target="${2:?missing value for --ssh-target}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${data_dir}" ]]; then
  echo "--data-dir is required." >&2
  usage >&2
  exit 1
fi

image_path="${image_path/#\~/${HOME}}"
data_dir="${data_dir/#\~/${HOME}}"

if ! command -v srun >/dev/null 2>&1; then
  echo "srun is required to allocate a SPIDER worker node." >&2
  exit 1
fi

if [[ ! -f "${image_path}" ]]; then
  echo "SIF image not found: ${image_path}" >&2
  exit 1
fi

if [[ ! -d "${data_dir}" ]]; then
  echo "Data directory not found: ${data_dir}" >&2
  exit 1
fi

export FLITS_JOB_IMAGE="${image_path}"
export FLITS_JOB_DATA_DIR="${data_dir}"
export FLITS_JOB_PORT="${port}"
export FLITS_JOB_SSH_TARGET="${ssh_target}"

srun_args=(
  --partition="${partition}"
  -N 1
  -c "${cpus}"
  --time="${time_limit}"
  --job-name="${job_name}"
)

if [[ -n "${account}" ]]; then
  srun_args+=(--account="${account}")
fi

srun "${srun_args[@]}" --pty bash -i -l -c '
set -euo pipefail

worker_host=$(hostname)

if ! command -v apptainer >/dev/null 2>&1; then
  echo "apptainer is not available in the worker-node environment." >&2
  exit 1
fi

echo "Worker host: ${worker_host}"
echo "FLITS image: ${FLITS_JOB_IMAGE}"
echo "Bound data dir: ${FLITS_JOB_DATA_DIR}"
echo "Health check on Spider:"
echo "  curl http://127.0.0.1:${FLITS_JOB_PORT}/api/health"

if [[ -n "${FLITS_JOB_SSH_TARGET:-}" ]]; then
  echo "Open a local tunnel from your laptop with:"
  echo "  ssh -N -L ${FLITS_JOB_PORT}:${worker_host}:${FLITS_JOB_PORT} ${FLITS_JOB_SSH_TARGET}"
else
  echo "Open a local tunnel from your laptop with:"
  echo "  ssh -N -L ${FLITS_JOB_PORT}:${worker_host}:${FLITS_JOB_PORT} <your-spider-ssh-target>"
fi

echo "Then browse to http://127.0.0.1:${FLITS_JOB_PORT}"

export APPTAINERENV_FLITS_DATA_DIR=/data
export APPTAINERENV_OMP_NUM_THREADS=1
export APPTAINERENV_OPENBLAS_NUM_THREADS=1
export APPTAINERENV_MKL_NUM_THREADS=1

exec apptainer exec \
  --bind "${FLITS_JOB_DATA_DIR}:/data" \
  "${FLITS_JOB_IMAGE}" \
  python -m flits --host 0.0.0.0 --port "${FLITS_JOB_PORT}"
'
