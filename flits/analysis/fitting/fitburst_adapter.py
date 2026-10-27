"""Former name of :mod:`flits.analysis.fitting.model_fit`.

FLITS used to delegate model fits to the optional fitburst package through
this module. It now fits the fitburst model natively; the names below are kept
so existing imports keep working.
"""

from flits.analysis.fitting.model_fit import (
    DEFAULT_FIXED_PARAMETERS,
    DEFAULT_FREE_PARAMETERS,
    FIT_PARAMETERS,
    GLOBAL_PARAMETERS,
    MAX_MODEL_FIT_CELLS,
    MAX_MODEL_FIT_FUNCTION_EVALUATIONS,
    MIN_FIT_CHANNELS,
    MIN_FIT_TIME_BINS,
    NON_FITTABLE_PARAMETERS,
    SCINTILLATION_INACTIVE_PARAMETERS,
    WEIGHTING_MODES,
    ModelFitRequestConfig,
    ModelFitResult,
    fit_model_selected_band,
)

__all__ = [
    "DEFAULT_FIXED_PARAMETERS",
    "DEFAULT_FREE_PARAMETERS",
    "FIT_PARAMETERS",
    "GLOBAL_PARAMETERS",
    "MAX_MODEL_FIT_CELLS",
    "MAX_MODEL_FIT_FUNCTION_EVALUATIONS",
    "MIN_FIT_CHANNELS",
    "MIN_FIT_TIME_BINS",
    "NON_FITTABLE_PARAMETERS",
    "SCINTILLATION_INACTIVE_PARAMETERS",
    "WEIGHTING_MODES",
    "ModelFitRequestConfig",
    "ModelFitResult",
    "fit_model_selected_band",
]
