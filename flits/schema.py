"""Offline, versioned JSON schemas shipped with the package."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

SCHEMAS = {"measurement": "measurement-v1.json", "batch-manifest": "batch-manifest-v1.json"}


def get_schema(name: str) -> dict[str, Any]:
    """Return a fresh schema object, without network access or analysis imports."""
    if name not in SCHEMAS:
        raise ValueError(f"Unknown schema: {name}. Choose from {', '.join(SCHEMAS)}.")
    return json.loads(files("flits").joinpath("schemas", SCHEMAS[name]).read_text(encoding="utf-8"))
