"""FLITS: Fast-Look Interactive Transient Suite."""

from flits.models import BurstMeasurements, FilterbankMetadata, GaussianFit1D
from flits.settings import ObservationConfig, TelescopePreset, available_presets, detect_preset, get_preset
from flits.session import BurstSession

__version__ = "0.1.0"

__all__ = [
    "BurstMeasurements",
    "BurstSession",
    "FilterbankMetadata",
    "GaussianFit1D",
    "ObservationConfig",
    "TelescopePreset",
    "__version__",
    "available_presets",
    "detect_preset",
    "get_preset",
]
