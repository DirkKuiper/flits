#!/usr/bin/env bash

set -euo pipefail

archive_path="${HOME}/containers/flits_spider.tar.gz"
output_path="${HOME}/containers/flits_spider.sif"
force_build=0

usage() {
  cat <<'EOF'
Usage: scripts/spider/import_spider_sif.sh [options]

Convert a Docker archive copied to SPIDER into an Apptainer SIF image.

Options:
  --archive PATH     Input Docker archive (default: ~/containers/flits_spider.tar.gz)
  --output PATH      Output SIF path (default: ~/containers/flits_spider.sif)
  --force            Overwrite an existing SIF at the output path
  -h, --help         Show this help text
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      archive_path="${2:?missing value for --archive}"
      shift 2
      ;;
    --output)
      output_path="${2:?missing value for --output}"
      shift 2
      ;;
    --force)
      force_build=1
      shift
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

archive_path="${archive_path/#\~/${HOME}}"
output_path="${output_path/#\~/${HOME}}"

if ! command -v apptainer >/dev/null 2>&1; then
  echo "apptainer is required to create the SPIDER SIF image." >&2
  exit 1
fi

if [[ ! -f "${archive_path}" ]]; then
  echo "Docker archive not found: ${archive_path}" >&2
  exit 1
fi

mkdir -p "$(dirname "${output_path}")"

build_args=()
if [[ "${force_build}" -eq 1 ]]; then
  build_args+=(--force)
elif [[ -e "${output_path}" ]]; then
  echo "Refusing to overwrite existing SIF: ${output_path}" >&2
  echo "Re-run with --force to replace it." >&2
  exit 1
fi

echo "Building ${output_path} from ${archive_path}"
apptainer build "${build_args[@]}" "${output_path}" "docker-archive:${archive_path}"

echo "Created ${output_path}"
