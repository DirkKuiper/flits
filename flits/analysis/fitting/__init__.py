"""Fits of the fitburst burst model (Fonseca et al. 2024), implemented natively in FLITS."""

from .burst_model import BurstModel, BurstParameters, ParameterLayout
from .least_squares import BurstFitResult, ModelComparison, channel_weights, compare_fits, fit_burst_model
from .model_fit import *
from .model_fit import __all__ as _model_fit_all

__all__ = [
    "BurstFitResult",
    "BurstModel",
    "BurstParameters",
    "ModelComparison",
    "ParameterLayout",
    "channel_weights",
    "compare_fits",
    "fit_burst_model",
    *_model_fit_all,
]
