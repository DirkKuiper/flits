# Python API Reference

FLITS is usable as a library as well as through the browser. Everything the
interface does goes through the same objects documented here, so an analysis
scripted against this API and one performed interactively produce the same
numbers.

For reproducing a saved analysis without writing code, see
[Headless Replay](../user-guide/headless-replay.md).

## A minimal analysis

```python
import json

from flits import BurstSession

session = BurstSession.from_file(
    "data/GBT-L/burst.fil",
    dm=527.65,
    sefd_jy=10.0,
    sefd_fractional_uncertainty=0.1,
)

session.set_crop_ms(80.0, 200.0)
session.set_event_ms(120.0, 128.0)
session.add_offpulse_ms(0.0, 80.0)
session.auto_mask_jess("auto")

measurements = session.compute_properties()
print(measurements.fluence_jyms, measurements.snr)

# Persist everything needed to reproduce this later.
with open("burst_flits_session.json", "w") as handle:
    json.dump(session.snapshot_dict(), handle, indent=2)
```

## Session

::: flits.session.BurstSession

## Input

::: flits.io
    options:
      members:
        - inspect_filterbank
        - load_filterbank_data
        - load_stokes_data
        - reader_supports_stokes
        - list_readers

::: flits.io.reader.BurstReader

::: flits.io.reader.StokesBurstReader

::: flits.io.reader.FilterbankInspection

::: flits.stokes
    options:
      members:
        - normalize_polarization_basis
        - basis_from_psrfits
        - stokes_from_products

## Analysis

::: flits.analysis.localization.localize_burst

::: flits.measurements.compute_burst_measurements

::: flits.analysis.dm_optimization.optimize_dm_trials

::: flits.analysis.morphology.compute_width_analysis

::: flits.analysis.temporal.core.run_temporal_structure_analysis

::: flits.analysis.spectral.core.run_averaged_spectral_analysis

::: flits.analysis.temporal.multiscale.haar_excess_power

::: flits.analysis.temporal.multiscale.excess_power_fraction_below

::: flits.analysis.drift.run_drift_analysis

::: flits.analysis.drift.DriftAnalysisInputs

::: flits.models.DriftAnalysisSettings

::: flits.models.DriftAnalysisResult

::: flits.analysis.drift.time_frequency_slope

::: flits.analysis.drift.dm_slope_sensitivity

::: flits.analysis.drift.drift_dm_sensitivity

::: flits.analysis.drift.dm_equivalent_of_slope

## Polarization

::: flits.models.PolarizationSettings

::: flits.models.PolarizationAnalysisResult

::: flits.analysis.polarization.extract_normalized_linear_spectrum

::: flits.analysis.polarization.run_rm_synthesis

## Signal primitives

::: flits.signal
    options:
      members:
        - dedisperse
        - dedispersion_shift_bins
        - dedispersion_edge_bins
        - dedispersion_bin_resolution
        - shift_channels
        - normalize
        - normalize_stokes
        - block_reduce_mean
        - radiometer

## Configuration

::: flits.settings
    options:
      members:
        - ObservationConfig
        - TelescopePreset
        - available_presets
        - detect_preset
        - get_preset

## Command line

::: flits.cli
    options:
      members:
        - main
        - replay
        - serve
