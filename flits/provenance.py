"""Portable software provenance, with separate origins for cached analyses.

Environment capture is local and read-only. It never runs pip or contacts a
package server. Local source paths and machine hostnames are omitted, and
installer URLs have userinfo, queries, and fragments stripped.
The process environment is captured once; restart FLITS after changing code or
installing packages. A record describes software, not a restorable environment.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from flits import __version__

PROVENANCE_SCHEMA_VERSION = "1.0"
SESSION_SCHEMA_VERSION = "1.7"
SUPPORTED_SESSION_SCHEMAS = frozenset(f"1.{minor}" for minor in range(8))


def validate_session_schema(version: str) -> None:
    if version not in SUPPORTED_SESSION_SCHEMAS:
        raise ValueError(
            f"Unsupported session schema {version!r}; this FLITS supports 1.0 through {SESSION_SCHEMA_VERSION}. "
            "Use a compatible FLITS release; the source snapshot has not been modified."
        )


def _git(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=3, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def source_identity(path: Path) -> dict[str, Any]:
    """Identify a local source tree without exporting its filesystem location."""
    root_text = _git(path, "rev-parse", "--show-toplevel")
    result: dict[str, Any] = {"commit": None, "dirty": None}
    if root_text:
        root = Path(root_text).resolve()
        # Avoid accidentally identifying a parent repository containing an
        # unrelated virtualenv or unpacked copy as this package's source.
        tracked = _git(root, "ls-files", "--", str(path.resolve()))
        if tracked:
            result["commit"] = _git(root, "rev-parse", "HEAD")
            status = _git(root, "status", "--porcelain", "--untracked-files=normal", "--", str(path.resolve()))
            result["dirty"] = None if status is None else bool(status)
    digest = hashlib.sha256()
    count = 0
    try:
        for item in sorted(path.rglob("*.py")):
            if any(part in {".git", ".venv", "__pycache__"} for part in item.relative_to(path).parts):
                continue
            digest.update(item.relative_to(path).as_posix().encode() + b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
            count += 1
    except OSError:
        return {**result, "python_source_sha256": None}
    return {**result, "python_source_sha256": digest.hexdigest() if count else None}


def _direct_source(dist: metadata.Distribution) -> dict[str, Any] | None:
    try:
        raw = dist.read_text("direct_url.json")
        data = json.loads(raw) if raw else None
        if not isinstance(data, dict):
            return None
        url = urlsplit(str(data.get("url", "")))
        if url.scheme == "file":
            path = Path(unquote(url.path))
            module_name = str(dist.metadata.get("Name", "")).replace("-", "_")
            package_path = next(
                (candidate for candidate in (path / module_name, path / "src" / module_name) if candidate.is_dir()),
                None,
            )
            return {
                "kind": "local",
                "editable": bool(data.get("dir_info", {}).get("editable", False)),
                "archive_hashes": data.get("archive_info", {}).get("hashes", {}),
                **(source_identity(package_path) if package_path is not None else {"commit": None, "dirty": None}),
            }
        # Only include the public location: no userinfo, query, or fragment.
        location = urlunsplit((url.scheme, url.hostname or "", url.path, "", ""))
        source: dict[str, Any] = {"url": location}
        if isinstance(data.get("vcs_info"), dict):
            source.update({key: data["vcs_info"].get(key) for key in ("vcs", "commit_id", "requested_revision")})
        if isinstance(data.get("archive_info"), dict):
            source["archive_hashes"] = data["archive_info"].get("hashes", {})
        return source
    except (OSError, ValueError, TypeError, AttributeError):
        return {"status": "unavailable"}


@lru_cache(maxsize=1)
def _process_environment() -> dict[str, Any]:
    packages: dict[str, Any] = {}
    for dist in metadata.distributions():
        name = re.sub(r"[-_.]+", "-", str(dist.metadata.get("Name", ""))).lower()
        if not name:
            continue
        package: dict[str, Any] = {"version": dist.version}
        source = _direct_source(dist)
        if source is not None:
            package["source"] = source
        packages[name] = package
    # Explicit absence is useful for the optional fitter.
    packages.setdefault("fitburst", {"version": None, "status": "not_installed"})
    # A checkout on sys.path can shadow an installed distribution. Identify
    # the modules actually imported for the specialist wrappers as well.
    for name in ("your", "jess", "fitburst"):
        module = sys.modules.get(name)
        package = packages.setdefault(name, {"version": None, "status": "metadata_unavailable"})
        package["loaded"] = module is not None
        location = getattr(module, "__file__", None)
        if location:
            package["loaded_source"] = source_identity(Path(location).resolve().parent)
    return {
        "flits_version": __version__,
        "flits_source": source_identity(Path(__file__).resolve().parent),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "packages": dict(sorted(packages.items())),
    }


def capture_environment() -> dict[str, Any]:
    """Return an independent JSON-compatible copy of this process's software."""
    return copy.deepcopy(_process_environment())


def environment_id(environment: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(environment, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass
class SoftwareProvenance:
    runtime: dict[str, Any] = field(default_factory=capture_environment)
    environments: dict[str, Any] = field(default_factory=dict)
    products: dict[str, Any] = field(default_factory=dict)

    @property
    def runtime_id(self) -> str:
        return environment_id(self.runtime)

    def record(self, name: str, *, inputs: tuple[str, ...] = ()) -> None:
        self.environments[self.runtime_id] = copy.deepcopy(self.runtime)
        # Freeze the upstream software identities at calculation time. Later
        # recomputation of an input must not rewrite a cached result's origin.
        origins: dict[str, Any] = {}
        for key in inputs:
            parent = self.products.get(key, {})
            origins[key] = copy.deepcopy(parent) if parent else {"environment_id": None}
        self.products[name] = {"environment_id": self.runtime_id, "inputs": origins}

    def discard(self, *names: str) -> None:
        for name in names:
            self.products.pop(name, None)

    def annotate(self, name: str, key: str, source: str | None) -> None:
        """Update a derived annotation without relabelling the base calculation."""
        origin = self.products.setdefault(name, {"environment_id": None, "reason": "not_recorded"})
        inputs = origin.setdefault("inputs", {})
        if source is None:
            inputs.pop(key, None)
        else:
            inputs[key] = copy.deepcopy(self.products.get(source, {"environment_id": None}))

    def to_dict(self, active: list[str]) -> dict[str, Any]:
        environments = copy.deepcopy(self.environments)
        environments[self.runtime_id] = copy.deepcopy(self.runtime)
        return {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "saved_with": self.runtime_id,
            "environments": environments,
            "products": {
                name: copy.deepcopy(self.products.get(name, {"environment_id": None, "reason": "not_recorded"}))
                for name in active
            },
        }

    @classmethod
    def restore(cls, payload: dict[str, Any] | None, *, runtime: dict[str, Any]) -> SoftwareProvenance:
        result = cls(runtime=copy.deepcopy(runtime))
        if payload is None:
            return result
        if not isinstance(payload, dict) or payload.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
            raise ValueError("Unsupported software provenance schema.")
        environments = payload.get("environments")
        products = payload.get("products")
        if not isinstance(environments, dict) or not isinstance(products, dict):
            raise ValueError("Software provenance requires environment and product mappings.")
        for key, env in environments.items():
            if not isinstance(env, dict) or key != environment_id(env):
                raise ValueError("Software provenance environment identity does not match its content.")

        def validate_record(record: Any, depth: int = 0) -> None:
            if not isinstance(record, dict) or depth > 20:
                raise ValueError("Invalid software provenance product record.")
            key = record.get("environment_id")
            if key is not None and (not isinstance(key, str) or key not in environments):
                raise ValueError("Software provenance refers to a missing environment.")
            inputs = record.get("inputs", {})
            if not isinstance(inputs, dict):
                raise ValueError("Invalid software provenance inputs.")
            for value in inputs.values():
                validate_record(value, depth + 1)

        for record in products.values():
            validate_record(record)
        saved_with = payload.get("saved_with")
        if not isinstance(saved_with, str) or saved_with not in environments:
            raise ValueError("Software provenance save environment is missing.")
        result.environments = copy.deepcopy(environments)
        result.products = copy.deepcopy(products)
        return result

    def warnings(self, active: list[str]) -> list[str]:
        def identities(record: dict[str, Any]) -> set[str | None]:
            found = {record.get("environment_id")}
            for value in record.get("inputs", {}).values():
                found.update(identities(value))
            return found

        origins = {key: identities(self.products.get(key, {})) for key in active}
        unknown = [key for key, ids in origins.items() if None in ids]
        changed = [key for key, ids in origins.items() if ids - {None, self.runtime_id}]
        messages = []
        if unknown:
            messages.append(
                "Original software environment was not recorded for these results or their inputs: "
                + ", ".join(unknown)
                + "."
            )
        if changed:
            messages.append(
                "Stored results or their inputs were produced in a different software environment: "
                + ", ".join(changed)
                + "."
            )
        return messages
