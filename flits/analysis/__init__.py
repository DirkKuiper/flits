"""Public analysis entry points exposed by FLITS.

The analysis package groups the main higher-level routines used by the session
layer: width/morphology measurements, DM optimization, averaged spectral
analysis, temporal-structure analysis, and sub-burst drift-rate measurement.
"""

from __future__ import annotations

from flits.analysis.dm_optimization import optimize_dm_trials
from flits.analysis.drift import DriftAnalysisInputs, run_drift_analysis
from flits.analysis.morphology import compute_width_analysis
from flits.analysis.polarization import extract_normalized_linear_spectrum, run_rm_synthesis

# Preserve the historical package-level default_segment_bins export for now.
from flits.analysis.spectral.core import default_segment_bins, run_averaged_spectral_analysis
from flits.analysis.temporal.core import run_temporal_structure_analysis
from flits.analysis.temporal.multiscale import (
    HaarExcessPowerResult,
    excess_power_fraction_below,
    haar_excess_power,
)

__all__ = [
    "DriftAnalysisInputs",
    "HaarExcessPowerResult",
    "compute_width_analysis",
    "default_segment_bins",
    "excess_power_fraction_below",
    "extract_normalized_linear_spectrum",
    "haar_excess_power",
    "optimize_dm_trials",
    "run_averaged_spectral_analysis",
    "run_drift_analysis",
    "run_rm_synthesis",
    "run_temporal_structure_analysis",
]
