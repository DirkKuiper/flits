"""FLITS: Fast-Look Interactive Transient Suite."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.4.0"

__all__ = [
    "BurstMeasurements",
    "BurstSession",
    "DmOptimizationResult",
    "ExportArtifact",
    "ExportManifest",
    "ExportPlotPreview",
    "ExportPreview",
    "ExportPreviewArtifact",
    "FilterbankMetadata",
    "GaussianFit1D",
    "MeasurementDiagnostics",
    "MeasurementProvenance",
    "MeasurementUncertainties",
    "ObservationConfig",
    "TelescopePreset",
    "__version__",
    "available_presets",
    "detect_preset",
    "get_preset",
]

if TYPE_CHECKING:
    from flits.models import (
        BurstMeasurements,
        DmOptimizationResult,
        ExportArtifact,
        ExportManifest,
        ExportPlotPreview,
        ExportPreview,
        ExportPreviewArtifact,
        FilterbankMetadata,
        GaussianFit1D,
        MeasurementDiagnostics,
        MeasurementProvenance,
        MeasurementUncertainties,
    )
    from flits.session import BurstSession
    from flits.settings import ObservationConfig, TelescopePreset, available_presets, detect_preset, get_preset


def __getattr__(name: str):
    if name in {
        "BurstMeasurements",
        "DmOptimizationResult",
        "ExportArtifact",
        "ExportManifest",
        "ExportPlotPreview",
        "ExportPreview",
        "ExportPreviewArtifact",
        "FilterbankMetadata",
        "GaussianFit1D",
        "MeasurementDiagnostics",
        "MeasurementProvenance",
        "MeasurementUncertainties",
    }:
        models = importlib.import_module("flits.models")

        return getattr(models, name)

    if name in {"ObservationConfig", "TelescopePreset", "available_presets", "detect_preset", "get_preset"}:
        settings = importlib.import_module("flits.settings")

        return getattr(settings, name)

    if name == "BurstSession":
        BurstSession = importlib.import_module("flits.session").BurstSession

        return BurstSession

    raise AttributeError(f"module 'flits' has no attribute {name!r}")
