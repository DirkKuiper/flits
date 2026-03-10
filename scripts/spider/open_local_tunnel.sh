#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/spider/open_local_tunnel.sh SSH_TARGET WORKER_HOST [LOCAL_PORT] [REMOTE_PORT]

Open an SSH tunnel from your laptop to a FLITS process running on a SPIDER worker node.

Arguments:
  SSH_TARGET   SSH host or alias for the SPIDER login node
  WORKER_HOST  Worker hostname printed by scripts/spider/run_flits_job.sh
  LOCAL_PORT   Local browser port to expose (default: 8123)
  REMOTE_PORT  Worker-node FLITS port (default: LOCAL_PORT)
EOF
}

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 || $# -gt 4 ]]; then
  usage >&2
  exit 1
fi

ssh_target="$1"
worker_host="$2"
worker_host="${worker_host%%.*}"
local_port="${3:-8123}"
remote_port="${4:-${local_port}}"

echo "Forwarding http://127.0.0.1:${local_port} to ${worker_host}:${remote_port} via ${ssh_target}"
exec ssh -N -o ExitOnForwardFailure=yes -L "${local_port}:${worker_host}:${remote_port}" "${ssh_target}"
