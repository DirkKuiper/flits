"""FLITS: Fast-Look Interactive Transient Suite.

FLITS measures fast radio bursts that have already been detected, in SIGPROC
filterbank (``.fil``), PSRFITS (``.fits``, ``.sf``) and CHIME/FRB HDF5 (``.h5``)
files. It masks interference, localizes the burst in time and frequency,
optimizes the DM, and measures S/N, peak flux density, fluence, widths, arrival
times, sub-burst drift rate and rotation measure, classifying each uncertainty.

There are three entry points:

- ``flits measure FILE... --dm DM`` on the command line prints one JSON record
  per burst (``--format jsonl`` or ``csv`` also work).
- ``flits.measure_burst(path, flits.MeasureOptions(dm=...))`` returns the same
  record in Python.
- ``flits.BurstSession.from_file(path, dm=...)`` gives full control. Every
  action in the browser interface is a method on it.

Check each record's ``status`` and ``localization.warning_flags`` before using
its numbers. Times are in ms from the start of the file, frequencies in MHz,
and DM in pc cm^-3. Documentation: https://dirkkuiper.github.io/flits/, and for
language models https://dirkkuiper.github.io/flits/llms.txt.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "1.6.0"

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
    "MeasureOptions",
    "MeasurementDiagnostics",
    "MeasurementProvenance",
    "MeasurementUncertainties",
    "ObservationConfig",
    "TelescopePreset",
    "__version__",
    "available_presets",
    "detect_preset",
    "get_preset",
    "measure_burst",
    "measure_bursts",
]

if TYPE_CHECKING:
    from flits.measure import MeasureOptions, measure_burst, measure_bursts
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

    if name in {"MeasureOptions", "measure_burst", "measure_bursts"}:
        measure = importlib.import_module("flits.measure")

        return getattr(measure, name)

    if name == "BurstSession":
        BurstSession = importlib.import_module("flits.session").BurstSession

        return BurstSession

    raise AttributeError(f"module 'flits' has no attribute {name!r}")
