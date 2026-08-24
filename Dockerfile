# syntax=docker/dockerfile:1.6
FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    FLITS_DATA_DIR=/data

WORKDIR /app

# Pick up Debian security fixes from the base image stream before installing
# Python dependencies. This keeps the scheduled container rebuild useful even
# when the Python image tag has not been republished yet.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Keep the bundled build tooling current. Scanners flag the pip and setuptools
# versions shipped in the base image even when the app dependencies themselves
# are clean -- setuptools 70.x in particular carries CVE-2025-47273.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip setuptools wheel

# Install dependencies first so edits to flits/ don't bust this layer.
# BuildKit cache mount keeps pip's wheel cache across builds without bloating the image.
COPY requirements.txt requirements-full.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-full.txt

# Then install the project itself; --no-deps skips re-resolving requirements.
COPY pyproject.toml README.md MANIFEST.in ./
COPY docs ./docs
COPY flits ./flits
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps .

# Lift transitive dependencies that resolve to versions carrying advisories.
# None of these are imported by FLITS directly, but the image is a release
# artifact and should not ship known-vulnerable packages.
#
# This runs last so nothing installed earlier can pull a fixed version back
# down, and the versions are asserted afterwards so a regression fails the build
# instead of reaching the registry and the scheduled scan.
COPY docker/verify_security_floors.py /tmp/verify_security_floors.py
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade "msgpack>=1.2.1" "setuptools>=78.1.1" && \
    python /tmp/verify_security_floors.py && \
    rm /tmp/verify_security_floors.py

EXPOSE 8123

CMD ["flits", "--host", "0.0.0.0", "--port", "8123"]
