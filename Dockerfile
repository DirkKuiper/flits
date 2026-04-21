# syntax=docker/dockerfile:1.6
FROM python:3.14.0-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    FLITS_DATA_DIR=/data

WORKDIR /app

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

EXPOSE 8123

CMD ["flits", "--host", "0.0.0.0", "--port", "8123"]
