"""Polarization and Faraday-depth analysis."""

from .rm_synthesis import RMSynthesisResult, run_rm_synthesis
from .workflow import IntegratedPolarizationSpectrum, extract_normalized_linear_spectrum

__all__ = [
    "IntegratedPolarizationSpectrum",
    "RMSynthesisResult",
    "extract_normalized_linear_spectrum",
    "run_rm_synthesis",
]
