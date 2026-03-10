#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)

image_tag="flits:spider"
platform="linux/amd64"
output_path="${REPO_ROOT}/dist/flits_spider.tar.gz"

usage() {
  cat <<'EOF'
Usage: scripts/spider/build_spider_image.sh [options]

Build the FLITS Docker image for SPIDER and export it as a gzipped docker-save archive.

Options:
  --image-tag TAG    Docker tag to build and export (default: flits:spider)
  --platform VALUE   Target platform for buildx (default: linux/amd64)
  --output PATH      Output archive path (default: dist/flits_spider.tar.gz)
  -h, --help         Show this help text
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image-tag)
      image_tag="${2:?missing value for --image-tag}"
      shift 2
      ;;
    --platform)
      platform="${2:?missing value for --platform}"
      shift 2
      ;;
    --output)
      output_path="${2:?missing value for --output}"
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

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to build the SPIDER image archive." >&2
  exit 1
fi

mkdir -p "$(dirname "${output_path}")"

echo "Building ${image_tag} for ${platform} from ${REPO_ROOT}"
docker buildx build --platform "${platform}" --load -t "${image_tag}" "${REPO_ROOT}"

echo "Exporting ${image_tag} to ${output_path}"
docker save "${image_tag}" | gzip > "${output_path}"

echo "Created ${output_path}"
