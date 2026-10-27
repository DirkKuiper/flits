# Uncertainties

Every uncertainty FLITS reports carries a short label saying what kind of
number it is, a sentence on what it includes, and a note on how it was
computed. When a known term is missing, such as the SEFD calibration
uncertainty, the explanation says how to add it.

## Kinds of uncertainty

| Label in the interface | `classification` | What the number is |
| --- | --- | --- |
| 1σ | `formal_1sigma` | A 1σ uncertainty including every term FLITS knows about, for example a fluence's radiometer noise and SEFD calibration uncertainty combined in quadrature. |
| 1σ statistical | `statistical_only` | A 1σ statistical uncertainty: radiometer noise, or the scatter of Monte Carlo trials. A known systematic, such as the SEFD calibration uncertainty, was not supplied and is not included. |
| 1σ fit | `model_hessian` | A 1σ uncertainty from a model fit's covariance: burst-model parameters and the PSD power law. It assumes the model describes the data. |
| resolution | `resolution_limit` | Half a time or frequency bin, for quantities read off the sampling grid such as the peak-bin arrival time and the ACF widths. It bounds the error; it is not a standard deviation. |
| fit width | `heuristic_local_fit` | The width of the fitted peak of a DM sweep's score (integrated S/N or DMphase): how sharply the metric peaks. |
| range | `conservative_bound` | The [smoothed-structure DM range](../analysis/dm-optimization.md#smoothed-structure-sutinjo-et-al-2023): the DMs the noise cannot distinguish from the best one. It is wide, several times the scatter of the best DM. |
| approximate | `diagnostic_only` | An approximate interval, such as the PSD crossover frequency's 3σ range, or a value from an older session that did not record how it was computed. |

The three 1σ kinds also fill the plain uncertainty fields, such as
`results.uncertainties.fluence_jyms` and the `*_err` fields of the PSD fit. The
others appear in the uncertainty details and the export columns below.

## Quoting a statistical uncertainty

A statistical uncertainty is a real 1σ error. In the guided workflow the
fluence is 4.032 ± 0.058 Jy ms from radiometer noise, because the example has
no SEFD uncertainty. You can quote it as a statistical error and state the
calibration uncertainty separately, or supply the SEFD uncertainty so FLITS
combines the two. The SEFD term usually dominates.

## Adding a missing term

| Quantity | Term | Interface | Python | `flits measure` |
| --- | --- | --- | --- | --- |
| Peak flux, fluence | SEFD calibration | **SEFD Frac. Uncertainty** under Acquisition Overrides | `sefd_fractional_uncertainty=` in `BurstSession.from_file` | `--sefd-uncertainty` |
| Isotropic energy | SEFD and distance | Both fractional uncertainties; distance is under Source Context | `sefd_fractional_uncertainty=`, `distance_fractional_uncertainty=` | – |
| Drift rate | DM | **DM uncertainty** in the Drift tab | `run_drift_analysis(dm_uncertainty_pc_cm3=...)` | – |

Values are fractional: `0.1` means a 10% SEFD uncertainty.

## Where to find them

- **Interface:** the label sits next to each value. Hover over it to see what
  the uncertainty includes and how it was computed. A "Check" line lists
  problems with the data that affect it, such as low S/N or heavy masking.
- **Python:** `results.uncertainty_details[name]` holds the `value`, `units`,
  `classification`, `basis` (how it was computed), `tooltip` (what it means),
  and `warning_flags`. The 1σ kinds also fill `results.uncertainties.<name>`.
- **CSV export:** each quantity has `<name>_uncertainty_value`,
  `<name>_uncertainty_class` and `<name>_uncertainty_basis` columns.
- **JSON export and session snapshots:** the full details.

## Older fields

Uncertainty details still contain `is_formal_1sigma` and `publishable` so that
older sessions and scripts keep working. Both are deprecated: the
`classification` and `warning_flags` carry the same information.
