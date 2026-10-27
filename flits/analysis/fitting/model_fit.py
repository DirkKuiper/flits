"""Model fits of the selected event with FLITS's native fitburst model.

The session layer passes in an already cropped/reduced dynamic spectrum for
the selected frequency band. This module normalizes each channel against
off-pulse bins, fits the fitburst model (Fonseca et al. 2024) to the selected
event window with :mod:`flits.analysis.fitting.least_squares`, and returns
model-based intrinsic width and scattering-time diagnostics. Parameters,
solution JSON and fit statistics use fitburst's names and units.

A fit that cannot be constrained returns a structured :class:`ModelFitResult`
failure instead of raising.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from flits.analysis.fitting.burst_model import BurstModel, BurstParameters
from flits.analysis.fitting.least_squares import (
    BurstFitResult,
    channel_weights,
    compare_fit_statistics,
    fit_burst_model,
)
from flits.models import ModelComparisonDiagnostics, ModelComparisonRow, ModelFitDiagnostics, UncertaintyDetail

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
    "compare_component_counts",
    "fit_model_selected_band",
]

FITTER_NAME = "flits"
MIN_FIT_CHANNELS = 4
MIN_FIT_TIME_BINS = 16
MIN_WEIGHT_BINS = 8
MAX_FAILURE_LOG_CHARS = 4000
# The native fitter handles a few million samples in seconds; the cap keeps an
# interactive request from tying up the server.
MAX_MODEL_FIT_CELLS = 2_000_000
MAX_MODEL_FIT_DIAGNOSTIC_CELLS = 120_000
MAX_MODEL_FIT_DIAGNOSTIC_FREQ_BINS = 256
MAX_MODEL_FIT_DIAGNOSTIC_TIME_BINS = 1024
MAX_MODEL_FIT_FUNCTION_EVALUATIONS = 50_000
DEFAULT_FREE_PARAMETERS = ("amplitude", "arrival_time", "burst_width", "scattering_timescale")
DEFAULT_FIXED_PARAMETERS = ("dm", "dm_index", "scattering_index", "spectral_index", "spectral_running")
FIT_PARAMETERS = DEFAULT_FREE_PARAMETERS + DEFAULT_FIXED_PARAMETERS
GLOBAL_PARAMETERS = ("dm", "dm_index", "scattering_timescale", "scattering_index")
NON_FITTABLE_PARAMETERS = ("ref_freq",)
SCINTILLATION_INACTIVE_PARAMETERS = ("amplitude", "spectral_index", "spectral_running")
WEIGHTING_MODES = ("none", "auto", "fit_window", "manual_range")
# Automatic time upsampling: a component narrower than this many time bins is
# refitted with sub-samples no wider than 1/AUTO_SUBSAMPLES_PER_WIDTH of its
# width, so the model is averaged over each bin as the data are.
AUTO_SUBSAMPLES_PER_WIDTH = 3
MAX_AUTO_TIME_UPSAMPLE = 16
MAX_AUTO_UPSAMPLE_ROUNDS = 3
# Upsampled samples per evaluation stay below this, which keeps a fit to seconds.
MAX_UPSAMPLED_FIT_SAMPLES = 16_000_000
# The fit window extends the event window by its own length on each side, and
# by at least this many bins, so the baseline and any scattering tail are fitted.
MIN_FIT_PADDING_BINS = 8
MAX_COMPARISON_COMPONENTS = 5


def _model_fit_uncertainty_detail(
    *,
    value: float | None,
    units: str,
    parameter_label: str,
    covariance_basis: str | None = "observed_hessian",
    at_lower_bound: bool = False,
    below_time_resolution: bool = False,
) -> UncertaintyDetail:
    if covariance_basis == "gauss_newton":
        source = (
            "the inverse of the Gauss-Newton matrix J^T J, used because the observed Hessian was not positive definite"
        )
    else:
        source = "the inverse of the observed Hessian of chi-squared, the covariance fitburst reports"
    basis = f"{parameter_label} uncertainty from {source}, at the best fit of the fitburst model."
    tooltip = (
        "1σ from the model fit's covariance, not scaled by the reduced chi-squared. It assumes the burst model "
        "describes the data, so it can differ from the model-independent FLITS measurements."
    )
    warning_flags = ["model_fit_hessian"]
    if at_lower_bound:
        tooltip += (
            " The fit ended at the lower bound of zero, so the interval is one-sided: quote the value plus this "
            "uncertainty as an upper limit."
        )
        warning_flags.append("at_lower_bound")
    elif below_time_resolution:
        tooltip += (
            " It is shorter than one time bin: the model is averaged over each bin, so the data constrain it only "
            "through how the burst's flux spreads into neighbouring bins."
        )
        warning_flags.append("below_time_resolution")
    if value is None:
        warning_flags.append("uncertainty_unavailable")
    return UncertaintyDetail(
        value=None if value is None else float(value),
        units=units,
        classification="model_hessian",
        is_formal_1sigma=False,
        publishable=False,
        basis=basis,
        tooltip=tooltip,
        warning_flags=warning_flags,
    )


@dataclass(frozen=True)
class ModelFitRequestConfig:
    """Configuration for a selected-event model-fit request."""

    num_components: int = 1
    free_parameters: list[str] = field(default_factory=lambda: list(DEFAULT_FREE_PARAMETERS))
    initial_parameters: dict[str, list[float]] | None = None
    initial_parameter_source: str = "current_selection"
    weighting_mode: str = "none"
    weight_range: list[int] | None = None
    iterations: int = 1
    # None chooses the factor from the fitted width (see AUTO_SUBSAMPLES_PER_WIDTH).
    factor_time_upsample: int | None = None
    factor_freq_upsample: int = 1
    ref_freq_mhz: float | None = None
    is_folded: bool = False
    exact_jacobian: bool = True
    max_function_evaluations: int | None = None
    scintillation: bool = False
    # Bins added to each side of the event window; None pads by the event length.
    fit_window_padding_bins: int | None = None
    fixed_parameters: list[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scintillation", _coerce_bool(self.scintillation, default=False))
        free_parameters = _validate_free_parameters(self.free_parameters, scintillation=self.scintillation)
        object.__setattr__(self, "free_parameters", free_parameters)
        object.__setattr__(
            self, "fixed_parameters", _fixed_parameters_from_free(free_parameters, scintillation=self.scintillation)
        )
        object.__setattr__(
            self,
            "initial_parameter_source",
            _coerce_initial_parameter_source(self.initial_parameter_source, self.initial_parameters),
        )
        object.__setattr__(self, "weighting_mode", _coerce_weighting_mode(self.weighting_mode))
        object.__setattr__(self, "weight_range", _coerce_weight_range(self.weight_range))
        if self.weighting_mode == "manual_range" and self.weight_range is None:
            raise ValueError("manual_range weighting requires a two-bin weight_range.")
        object.__setattr__(self, "iterations", _coerce_iterations(self.iterations))
        object.__setattr__(self, "factor_time_upsample", _coerce_optional_positive_int(self.factor_time_upsample))
        object.__setattr__(self, "factor_freq_upsample", _coerce_positive_int(self.factor_freq_upsample))
        object.__setattr__(self, "ref_freq_mhz", _coerce_ref_freq_mhz(self.ref_freq_mhz))
        object.__setattr__(self, "is_folded", _coerce_bool(self.is_folded, default=False))
        object.__setattr__(self, "exact_jacobian", _coerce_bool(self.exact_jacobian, default=True))
        object.__setattr__(
            self, "max_function_evaluations", _coerce_max_function_evaluations(self.max_function_evaluations)
        )
        object.__setattr__(self, "fit_window_padding_bins", _coerce_padding_bins(self.fit_window_padding_bins))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible request payload."""
        return {
            "num_components": int(self.num_components),
            "free_parameters": list(self.free_parameters),
            "initial_parameters": self.initial_parameters,
            "initial_parameter_source": _coerce_initial_parameter_source(
                self.initial_parameter_source, self.initial_parameters
            ),
            "solver": {
                "weighting_mode": _coerce_weighting_mode(self.weighting_mode),
                "weight_range": None if self.weight_range is None else [int(value) for value in self.weight_range],
                "iterations": _coerce_iterations(self.iterations),
                "factor_time_upsample": _coerce_optional_positive_int(self.factor_time_upsample),
                "factor_freq_upsample": _coerce_positive_int(self.factor_freq_upsample),
                "ref_freq_mhz": _coerce_ref_freq_mhz(self.ref_freq_mhz),
                "is_folded": bool(self.is_folded),
                "exact_jacobian": bool(self.exact_jacobian),
                "max_function_evaluations": _coerce_max_function_evaluations(self.max_function_evaluations),
                "fit_window_padding_bins": _coerce_padding_bins(self.fit_window_padding_bins),
            },
            "scintillation": bool(self.scintillation),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ModelFitRequestConfig:
        """Build a request config from a web/API payload."""
        if payload is None:
            return cls()
        payload = dict(payload)
        solver = payload.get("solver")
        if not isinstance(solver, dict):
            solver = {}
        solution = payload.get("solution")
        initial_parameters = payload.get("initial_parameters")
        initial_parameter_source = payload.get("initial_parameter_source")
        if initial_parameters is None and isinstance(solution, dict):
            initial_parameters = solution.get("model_parameters")
            if initial_parameters is not None:
                initial_parameter_source = "imported_solution"
        try:
            num_components = int(payload.get("num_components", 1))
        except (TypeError, ValueError):
            num_components = 1
        return cls(
            num_components=max(1, num_components),
            free_parameters=payload.get("free_parameters", list(DEFAULT_FREE_PARAMETERS)),
            initial_parameters=initial_parameters,
            initial_parameter_source=_coerce_initial_parameter_source(initial_parameter_source, initial_parameters),
            weighting_mode=solver.get("weighting_mode", payload.get("weighting_mode", "none")),
            weight_range=_coerce_weight_range(solver.get("weight_range", payload.get("weight_range"))),
            iterations=_coerce_iterations(solver.get("iterations", payload.get("iterations", 1))),
            factor_time_upsample=_coerce_optional_positive_int(
                solver.get("factor_time_upsample", payload.get("factor_time_upsample"))
            ),
            factor_freq_upsample=_coerce_positive_int(
                solver.get("factor_freq_upsample", payload.get("factor_freq_upsample"))
            ),
            ref_freq_mhz=_coerce_ref_freq_mhz(solver.get("ref_freq_mhz", payload.get("ref_freq_mhz"))),
            is_folded=_coerce_bool(solver.get("is_folded", payload.get("is_folded")), default=False),
            exact_jacobian=_coerce_bool(solver.get("exact_jacobian", payload.get("exact_jacobian")), default=True),
            max_function_evaluations=_coerce_max_function_evaluations(
                solver.get("max_function_evaluations", payload.get("max_function_evaluations"))
            ),
            scintillation=_coerce_bool(payload.get("scintillation"), default=False),
            fit_window_padding_bins=_coerce_padding_bins(
                solver.get("fit_window_padding_bins", payload.get("fit_window_padding_bins"))
            ),
        )


@dataclass(frozen=True)
class ModelFitResult:
    """Result of a selected-event model fit.

    Parameters
    ----------
    status
        Machine-readable fit status. ``"ok"`` indicates a converged fit; other
        values describe structured failure modes.
    message
        Human-readable solver or adapter message, when available.
    width_ms_model
        Fitted intrinsic burst width for the first component, in milliseconds.
        Full component arrays are stored in ``diagnostics.bestfit_parameters``.
    width_uncertainty_ms
        Model-fit uncertainty for ``width_ms_model``, in milliseconds.
    tau_sc_ms
        Fitted scattering timescale for the first component, in milliseconds.
    tau_uncertainty_ms
        Model-fit uncertainty for ``tau_sc_ms``, in milliseconds.
    diagnostics
        Full diagnostic payload, including parameter dictionaries, fit
        statistics, model/data dynamic spectra, and residual profiles.
    """

    status: str
    message: str | None
    width_ms_model: float | None
    width_uncertainty_ms: float | None
    tau_sc_ms: float | None
    tau_uncertainty_ms: float | None
    diagnostics: ModelFitDiagnostics


def fit_model_selected_band(
    *,
    selected_band: np.ndarray,
    freqs_mhz: np.ndarray,
    time_axis_ms: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    offpulse_bins: np.ndarray,
    tsamp_ms: float,
    peak_rel_bin: int | None,
    width_guess_ms: float | None,
    config: ModelFitRequestConfig | None = None,
) -> ModelFitResult:
    """Fit intrinsic width and scattering time for a selected dynamic spectrum.

    Parameters
    ----------
    selected_band
        Dynamic spectrum with shape ``(n_channels, n_time)``. Rows correspond
        to the selected frequency channels and columns to the current
        crop/reduced time grid. Non-finite samples are replaced by each
        channel's baseline during normalization.
    freqs_mhz
        Frequency axis in MHz with length ``n_channels``. The order should
        match ``selected_band`` rows.
    time_axis_ms
        Time axis in milliseconds with length ``n_time``. The fitted event
        window is converted to seconds, fitburst's unit, for the model.
    event_rel_start, event_rel_end
        Half-open event window, in selected-band time-bin coordinates. Only
        this interval is fitted.
    offpulse_bins
        Time-bin indices used as the per-channel reference for baseline and
        noise estimation. If empty, the full channel is used as the reference.
    tsamp_ms
        Effective time resolution of the selected/reduced grid, in
        milliseconds.
    peak_rel_bin
        Optional peak bin in selected-band coordinates. If omitted, the peak is
        estimated from the mean normalized event profile.
    width_guess_ms
        Optional initial intrinsic-width guess in milliseconds. If omitted or
        non-finite, the adapter seeds the width from the event duration and
        time resolution.
    config
        Optional model-fit request configuration. Defaults to
        :class:`ModelFitRequestConfig`.

    Returns
    -------
    ModelFitResult
        Structured fit result. Possible ``status`` values are ``"ok"``,
        ``"insufficient_data"``, ``"insufficient_time_bins"``,
        ``"insufficient_channels"``, ``"fit_window_too_large"``,
        ``"insufficient_signal"``, ``"invalid_config"``,
        ``"evaluation_limit_exceeded"`` and ``"fit_failed"``.

    Notes
    -----
    The scalar width and scattering-time fields report the first component.
    For multi-component fits, inspect ``diagnostics.bestfit_parameters`` for
    the full parameter arrays.
    """
    if config is None:
        config = ModelFitRequestConfig()
    fit_iterations = _coerce_iterations(config.iterations)

    data = np.asarray(selected_band, dtype=float)
    freqs = np.asarray(freqs_mhz, dtype=float)
    time_axis_ms = np.asarray(time_axis_ms, dtype=float)
    offpulse_bins = np.asarray(offpulse_bins, dtype=int)

    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
        return _failed_result(
            status="insufficient_data",
            message="No selected-band data are available for fitting.",
            component_count=config.num_components,
            free_parameters=config.free_parameters,
            initial_parameter_source=config.initial_parameter_source,
            fixed_parameters=config.fixed_parameters,
            scintillation=config.scintillation,
            weighting_mode=config.weighting_mode,
            fit_iterations_requested=fit_iterations,
            fit_iterations_completed=0,
            is_folded=config.is_folded,
            exact_jacobian=config.exact_jacobian,
            max_function_evaluations=config.max_function_evaluations,
        )
    if data.shape[1] < MIN_FIT_TIME_BINS:
        return _failed_result(
            status="insufficient_time_bins",
            message=f"At least {MIN_FIT_TIME_BINS} time bins are required for model fits.",
            component_count=config.num_components,
            free_parameters=config.free_parameters,
            initial_parameter_source=config.initial_parameter_source,
            fixed_parameters=config.fixed_parameters,
            scintillation=config.scintillation,
            weighting_mode=config.weighting_mode,
            fit_iterations_requested=fit_iterations,
            fit_iterations_completed=0,
            is_folded=config.is_folded,
            exact_jacobian=config.exact_jacobian,
            max_function_evaluations=config.max_function_evaluations,
        )
    fit_start, fit_end = _fit_window(
        event_rel_start=int(event_rel_start),
        event_rel_end=int(event_rel_end),
        num_time=int(data.shape[1]),
        num_channels=int(data.shape[0]),
        padding_bins=config.fit_window_padding_bins,
    )
    fit_cells = int(data.shape[0]) * (fit_end - fit_start)
    if fit_cells > MAX_MODEL_FIT_CELLS:
        return _failed_result(
            status="fit_window_too_large",
            message=(
                f"The selected model-fit window has {fit_cells:,} channel-time samples. "
                f"Reduce the event window or increase time/frequency downsampling before fitting "
                f"(interactive limit: {MAX_MODEL_FIT_CELLS:,})."
            ),
            component_count=config.num_components,
            free_parameters=config.free_parameters,
            initial_parameter_source=config.initial_parameter_source,
            fixed_parameters=config.fixed_parameters,
            scintillation=config.scintillation,
            weighting_mode=config.weighting_mode,
            fit_iterations_requested=fit_iterations,
            fit_iterations_completed=0,
            is_folded=config.is_folded,
            exact_jacobian=config.exact_jacobian,
            max_function_evaluations=config.max_function_evaluations,
        )

    normalized_data, good_freq = _normalize_dynamic_spectrum(data, offpulse_bins)
    good_channel_count = int(np.count_nonzero(good_freq))
    if good_channel_count < MIN_FIT_CHANNELS:
        return _failed_result(
            status="insufficient_channels",
            message=f"At least {MIN_FIT_CHANNELS} unmasked channels are required for model fits.",
            component_count=config.num_components,
            free_parameters=config.free_parameters,
            initial_parameter_source=config.initial_parameter_source,
            fixed_parameters=config.fixed_parameters,
            scintillation=config.scintillation,
            weighting_mode=config.weighting_mode,
            fit_iterations_requested=fit_iterations,
            fit_iterations_completed=0,
            is_folded=config.is_folded,
            exact_jacobian=config.exact_jacobian,
            max_function_evaluations=config.max_function_evaluations,
        )

    event_slice = np.asarray(
        np.nanmean(normalized_data[good_freq, event_rel_start:event_rel_end], axis=0),
        dtype=float,
    )
    if event_slice.size == 0 or not np.isfinite(event_slice).any():
        return _failed_result(
            status="insufficient_signal",
            message="The selected event window does not contain a stable signal for fitting.",
            component_count=config.num_components,
            free_parameters=config.free_parameters,
            initial_parameter_source=config.initial_parameter_source,
            fixed_parameters=config.fixed_parameters,
            scintillation=config.scintillation,
            weighting_mode=config.weighting_mode,
            fit_iterations_requested=fit_iterations,
            fit_iterations_completed=0,
            is_folded=config.is_folded,
            exact_jacobian=config.exact_jacobian,
            max_function_evaluations=config.max_function_evaluations,
        )

    if peak_rel_bin is None:
        peak_rel_bin = int(event_rel_start + np.nanargmax(event_slice))
    peak_rel_bin = max(0, min(int(peak_rel_bin), data.shape[1] - 1))

    initial_parameters = _initial_parameters(
        normalized_data=normalized_data,
        freqs_mhz=freqs,
        time_axis_ms=time_axis_ms,
        tsamp_ms=float(tsamp_ms),
        peak_rel_bin=peak_rel_bin,
        event_rel_start=int(event_rel_start),
        event_rel_end=int(event_rel_end),
        width_guess_ms=width_guess_ms,
        num_components=config.num_components,
    )

    if config.initial_parameters:
        initial_parameters = _apply_initial_parameter_overrides(
            defaults=initial_parameters,
            overrides=config.initial_parameters,
            num_components=config.num_components,
        )
    ref_freq_mhz = _coerce_ref_freq_mhz(config.ref_freq_mhz)
    if ref_freq_mhz is not None:
        effective_ref_freq_mhz = float(ref_freq_mhz)
        initial_parameters["ref_freq"] = [effective_ref_freq_mhz] * config.num_components
    else:
        ref_values = np.asarray(initial_parameters.get("ref_freq", []), dtype=float).ravel()
        finite_refs = ref_values[np.isfinite(ref_values) & (ref_values > 0)]
        effective_ref_freq_mhz = float(finite_refs[0] if finite_refs.size else np.min(freqs))
        if finite_refs.size == 0:
            initial_parameters["ref_freq"] = [effective_ref_freq_mhz] * config.num_components
    factor_freq_upsample = _coerce_positive_int(config.factor_freq_upsample)
    time_upsample_basis = "auto" if config.factor_time_upsample is None else "requested"
    factor_time_upsample = 1 if config.factor_time_upsample is None else int(config.factor_time_upsample)

    fit_data = np.array(normalized_data[:, fit_start:fit_end], dtype=float, copy=True)
    fit_time_axis_ms = time_axis_ms[fit_start:fit_end]
    times_sec = (fit_time_axis_ms - float(time_axis_ms[0])) / 1e3
    fit_window_ms = [float(time_axis_ms[fit_start]), float(time_axis_ms[fit_end - 1] + float(tsamp_ms))]
    weighting_mode = _coerce_weighting_mode(config.weighting_mode)
    # A manual weight range is given in event-window bins.
    weight_range = _coerce_weight_range(config.weight_range, int(event_rel_end) - int(event_rel_start))
    event_offset = int(event_rel_start) - fit_start
    weight_range_basis: str | None = "none"
    context: dict[str, Any] = {
        "initial_parameters": initial_parameters,
        "free_parameters": config.free_parameters,
        "initial_parameter_source": config.initial_parameter_source,
        "fixed_parameters": config.fixed_parameters,
        "component_count": config.num_components,
        "scintillation": config.scintillation,
        "weighting_mode": weighting_mode,
        "fit_iterations_requested": fit_iterations,
        "factor_time_upsample": factor_time_upsample,
        "time_upsample_basis": time_upsample_basis,
        "factor_freq_upsample": factor_freq_upsample,
        "ref_freq_mhz": effective_ref_freq_mhz,
        "is_folded": config.is_folded,
        "exact_jacobian": config.exact_jacobian,
        "fit_window_ms": fit_window_ms,
        "time_resolution_ms": float(tsamp_ms),
    }
    if weighting_mode == "auto":
        offpulse_weights, offpulse_range = _offpulse_channel_weights(
            normalized_data=normalized_data,
            good_freq=good_freq,
            offpulse_bins=offpulse_bins,
        )
        if offpulse_weights is not None:
            weights = offpulse_weights
            weight_range = offpulse_range
            weight_range_basis = "offpulse_bins"
        else:
            weights = channel_weights(fit_data, good_freq, weighted=True)
            weight_range_basis = "fit_window"
    elif weighting_mode == "fit_window":
        weights = channel_weights(fit_data, good_freq, weighted=True)
        weight_range_basis = "fit_window"
    elif weighting_mode == "manual_range":
        if weight_range is None:
            return _failed_result(
                status="invalid_config",
                message="manual_range weighting requires a valid two-bin weight_range within the event window.",
                weight_range=None,
                weight_range_basis="invalid",
                fit_iterations_completed=0,
                max_function_evaluations=config.max_function_evaluations,
                **context,
            )
        weights = channel_weights(
            fit_data,
            good_freq,
            weighted=True,
            weight_range=[weight_range[0] + event_offset, weight_range[1] + event_offset],
        )
        weight_range_basis = "manual_range"
    else:
        weights = channel_weights(fit_data, good_freq, weighted=False)
    context.update({"weight_range": weight_range, "weight_range_basis": weight_range_basis})

    def burst_model(factor: int) -> BurstModel:
        return BurstModel(
            freqs,
            times_sec,
            factor_freq_upsample=factor_freq_upsample,
            factor_time_upsample=factor,
            is_folded=config.is_folded,
            scintillation=config.scintillation,
        )

    try:
        model = burst_model(factor_time_upsample)
        start = BurstParameters.from_dict(initial_parameters, config.num_components)
    except ValueError as exc:
        return _failed_result(
            status="invalid_config",
            message=str(exc),
            fit_iterations_completed=0,
            max_function_evaluations=config.max_function_evaluations,
            **context,
        )

    def run_passes(model: BurstModel, current: BurstParameters) -> tuple[BurstFitResult, int] | ModelFitResult:
        """Run the requested optimizer passes, each from the previous best fit."""
        completed_iterations = 0
        fit: BurstFitResult | None = None
        for _iteration_index in range(fit_iterations):
            try:
                fit = fit_burst_model(
                    model,
                    fit_data,
                    current,
                    config.free_parameters,
                    weights=weights,
                    exact_jacobian=config.exact_jacobian,
                    max_function_evaluations=config.max_function_evaluations,
                )
            except ValueError as exc:
                return _failed_result(
                    status="invalid_config",
                    message=str(exc),
                    bestfit_parameters=current.to_dict() if completed_iterations else {},
                    fit_iterations_completed=completed_iterations,
                    max_function_evaluations=config.max_function_evaluations,
                    **context,
                )
            except Exception as exc:
                return _failed_result(
                    status="fit_failed",
                    message="The model fit raised an exception.",
                    bestfit_parameters=current.to_dict() if completed_iterations else {},
                    fit_iterations_completed=completed_iterations,
                    max_function_evaluations=config.max_function_evaluations,
                    failure_exception=_sanitize_log(f"{type(exc).__name__}: {exc}"),
                    **context,
                )
            if not fit.success:
                return _failed_result(
                    status="evaluation_limit_exceeded" if fit.status == 0 else "fit_failed",
                    message=fit.message or "The model fit could not converge for the current selection.",
                    bestfit_parameters=current.to_dict() if completed_iterations else {},
                    fit_statistics=fit.fit_statistics,
                    fit_parameters=list(fit.layout.fit_parameters),
                    fit_iterations_completed=completed_iterations,
                    max_function_evaluations=fit.max_function_evaluations,
                    function_evaluations=fit.function_evaluations,
                    solver_status=fit.status,
                    solver_message=fit.message,
                    **context,
                )
            current = fit.parameters
            completed_iterations += 1
        assert fit is not None
        return fit, completed_iterations

    outcome = run_passes(model, start)
    if isinstance(outcome, ModelFitResult):
        return outcome
    fit, completed_iterations = outcome
    if time_upsample_basis == "auto":
        # A model narrower than a time bin must be averaged over the bin, as the
        # data are; refit from the solution on a fine enough grid.
        for _round in range(MAX_AUTO_UPSAMPLE_ROUNDS):
            needed = _auto_time_upsample(
                fit.parameters, time_resolution_s=float(tsamp_ms) / 1e3, current=factor_time_upsample, cells=fit_cells
            )
            if needed <= factor_time_upsample:
                break
            factor_time_upsample = needed
            context["factor_time_upsample"] = needed
            outcome = run_passes(burst_model(needed), fit.parameters)
            if isinstance(outcome, ModelFitResult):
                return outcome
            fit, completed_iterations = outcome
    assert fit.model is not None

    full_best_parameters: dict[str, list[float] | None] = dict(fit.parameters.to_dict())
    bestfit_uncertainties: dict[str, list[float] | None] = dict(fit.bestfit_uncertainties)
    model_dynamic_spectrum = np.array(fit.model, dtype=float, copy=True)

    data_profile = np.nanmean(fit_data[good_freq, :], axis=0)
    model_profile = np.mean(model_dynamic_spectrum[good_freq, :], axis=0)
    residual_profile = data_profile - model_profile

    data_freq_profile = np.nanmean(fit_data, axis=1)
    model_freq_profile = np.mean(model_dynamic_spectrum, axis=1)
    residual_freq_profile = data_freq_profile - model_freq_profile

    bad_freq = ~good_freq
    fit_data[bad_freq, :] = np.nan
    model_dynamic_spectrum[bad_freq, :] = np.nan
    data_freq_profile[bad_freq] = np.nan
    model_freq_profile[bad_freq] = np.nan
    residual_freq_profile[bad_freq] = np.nan
    residual_dynamic_spectrum = fit_data - model_dynamic_spectrum

    diag_freq_idx, diag_time_idx = _diagnostic_grid_indices(fit_data.shape[0], fit_data.shape[1])
    diag_data = np.asarray(fit_data[np.ix_(diag_freq_idx, diag_time_idx)], dtype=float)
    diag_model = np.asarray(model_dynamic_spectrum[np.ix_(diag_freq_idx, diag_time_idx)], dtype=float)
    diag_residual = np.asarray(residual_dynamic_spectrum[np.ix_(diag_freq_idx, diag_time_idx)], dtype=float)
    channel_amplitudes = np.empty((0, 0), dtype=float)
    if fit.channel_amplitudes is not None:
        channel_amplitudes = np.array(fit.channel_amplitudes[diag_freq_idx], dtype=float, copy=True)
        channel_amplitudes[bad_freq[diag_freq_idx]] = np.nan
    fit_statistics = dict(fit.fit_statistics)
    fit_statistics.update(
        {
            "function_evaluations": fit.function_evaluations,
            "jacobian_evaluations": fit.jacobian_evaluations,
            "max_function_evaluations": fit.max_function_evaluations,
            "solver_status": fit.status,
            "diagnostic_num_freq": int(diag_freq_idx.size),
            "diagnostic_num_time": int(diag_time_idx.size),
            "diagnostic_num_cells": int(diag_data.size),
        }
    )

    width_sec = _parameter_value(full_best_parameters, "burst_width")
    tau_sec = _parameter_value(full_best_parameters, "scattering_timescale")
    width_uncertainty_sec = _parameter_value(bestfit_uncertainties, "burst_width")
    tau_uncertainty_sec = _parameter_value(bestfit_uncertainties, "scattering_timescale")
    resolution_flags = _resolution_flags(fit, time_resolution_ms=float(tsamp_ms))
    uncertainty_details = {
        "width_ms_model": _model_fit_uncertainty_detail(
            value=None if width_uncertainty_sec is None else float(width_uncertainty_sec * 1e3),
            units="ms",
            parameter_label="Intrinsic width",
            covariance_basis=fit.covariance_basis,
            at_lower_bound="burst_width1" in fit.parameters_at_bounds,
            below_time_resolution=width_sec is not None and width_sec * 1e3 < float(tsamp_ms),
        ),
        "tau_sc_ms": _model_fit_uncertainty_detail(
            value=None if tau_uncertainty_sec is None else float(tau_uncertainty_sec * 1e3),
            units="ms",
            parameter_label="Scattering timescale",
            covariance_basis=fit.covariance_basis,
            at_lower_bound="scattering_timescale" in fit.parameters_at_bounds,
            below_time_resolution="scattering_below_time_resolution" in resolution_flags,
        ),
    }

    diagnostics = ModelFitDiagnostics(
        status="ok",
        message=fit.message,
        fitter=FITTER_NAME,
        component_count=config.num_components,
        fit_parameters=list(fit.layout.fit_parameters),
        free_parameters=config.free_parameters,
        initial_parameter_source=config.initial_parameter_source,
        fixed_parameters=config.fixed_parameters,
        scintillation=config.scintillation,
        weighting_mode=weighting_mode,
        weight_range=weight_range,
        weight_range_basis=weight_range_basis,
        fit_iterations_requested=fit_iterations,
        fit_iterations_completed=completed_iterations,
        factor_time_upsample=factor_time_upsample,
        time_upsample_basis=time_upsample_basis,
        factor_freq_upsample=factor_freq_upsample,
        ref_freq_mhz=effective_ref_freq_mhz,
        is_folded=config.is_folded,
        exact_jacobian=config.exact_jacobian,
        fit_window_ms=fit_window_ms,
        time_resolution_ms=float(tsamp_ms),
        resolution_flags=resolution_flags,
        max_function_evaluations=fit.max_function_evaluations,
        function_evaluations=fit.function_evaluations,
        solver_status=fit.status,
        solver_message=fit.message,
        initial_parameters=_copy_parameter_dict(initial_parameters),
        bestfit_parameters=full_best_parameters,
        bestfit_uncertainties=bestfit_uncertainties,
        fit_statistics=_sanitize_fit_statistics(fit_statistics),
        parameter_labels=list(fit.labels),
        covariance=np.empty((0, 0), dtype=float) if fit.covariance is None else np.asarray(fit.covariance),
        covariance_basis=fit.covariance_basis,
        parameters_at_bounds=list(fit.parameters_at_bounds),
        freq_axis_mhz=np.asarray(freqs[diag_freq_idx], dtype=float),
        time_axis_ms=np.asarray(fit_time_axis_ms[diag_time_idx], dtype=float),
        data_dynamic_spectrum_sn=diag_data,
        model_dynamic_spectrum_sn=diag_model,
        residual_dynamic_spectrum_sn=diag_residual,
        data_profile_sn=np.asarray(data_profile[diag_time_idx], dtype=float),
        model_profile_sn=np.asarray(model_profile[diag_time_idx], dtype=float),
        residual_profile_sn=np.asarray(residual_profile[diag_time_idx], dtype=float),
        data_freq_profile_sn=np.asarray(data_freq_profile[diag_freq_idx], dtype=float),
        model_freq_profile_sn=np.asarray(model_freq_profile[diag_freq_idx], dtype=float),
        residual_freq_profile_sn=np.asarray(residual_freq_profile[diag_freq_idx], dtype=float),
        channel_amplitudes=channel_amplitudes,
        uncertainty_details=uncertainty_details,
    )
    return ModelFitResult(
        status="ok",
        message=diagnostics.message,
        width_ms_model=None if width_sec is None else float(width_sec * 1e3),
        width_uncertainty_ms=None if width_uncertainty_sec is None else float(width_uncertainty_sec * 1e3),
        tau_sc_ms=None if tau_sec is None else float(tau_sec * 1e3),
        tau_uncertainty_ms=None if tau_uncertainty_sec is None else float(tau_uncertainty_sec * 1e3),
        diagnostics=diagnostics,
    )


def compare_component_counts(
    *,
    selected_band: np.ndarray,
    freqs_mhz: np.ndarray,
    time_axis_ms: np.ndarray,
    event_rel_start: int,
    event_rel_end: int,
    offpulse_bins: np.ndarray,
    tsamp_ms: float,
    peak_rel_bin: int | None,
    width_guess_ms: float | None,
    config: ModelFitRequestConfig | None = None,
    max_components: int = 3,
) -> ModelComparisonDiagnostics:
    """Fit the selection with one to ``max_components`` components and compare the fits.

    The one-component fit starts from the automatic guess at the strongest
    peak. Each further fit starts from the previous solution with one more
    component at the largest positive residual of the event profile, scaled to
    that residual. All fits share the fit window, the weights and the time
    upsampling (chosen by the one-component fit when it is automatic), so their
    chi-squared values compare like with like. The comparison stops at the
    first fit that fails or when no positive residual is left; the fits that
    converged are still compared. A component the data do not support often
    fails to converge, because nothing stops it shrinking onto a noise spike.

    The inputs are those of :func:`fit_model_selected_band`; ``config`` gives
    the free parameters and solver settings, and its component count and
    initial parameters are ignored.
    """
    base = config if config is not None else ModelFitRequestConfig()
    max_components = max(2, min(int(max_components), MAX_COMPARISON_COMPONENTS))
    axis_ms = np.asarray(time_axis_ms, dtype=float)
    event_window_ms = (
        float(axis_ms[int(event_rel_start)]),
        float(axis_ms[max(int(event_rel_start), int(event_rel_end) - 1)] + float(tsamp_ms)),
    )
    rows: list[ModelComparisonRow] = []
    statistics: list[Mapping[str, float | int | None]] = []
    initial: dict[str, list[float]] | None = None
    factor_time_upsample = base.factor_time_upsample
    fit_window_ms: list[float] | None = None
    message: str | None = None
    for count in range(1, max_components + 1):
        trial = replace(
            base,
            num_components=count,
            initial_parameters=initial,
            initial_parameter_source="current_selection" if initial is None else "component_comparison",
            factor_time_upsample=factor_time_upsample,
        )
        result = fit_model_selected_band(
            selected_band=selected_band,
            freqs_mhz=freqs_mhz,
            time_axis_ms=axis_ms,
            event_rel_start=event_rel_start,
            event_rel_end=event_rel_end,
            offpulse_bins=offpulse_bins,
            tsamp_ms=tsamp_ms,
            peak_rel_bin=peak_rel_bin,
            width_guess_ms=width_guess_ms,
            config=trial,
        )
        diagnostics = result.diagnostics
        if result.status != "ok":
            rows.append(ModelComparisonRow(num_components=count, status=result.status, message=result.message))
            reason = (
                "it reached the evaluation limit"
                if result.status == "evaluation_limit_exceeded"
                else str(result.message or result.status).rstrip(".")
            )
            message = (
                f"The {count}-component fit did not converge: {reason}. A component the data do not support "
                "often fails this way; to check, fit it with more evaluations or your own initial guesses."
            )
            break
        factor_time_upsample = diagnostics.factor_time_upsample
        fit_window_ms = diagnostics.fit_window_ms
        statistics.append(diagnostics.fit_statistics)
        rows.append(
            ModelComparisonRow(
                num_components=count,
                status="ok",
                message=result.message,
                chisq_reduced=_float_statistic(diagnostics.fit_statistics.get("chisq_final_reduced")),
                bestfit_parameters=dict(diagnostics.bestfit_parameters),
                bestfit_uncertainties=dict(diagnostics.bestfit_uncertainties),
                parameters_at_bounds=list(diagnostics.parameters_at_bounds),
            )
        )
        if count == max_components:
            break
        initial = _with_component_at_largest_residual(
            diagnostics,
            event_window_ms=event_window_ms,
            time_origin_ms=float(axis_ms[0]),
            min_width_s=float(tsamp_ms) / 1e3,
        )
        if initial is None:
            message = f"No positive residual is left in the event window after {count} components."
            break

    converged = [row for row in rows if row.status == "ok"]
    preferred: int | None = converged[0].num_components if len(converged) == 1 else None
    if len(converged) >= 2:
        comparisons = compare_fit_statistics(statistics)
        for comparison in comparisons:
            row = converged[comparison.index]
            converged[comparison.index] = replace(
                row,
                num_parameters=comparison.num_parameters,
                chisq=comparison.chisq,
                aic=comparison.aic,
                bic=comparison.bic,
                delta_aic=comparison.delta_aic,
                delta_bic=comparison.delta_bic,
                f_test_p_value=comparison.f_test_p_value,
            )
        preferred = min(converged, key=lambda row: row.bic if row.bic is not None else np.inf).num_components
        by_count = {row.num_components: row for row in converged}
        rows = [by_count.get(row.num_components, row) for row in rows]
    status = "ok" if converged else "fit_failed"
    return ModelComparisonDiagnostics(
        status=status,
        message=message,
        max_components=max_components,
        preferred_components=preferred,
        free_parameters=list(base.free_parameters),
        scintillation=base.scintillation,
        factor_time_upsample=factor_time_upsample,
        fit_window_ms=fit_window_ms,
        rows=rows,
    )


def _float_statistic(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _with_component_at_largest_residual(
    diagnostics: ModelFitDiagnostics,
    *,
    event_window_ms: tuple[float, float],
    time_origin_ms: float,
    min_width_s: float,
) -> dict[str, list[float]] | None:
    """The fit's parameters plus a component at the largest positive event-profile residual.

    The new component's amplitude scales the strongest component's by the
    ratio of the residual peak to the model peak, which holds in either
    profile normalization; its width is the median fitted width, and its
    spectrum copies the strongest component's.
    """
    times = np.asarray(diagnostics.time_axis_ms, dtype=float)
    residual = np.asarray(diagnostics.residual_profile_sn, dtype=float)
    model = np.asarray(diagnostics.model_profile_sn, dtype=float)
    inside = (times >= event_window_ms[0]) & (times < event_window_ms[1]) & np.isfinite(residual)
    if times.size != residual.size or not inside.any():
        return None
    index = int(np.flatnonzero(inside)[int(np.argmax(residual[inside]))])
    peak = float(residual[index])
    if not peak > 0:
        return None
    parameters = {
        key: [float(value) for value in values] for key, values in diagnostics.bestfit_parameters.items() if values
    }
    amplitudes = parameters.get("amplitude")
    widths = parameters.get("burst_width")
    if not amplitudes or not widths:
        return None
    strongest = int(np.argmax(amplitudes))
    model_peak = float(np.nanmax(model)) if model.size and np.isfinite(model).any() else 0.0
    amplitude = amplitudes[strongest] + float(np.log10(peak / model_peak)) if model_peak > 0 else float(np.log10(peak))
    added = {
        "amplitude": amplitude,
        "arrival_time": (float(times[index]) - time_origin_ms) / 1e3,
        "burst_width": max(float(np.median(widths)), min_width_s),
    }
    for key, values in parameters.items():
        if key in added:
            values.append(added[key])
        else:
            # Shared parameters repeat; per-component ones copy the strongest component.
            values.append(values[0] if key in GLOBAL_PARAMETERS else values[min(strongest, len(values) - 1)])
    return parameters


def _fit_window(
    *,
    event_rel_start: int,
    event_rel_end: int,
    num_time: int,
    num_channels: int,
    padding_bins: int | None,
) -> tuple[int, int]:
    """Return the half-open bin range fitted: the event window plus padding on each side.

    Automatic padding is the event length, at least MIN_FIT_PADDING_BINS, and
    shrinks so the window stays within MAX_MODEL_FIT_CELLS. The window is
    clipped to the data.
    """
    event_length = max(0, event_rel_end - event_rel_start)
    if padding_bins is None:
        padding = max(event_length, MIN_FIT_PADDING_BINS)
        room = MAX_MODEL_FIT_CELLS // max(1, num_channels) - event_length
        padding = max(0, min(padding, room // 2))
    else:
        padding = max(0, int(padding_bins))
    return max(0, event_rel_start - padding), min(num_time, event_rel_end + padding)


def _auto_time_upsample(parameters: BurstParameters, *, time_resolution_s: float, current: int, cells: int) -> int:
    """Time upsampling that resolves the narrowest fitted component, never below ``current``.

    A scattered profile rises on the scale of the component width, so the
    width alone sets how finely the model must be sampled.
    """
    widths = np.asarray(parameters.burst_width, dtype=float)
    widths = widths[np.isfinite(widths)]
    cap = min(MAX_AUTO_TIME_UPSAMPLE, max(1, MAX_UPSAMPLED_FIT_SAMPLES // max(1, cells)))
    if widths.size == 0 or not time_resolution_s > 0:
        return current
    narrowest = float(np.min(widths))
    if narrowest <= 0:
        return max(current, cap)
    needed = int(np.ceil(AUTO_SUBSAMPLES_PER_WIDTH * time_resolution_s / narrowest))
    return max(current, min(needed, cap))


def _resolution_flags(fit: BurstFitResult, *, time_resolution_ms: float) -> list[str]:
    """Flag fitted timescales shorter than one time bin."""
    flags: list[str] = []
    widths_ms = np.asarray(fit.parameters.burst_width, dtype=float) * 1e3
    if np.any(widths_ms < time_resolution_ms):
        flags.append("width_below_time_resolution")
    tau_ms = float(fit.parameters.scattering_timescale) * 1e3
    if 0 < tau_ms < time_resolution_ms and "scattering_timescale" not in fit.parameters_at_bounds:
        flags.append("scattering_below_time_resolution")
    return flags


def _normalize_dynamic_spectrum(
    data: np.ndarray,
    offpulse_bins: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Put the dynamic spectrum in off-pulse signal-to-noise units.

    Each channel's off-pulse baseline is subtracted, and the whole band is
    divided by the median off-pulse noise of its channels. FLITS's readers
    have already flattened the bandpass, so this sets the S/N scale without
    dividing each channel by its own noise estimate: those estimates scatter
    by a few per cent between channels, and dividing by them would stamp that
    scatter onto the burst spectrum, where a smooth spectral model cannot
    follow it and a bright burst's fit is biased. Per-channel noise enters
    through the fit weights instead.

    Parameters
    ----------
    data
        Dynamic spectrum with shape ``(n_channels, n_time)``.
    offpulse_bins
        Reference time bins used to estimate each channel's baseline and noise.

    Returns
    -------
    normalized, good_freq
        ``normalized`` has the same shape as ``data``. ``good_freq`` marks
        channels with finite samples and positive finite noise estimates; other
        rows remain zero-filled and are excluded from the fit.
    """
    normalized = np.zeros_like(data, dtype=float)
    good_freq = np.zeros(data.shape[0], dtype=bool)
    sigmas = np.full(data.shape[0], np.nan)
    for index in range(data.shape[0]):
        row = np.asarray(data[index], dtype=float)
        finite_row = row[np.isfinite(row)]
        if finite_row.size == 0:
            continue
        reference = row[offpulse_bins] if offpulse_bins.size else row
        finite_reference = reference[np.isfinite(reference)]
        if finite_reference.size == 0:
            finite_reference = finite_row
        baseline = float(np.nanmean(finite_reference))
        sigma = float(np.nanstd(finite_reference))
        if not np.isfinite(sigma) or sigma <= 0:
            continue
        normalized[index, :] = np.where(np.isfinite(row), row, baseline) - baseline
        sigmas[index] = sigma
        good_freq[index] = True
    if good_freq.any():
        normalized[good_freq] /= float(np.median(sigmas[good_freq]))
    return normalized, good_freq


def _diagnostic_indices(length: int, max_length: int) -> np.ndarray:
    """Return evenly spaced indices that cap a diagnostic axis length."""
    length = max(0, int(length))
    max_length = max(1, int(max_length))
    if length <= max_length:
        return np.arange(length, dtype=int)
    return np.unique(np.linspace(0, length - 1, max_length, dtype=int))


def _diagnostic_grid_indices(num_freq: int, num_time: int) -> tuple[np.ndarray, np.ndarray]:
    """Return frequency/time indices that keep model-fit diagnostics bounded."""
    freq_limit = min(MAX_MODEL_FIT_DIAGNOSTIC_FREQ_BINS, max(1, int(num_freq)))
    freq_idx = _diagnostic_indices(num_freq, freq_limit)
    time_limit = min(
        MAX_MODEL_FIT_DIAGNOSTIC_TIME_BINS,
        max(1, MAX_MODEL_FIT_DIAGNOSTIC_CELLS // max(1, int(freq_idx.size))),
        max(1, int(num_time)),
    )
    time_idx = _diagnostic_indices(num_time, time_limit)
    return freq_idx, time_idx


def _initial_parameters(
    *,
    normalized_data: np.ndarray,
    freqs_mhz: np.ndarray,
    time_axis_ms: np.ndarray,
    tsamp_ms: float,
    peak_rel_bin: int,
    event_rel_start: int,
    event_rel_end: int,
    width_guess_ms: float | None,
    num_components: int = 1,
) -> dict[str, list[float]]:
    """Seed the fitburst-style parameter dictionary from the selected event.

    The adapter estimates the log-amplitude from the normalized event peak,
    places the initial arrival time at the selected peak bin, converts width
    and scattering guesses from milliseconds to fitburst's seconds convention,
    uses the minimum selected frequency as ``ref_freq``, and seeds fixed DM and
    spectral terms with neutral/default values.
    """
    event_window = normalized_data[:, event_rel_start:event_rel_end]
    peak_value = float(np.nanmax(event_window)) if event_window.size and np.isfinite(event_window).any() else 1.0
    amplitude_guess = float(np.log10(max(peak_value, 1e-2)))
    arrival_time_sec = float((time_axis_ms[peak_rel_bin] - float(time_axis_ms[0])) / 1e3)
    width_ms = (
        float(width_guess_ms)
        if width_guess_ms is not None and np.isfinite(width_guess_ms)
        else max(tsamp_ms * 2.0, (event_rel_end - event_rel_start) * tsamp_ms / 6.0)
    )
    width_ms = max(width_ms, tsamp_ms)
    tau_ms = max(tsamp_ms, width_ms / 4.0)
    ref_freq = float(np.min(freqs_mhz))
    return {
        "amplitude": [amplitude_guess] * num_components,
        "arrival_time": [arrival_time_sec] * num_components,
        "burst_width": [float(width_ms / 1e3)] * num_components,
        "dm": [0.0] * num_components,
        "dm_index": [-2.0] * num_components,
        "ref_freq": [ref_freq] * num_components,
        "scattering_timescale": [float(tau_ms / 1e3)] * num_components,
        "scattering_index": [-4.0] * num_components,
        "spectral_index": [0.0] * num_components,
        "spectral_running": [0.0] * num_components,
    }


def _apply_initial_parameter_overrides(
    *,
    defaults: dict[str, list[float]],
    overrides: dict[str, Any],
    num_components: int,
) -> dict[str, list[float]]:
    """Return initial parameters with JSON/API overrides sanitized.

    The web UI submits per-component guesses as plain JSON numbers. This helper
    keeps the adapter tolerant of scalar values, short arrays, long arrays, and
    non-finite entries while ensuring the model receives exactly one numeric
    value per component for every known parameter.
    """
    coerced = {key: _coerce_parameter_values(values, values, num_components) for key, values in defaults.items()}
    for key, values in overrides.items():
        if key not in defaults:
            continue
        coerced[key] = _coerce_parameter_values(values, defaults[key], num_components)
    return coerced


def _coerce_parameter_values(
    values: Any,
    defaults: list[float],
    num_components: int,
) -> list[float]:
    component_count = max(1, int(num_components))
    default_array = np.asarray(defaults, dtype=float).ravel()
    if default_array.size == 0:
        default_array = np.zeros(component_count, dtype=float)
    if default_array.size < component_count:
        default_array = np.pad(default_array, (0, component_count - default_array.size), mode="edge")
    default_array = default_array[:component_count]

    try:
        value_array = np.asarray(values, dtype=float).ravel()
    except (TypeError, ValueError):
        return [float(value) for value in default_array]

    if value_array.size == 0:
        return [float(value) for value in default_array]
    if value_array.size == 1:
        value_array = np.repeat(value_array[0], component_count)
    elif value_array.size < component_count:
        value_array = np.concatenate([value_array, default_array[value_array.size : component_count]])
    elif value_array.size > component_count:
        value_array = value_array[:component_count]

    value_array = np.where(np.isfinite(value_array), value_array, default_array)
    return [float(value) for value in value_array]


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return bool(default)
    return bool(value)


def _coerce_initial_parameter_source(value: Any, initial_parameters: Any = None) -> str:
    allowed = {"current_selection", "previous_fit", "imported_solution", "component_comparison", "api"}
    if value is None:
        return "api" if initial_parameters else "current_selection"
    source = str(value).strip().lower().replace("-", "_")
    return source if source in allowed else ("api" if initial_parameters else "current_selection")


def _coerce_weighting_mode(value: Any) -> str:
    if value is None:
        return "none"
    mode = str(value).strip().lower().replace("-", "_")
    if mode in {"off", "false", "unweighted"}:
        return "none"
    if mode in {"weighted", "on", "true"}:
        return "auto"
    return mode if mode in WEIGHTING_MODES else "none"


def _coerce_iterations(value: Any) -> int:
    try:
        iterations = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, iterations)


def _coerce_max_function_evaluations(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        max_evaluations = int(value)
    except (TypeError, ValueError):
        return None
    if max_evaluations <= 0:
        return None
    return min(max_evaluations, MAX_MODEL_FIT_FUNCTION_EVALUATIONS)


def _coerce_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def _coerce_optional_positive_int(value: Any) -> int | None:
    """A positive integer, or None for "auto" (None, blank, "auto" or zero)."""
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "auto"}):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _coerce_padding_bins(value: Any) -> int | None:
    """Bins of padding on each side of the event window, or None for automatic padding."""
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "auto"}):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _coerce_ref_freq_mhz(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def _validate_free_parameters(values: Any, *, scintillation: bool = False) -> list[str]:
    if values is None:
        names = list(DEFAULT_FREE_PARAMETERS)
    else:
        if isinstance(values, str):
            raise ValueError("free_parameters must be a list of model parameter names.")
        try:
            names = [str(value).strip() for value in values]
        except TypeError as exc:
            raise ValueError("free_parameters must be a list of model parameter names.") from exc

    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate free fit parameters: {', '.join(duplicates)}.")

    non_fittable = [name for name in names if name in NON_FITTABLE_PARAMETERS]
    if non_fittable:
        raise ValueError(
            f"{', '.join(non_fittable)} is an initialization/reference parameter, not a fittable model parameter."
        )

    allowed = set(FIT_PARAMETERS)
    unknown = sorted({name for name in names if name not in allowed})
    if unknown:
        raise ValueError(f"Unknown free fit parameters: {', '.join(unknown)}.")

    if scintillation:
        names = [name for name in names if name not in SCINTILLATION_INACTIVE_PARAMETERS]

    active_fit_parameters = [
        parameter
        for parameter in FIT_PARAMETERS
        if not (scintillation and parameter in SCINTILLATION_INACTIVE_PARAMETERS)
    ]
    if active_fit_parameters and not any(parameter in names for parameter in active_fit_parameters):
        raise ValueError("At least one model parameter must remain free.")

    return names


def _fixed_parameters_from_free(free_parameters: list[str], *, scintillation: bool = False) -> list[str]:
    free = set(free_parameters)
    return [
        parameter
        for parameter in FIT_PARAMETERS
        if parameter not in free and not (scintillation and parameter in SCINTILLATION_INACTIVE_PARAMETERS)
    ]


def _coerce_weight_range(values: Any, num_time: int | None = None) -> list[int] | None:
    if values is None:
        return None
    try:
        value_array = np.asarray(values, dtype=float).ravel()
    except (TypeError, ValueError):
        return None
    if value_array.size < 2 or not np.all(np.isfinite(value_array[:2])):
        return None
    start = int(np.floor(value_array[0]))
    end = int(np.ceil(value_array[1]))
    if num_time is not None:
        start = max(0, min(start, int(num_time)))
        end = max(0, min(end, int(num_time)))
    if end <= start:
        return None
    return [start, end]


def _offpulse_channel_weights(
    *,
    normalized_data: np.ndarray,
    good_freq: np.ndarray,
    offpulse_bins: np.ndarray,
) -> tuple[np.ndarray | None, list[int] | None]:
    bins = np.asarray(offpulse_bins, dtype=int)
    bins = np.unique(bins[(bins >= 0) & (bins < normalized_data.shape[1])])
    if bins.size < MIN_WEIGHT_BINS:
        return None, None

    weights = np.zeros(normalized_data.shape[0], dtype=float)
    for index in np.flatnonzero(good_freq):
        reference = np.asarray(normalized_data[index, bins], dtype=float)
        reference = reference[np.isfinite(reference)]
        if reference.size < MIN_WEIGHT_BINS:
            return None, None
        sigma = float(np.nanstd(reference))
        if not np.isfinite(sigma) or sigma <= 0:
            return None, None
        weights[index] = 1.0 / sigma

    if int(np.count_nonzero(weights > 0)) < MIN_FIT_CHANNELS:
        return None, None
    return weights, [int(bins[0]), int(bins[-1] + 1)]


def _copy_parameter_dict(
    parameters: Mapping[str, Any],
) -> dict[str, list[float] | None]:
    copied: dict[str, list[float] | None] = {}
    for key, values in parameters.items():
        if values is None:
            copied[str(key)] = None
            continue
        try:
            value_array = np.asarray(values, dtype=float).ravel()
        except (TypeError, ValueError):
            copied[str(key)] = []
            continue
        copied[str(key)] = [float(value) for value in value_array]
    return copied


def _merge_parameter_dicts(
    base: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, list[float] | None]:
    merged = _copy_parameter_dict(base)
    merged.update(_copy_parameter_dict(updates))
    return merged


def _parameter_value(parameters: dict[str, list[float] | None], key: str) -> float | None:
    values = parameters.get(key)
    if not values:
        return None
    value = values[0]
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _sanitize_fit_statistics(
    fit_statistics: Mapping[str, object],
) -> dict[str, float | int | None]:
    """Keep only scalar, JSON-safe fit statistics for diagnostics exports."""
    payload: dict[str, float | int | None] = {}
    for key in (
        "chisq_initial",
        "chisq_final",
        "chisq_final_reduced",
        "snr",
        "num_freq",
        "num_freq_good",
        "num_fit_parameters",
        "num_linear_parameters",
        "num_observations",
        "num_time",
        "aic",
        "bic",
        "function_evaluations",
        "jacobian_evaluations",
        "max_function_evaluations",
        "solver_status",
        "diagnostic_num_freq",
        "diagnostic_num_time",
        "diagnostic_num_cells",
    ):
        value = fit_statistics.get(key)
        if value is None:
            payload[key] = None
        elif isinstance(value, (np.integer, int)):
            payload[key] = int(value)
        elif isinstance(value, (np.floating, float)):
            payload[key] = None if not np.isfinite(value) else float(value)
    return payload


def _sanitize_log(value: object) -> str | None:
    """Return a bounded, JSON-safe diagnostic string."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = "".join(char if char == "\n" or char == "\t" or ord(char) >= 32 else " " for char in text)
    sanitized = "\n".join(line.rstrip() for line in sanitized.splitlines()).strip()
    if not sanitized:
        return None
    if len(sanitized) <= MAX_FAILURE_LOG_CHARS:
        return sanitized
    suffix = "\n[diagnostic output truncated]"
    return sanitized[: MAX_FAILURE_LOG_CHARS - len(suffix)] + suffix


def _failed_result(
    *,
    status: str,
    message: str | None,
    initial_parameters: Mapping[str, Any] | None = None,
    bestfit_parameters: Mapping[str, Any] | None = None,
    fit_statistics: Mapping[str, object] | None = None,
    fit_parameters: list[str] | None = None,
    free_parameters: list[str] | None = None,
    initial_parameter_source: str | None = None,
    fixed_parameters: list[str] | None = None,
    component_count: int = 1,
    scintillation: bool | None = None,
    weighting_mode: str | None = None,
    weight_range: list[int] | None = None,
    weight_range_basis: str | None = None,
    fit_iterations_requested: int | None = None,
    fit_iterations_completed: int | None = None,
    factor_time_upsample: int | None = None,
    time_upsample_basis: str | None = None,
    factor_freq_upsample: int | None = None,
    ref_freq_mhz: float | None = None,
    is_folded: bool | None = None,
    exact_jacobian: bool | None = None,
    fit_window_ms: list[float] | None = None,
    time_resolution_ms: float | None = None,
    max_function_evaluations: int | None = None,
    function_evaluations: int | None = None,
    solver_status: int | None = None,
    solver_message: str | None = None,
    failure_stdout: str | None = None,
    failure_stderr: str | None = None,
    failure_exception: str | None = None,
) -> ModelFitResult:
    """Return a structured failure result with empty diagnostic arrays."""
    diagnostics = ModelFitDiagnostics(
        status=status,
        message=message,
        fitter=FITTER_NAME,
        component_count=component_count,
        fit_parameters=list(FIT_PARAMETERS if fit_parameters is None else fit_parameters),
        free_parameters=list(DEFAULT_FREE_PARAMETERS if free_parameters is None else free_parameters),
        initial_parameter_source=_coerce_initial_parameter_source(initial_parameter_source, initial_parameters),
        fixed_parameters=list(DEFAULT_FIXED_PARAMETERS if fixed_parameters is None else fixed_parameters),
        scintillation=None if scintillation is None else bool(scintillation),
        weighting_mode=None if weighting_mode is None else _coerce_weighting_mode(weighting_mode),
        weight_range=weight_range,
        weight_range_basis=weight_range_basis,
        fit_iterations_requested=fit_iterations_requested,
        fit_iterations_completed=fit_iterations_completed,
        factor_time_upsample=factor_time_upsample,
        time_upsample_basis=time_upsample_basis,
        factor_freq_upsample=factor_freq_upsample,
        ref_freq_mhz=ref_freq_mhz,
        is_folded=is_folded,
        exact_jacobian=exact_jacobian,
        fit_window_ms=fit_window_ms,
        time_resolution_ms=time_resolution_ms,
        max_function_evaluations=max_function_evaluations,
        function_evaluations=function_evaluations,
        solver_status=solver_status,
        solver_message=solver_message,
        failure_stdout=failure_stdout,
        failure_stderr=failure_stderr,
        failure_exception=failure_exception,
        initial_parameters=_copy_parameter_dict(initial_parameters or {}),
        bestfit_parameters=_copy_parameter_dict(bestfit_parameters or {}),
        bestfit_uncertainties={},
        fit_statistics=_sanitize_fit_statistics(fit_statistics or {}),
        freq_axis_mhz=np.array([], dtype=float),
        time_axis_ms=np.array([], dtype=float),
        data_dynamic_spectrum_sn=np.empty((0, 0), dtype=float),
        model_dynamic_spectrum_sn=np.empty((0, 0), dtype=float),
        residual_dynamic_spectrum_sn=np.empty((0, 0), dtype=float),
        data_profile_sn=np.array([], dtype=float),
        model_profile_sn=np.array([], dtype=float),
        residual_profile_sn=np.array([], dtype=float),
    )
    return ModelFitResult(
        status=status,
        message=message,
        width_ms_model=None,
        width_uncertainty_ms=None,
        tau_sc_ms=None,
        tau_uncertainty_ms=None,
        diagnostics=diagnostics,
    )
