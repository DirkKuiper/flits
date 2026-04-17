"""Public analysis entry points exposed by FLITS.

The analysis package groups the main higher-level routines used by the session
layer: width/morphology measurements, DM optimization, averaged spectral
analysis, and temporal-structure analysis.
"""

from __future__ import annotations

from flits.analysis.dm_optimization import optimize_dm_trials
from flits.analysis.spectral.core import default_segment_bins, run_averaged_spectral_analysis
from flits.analysis.morphology import compute_width_analysis
from flits.analysis.temporal.core import run_temporal_structure_analysis

__all__ = [
    "compute_width_analysis",
    "default_segment_bins",
    "optimize_dm_trials",
    "run_averaged_spectral_analysis",
    "run_temporal_structure_analysis",
]
