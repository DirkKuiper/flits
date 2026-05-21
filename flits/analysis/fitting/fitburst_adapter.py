"""Adapter for optional fitburst-backed model fits.

This module keeps the optional :mod:`fitburst` dependency behind a small FLITS
interface. The session layer passes in an already cropped/reduced dynamic
spectrum for the selected frequency band; this adapter normalizes each channel
against off-pulse bins, fits only the selected event window, and returns
model-based intrinsic width and scattering-time diagnostics.

If fitburst is not installed or a fit cannot be constrained, callers receive a
structured :class:`ModelFitResult` failure instead of an import-time
exception.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from flits.models import ModelFitDiagnostics, UncertaintyDetail

try:
    from scipy.optimize import least_squares
except Exception:  # pragma: no cover - scipy is expected with fitburst, but keep import optional
    least_squares = None

try:
    from fitburst.analysis.fitter import LSFitter
    from fitburst.analysis.model import SpectrumModeler
except Exception:  # pragma: no cover - optional runtime dependency
    LSFitter = None
    SpectrumModeler = None


MIN_FIT_CHANNELS = 4
MIN_FIT_TIME_BINS = 16
MIN_WEIGHT_BINS = 8
MAX_FITBURST_LOG_CHARS = 4000
MAX_MODEL_FIT_CELLS = 500_000
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


def _fitburst_uncertainty_detail(
    *,
    value: float | None,
    units: str,
    parameter_label: str,
) -> UncertaintyDetail:
    basis = (
        f"{parameter_label} uncertainty reported by fitburst from its local least-squares covariance/Hessian estimate. "
        "This is model-based, secondary to the non-parametric FLITS measurements, and should not be compared directly "
        "with formal 1-sigma uncertainties from other estimators."
    )
    tooltip = (
        "Model-based fitburst uncertainty. It reflects the local fit covariance of the selected parametric model and "
        "is retained as a secondary diagnostic rather than a publishable non-parametric error bar."
    )
    warning_flags = ["fitburst_model_hessian"]
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
    factor_time_upsample: int = 1
    factor_freq_upsample: int = 1
    ref_freq_mhz: float | None = None
    is_folded: bool = False
    exact_jacobian: bool = True
    max_function_evaluations: int | None = None
    scintillation: bool = False
    fixed_parameters: list[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scintillation", _coerce_bool(self.scintillation, default=False))
        free_parameters = _validate_free_parameters(self.free_parameters, scintillation=self.scintillation)
        object.__setattr__(self, "free_parameters", free_parameters)
        object.__setattr__(self, "fixed_parameters", _fixed_parameters_from_free(free_parameters, scintillation=self.scintillation))
        object.__setattr__(self, "initial_parameter_source", _coerce_initial_parameter_source(self.initial_parameter_source, self.initial_parameters))
        object.__setattr__(self, "weighting_mode", _coerce_weighting_mode(self.weighting_mode))
        object.__setattr__(self, "weight_range", _coerce_weight_range(self.weight_range))
        if self.weighting_mode == "manual_range" and self.weight_range is None:
            raise ValueError("manual_range weighting requires a two-bin weight_range.")
        object.__setattr__(self, "iterations", _coerce_iterations(self.iterations))
        object.__setattr__(self, "factor_time_upsample", _coerce_positive_int(self.factor_time_upsample))
        object.__setattr__(self, "factor_freq_upsample", _coerce_positive_int(self.factor_freq_upsample))
        object.__setattr__(self, "ref_freq_mhz", _coerce_ref_freq_mhz(self.ref_freq_mhz))
        object.__setattr__(self, "is_folded", _coerce_bool(self.is_folded, default=False))
        object.__setattr__(self, "exact_jacobian", _coerce_bool(self.exact_jacobian, default=True))
        object.__setattr__(self, "max_function_evaluations", _coerce_max_function_evaluations(self.max_function_evaluations))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible request payload."""
        return {
            "num_components": int(self.num_components),
            "free_parameters": list(self.free_parameters),
            "initial_parameters": self.initial_parameters,
            "initial_parameter_source": _coerce_initial_parameter_source(self.initial_parameter_source, self.initial_parameters),
            "solver": {
                "weighting_mode": _coerce_weighting_mode(self.weighting_mode),
                "weight_range": None if self.weight_range is None else [int(value) for value in self.weight_range],
                "iterations": _coerce_iterations(self.iterations),
                "factor_time_upsample": _coerce_positive_int(self.factor_time_upsample),
                "factor_freq_upsample": _coerce_positive_int(self.factor_freq_upsample),
                "ref_freq_mhz": _coerce_ref_freq_mhz(self.ref_freq_mhz),
                "is_folded": bool(self.is_folded),
                "exact_jacobian": bool(self.exact_jacobian),
                "max_function_evaluations": _coerce_max_function_evaluations(self.max_function_evaluations),
            },
            "scintillation": bool(self.scintillation),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ModelFitRequestConfig":
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
            initial_parameter_source=initial_parameter_source,
            weighting_mode=solver.get("weighting_mode", payload.get("weighting_mode", "none")),
            weight_range=_coerce_weight_range(solver.get("weight_range", payload.get("weight_range"))),
            iterations=_coerce_iterations(solver.get("iterations", payload.get("iterations", 1))),
            factor_time_upsample=_coerce_positive_int(solver.get("factor_time_upsample", payload.get("factor_time_upsample"))),
            factor_freq_upsample=_coerce_positive_int(solver.get("factor_freq_upsample", payload.get("factor_freq_upsample"))),
            ref_freq_mhz=_coerce_ref_freq_mhz(solver.get("ref_freq_mhz", payload.get("ref_freq_mhz"))),
            is_folded=_coerce_bool(solver.get("is_folded", payload.get("is_folded")), default=False),
            exact_jacobian=_coerce_bool(solver.get("exact_jacobian", payload.get("exact_jacobian")), default=True),
            max_function_evaluations=_coerce_max_function_evaluations(
                solver.get("max_function_evaluations", payload.get("max_function_evaluations"))
            ),
            scintillation=_coerce_bool(payload.get("scintillation"), default=False),
        )


@dataclass(frozen=True)
class ModelFitResult:
    """Result returned by the fitburst adapter.

    Parameters
    ----------
    status
        Machine-readable fit status. ``"ok"`` indicates a converged fit; other
        values describe structured failure modes.
    message
        Human-readable fitburst or adapter message, when available.
    width_ms_model
        Fitted intrinsic burst width for the first component, in milliseconds.
        Full component arrays are stored in ``diagnostics.bestfit_parameters``.
    width_uncertainty_ms
        Fitburst uncertainty for ``width_ms_model``, in milliseconds.
    tau_sc_ms
        Fitted scattering timescale for the first component, in milliseconds.
    tau_uncertainty_ms
        Fitburst uncertainty for ``tau_sc_ms``, in milliseconds.
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
    config: "ModelFitRequestConfig" | None = None,
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
        Time axis in milliseconds with length ``n_time``. The adapter converts
        the fitted event window to seconds before constructing the fitburst
        model.
    event_rel_start, event_rel_end
        Half-open event window, in selected-band time-bin coordinates. Only
        this interval is passed to ``LSFitter``.
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
        ``"fitburst_unavailable"``, ``"insufficient_data"``,
        ``"insufficient_time_bins"``, ``"insufficient_channels"``,
        ``"fit_window_too_large"``, ``"insufficient_signal"``, and
        ``"fit_failed"``.

    Notes
    -----
    The scalar width and scattering-time fields report the first component.
    For multi-component fits, inspect ``diagnostics.bestfit_parameters`` for
    the full fitburst parameter arrays.
    """
    if config is None:
        config = ModelFitRequestConfig()
    fit_iterations = _coerce_iterations(config.iterations)

    if SpectrumModeler is None or LSFitter is None:
        return _failed_result(
            status="fitburst_unavailable",
            message="fitburst is not available in the active Python environment.",
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
    fit_time_bins = max(0, int(event_rel_end) - int(event_rel_start))
    fit_cells = int(data.shape[0]) * fit_time_bins
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
    factor_time_upsample = _coerce_positive_int(config.factor_time_upsample)
    factor_freq_upsample = _coerce_positive_int(config.factor_freq_upsample)

    fit_data = np.array(normalized_data[:, event_rel_start:event_rel_end], dtype=float, copy=True)
    fit_time_axis_ms = time_axis_ms[event_rel_start:event_rel_end]
    times_sec = (fit_time_axis_ms - float(time_axis_ms[0])) / 1e3
    weighting_mode = _coerce_weighting_mode(config.weighting_mode)
    weight_range = _coerce_weight_range(config.weight_range, fit_data.shape[1])
    weight_range_basis: str | None = "none"
    custom_weights: np.ndarray | None = None
    fitter_weighted = False
    fitter_weight_range: list[int] | None = None
    if weighting_mode == "auto":
        custom_weights, weight_range = _offpulse_channel_weights(
            normalized_data=normalized_data,
            good_freq=good_freq,
            offpulse_bins=offpulse_bins,
        )
        if custom_weights is not None:
            weight_range_basis = "offpulse_bins"
        else:
            fitter_weighted = True
            weight_range_basis = "fit_window"
    elif weighting_mode == "fit_window":
        fitter_weighted = True
        weight_range_basis = "fit_window"
    elif weighting_mode == "manual_range":
        if weight_range is None:
            return _failed_result(
                status="invalid_config",
                message="manual_range weighting requires a valid two-bin weight_range within the fitted event window.",
                initial_parameters=initial_parameters,
                free_parameters=config.free_parameters,
                initial_parameter_source=config.initial_parameter_source,
                fixed_parameters=config.fixed_parameters,
                component_count=config.num_components,
                scintillation=config.scintillation,
                weighting_mode=weighting_mode,
                weight_range=None,
                weight_range_basis="invalid",
                fit_iterations_requested=fit_iterations,
                fit_iterations_completed=0,
                factor_time_upsample=factor_time_upsample,
                factor_freq_upsample=factor_freq_upsample,
                ref_freq_mhz=effective_ref_freq_mhz,
                is_folded=config.is_folded,
                exact_jacobian=config.exact_jacobian,
                max_function_evaluations=config.max_function_evaluations,
            )
        fitter_weighted = True
        fitter_weight_range = weight_range
        weight_range_basis = "manual_range"
    
    model = SpectrumModeler(
        freqs,
        times_sec,
        dm_incoherent=0.0,
        factor_freq_upsample=factor_freq_upsample,
        factor_time_upsample=factor_time_upsample,
        num_components=config.num_components,
        is_dedispersed=True,
        is_folded=config.is_folded,
        scintillation=config.scintillation,
    )
    current_parameters = _copy_parameter_dict(initial_parameters)
    completed_iterations = 0
    fitter: Any | None = None
    results: Any | None = None
    fit_statistics: dict[str, object] = {}
    bestfit_uncertainties: dict[str, list[float] | None] = {}
    fit_parameters: list[str] = []
    solver_status: int | None = None
    solver_message: str | None = None
    function_evaluations: int | None = None
    effective_max_function_evaluations: int | None = None

    for _iteration_index in range(fit_iterations):
        _update_model_parameters(model, current_parameters)
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        fitter = None
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                fitter = LSFitter(
                    fit_data,
                    model,
                    good_freq=good_freq,
                    weighted_fit=fitter_weighted,
                    weight_range=fitter_weight_range,
                )
                if custom_weights is not None:
                    fitter.weights = custom_weights
                fitter.fix_parameter(config.fixed_parameters)
                effective_max_function_evaluations = _effective_max_function_evaluations(
                    fitter,
                    config.max_function_evaluations,
                )
                _run_fitburst_optimizer(
                    fitter,
                    exact_jacobian=config.exact_jacobian,
                    max_function_evaluations=config.max_function_evaluations,
                )
        except Exception as exc:
            return _failed_result(
                status="fit_failed",
                message="fitburst raised an exception during fitting.",
                initial_parameters=initial_parameters,
                bestfit_parameters=current_parameters if completed_iterations > 0 else {},
                fit_statistics=getattr(fitter, "fit_statistics", {}) if fitter is not None else {},
                fit_parameters=list(getattr(fitter, "fit_parameters", fit_parameters)) if fitter is not None else fit_parameters,
                free_parameters=config.free_parameters,
                initial_parameter_source=config.initial_parameter_source,
                fixed_parameters=config.fixed_parameters,
                component_count=config.num_components,
                scintillation=config.scintillation,
                weighting_mode=weighting_mode,
                weight_range=weight_range,
                weight_range_basis=weight_range_basis,
                fit_iterations_requested=fit_iterations,
                fit_iterations_completed=completed_iterations,
                factor_time_upsample=factor_time_upsample,
                factor_freq_upsample=factor_freq_upsample,
                ref_freq_mhz=effective_ref_freq_mhz,
                is_folded=config.is_folded,
                exact_jacobian=config.exact_jacobian,
                max_function_evaluations=effective_max_function_evaluations or config.max_function_evaluations,
                function_evaluations=function_evaluations,
                solver_status=solver_status,
                solver_message=solver_message,
                failure_stdout=_sanitize_fitburst_log(stdout_buffer.getvalue()),
                failure_stderr=_sanitize_fitburst_log(stderr_buffer.getvalue()),
                failure_exception=_sanitize_fitburst_log(f"{type(exc).__name__}: {exc}"),
            )

        results = getattr(fitter, "results", None)
        solver_status = _solver_status(results)
        solver_message = _solver_message(results)
        function_evaluations = _solver_nfev(results)
        if results is None or not getattr(results, "success", False):
            status = "evaluation_limit_exceeded" if solver_status == 0 else "fit_failed"
            message = solver_message
            return _failed_result(
                status=status,
                message=message or "fitburst could not converge for the current selection.",
                initial_parameters=initial_parameters,
                bestfit_parameters=current_parameters if completed_iterations > 0 else {},
                fit_statistics=getattr(fitter, "fit_statistics", {}),
                fit_parameters=list(getattr(fitter, "fit_parameters", [])),
                free_parameters=config.free_parameters,
                initial_parameter_source=config.initial_parameter_source,
                fixed_parameters=config.fixed_parameters,
                component_count=config.num_components,
                scintillation=config.scintillation,
                weighting_mode=weighting_mode,
                weight_range=weight_range,
                weight_range_basis=weight_range_basis,
                fit_iterations_requested=fit_iterations,
                fit_iterations_completed=completed_iterations,
                factor_time_upsample=factor_time_upsample,
                factor_freq_upsample=factor_freq_upsample,
                ref_freq_mhz=effective_ref_freq_mhz,
                is_folded=config.is_folded,
                exact_jacobian=config.exact_jacobian,
                max_function_evaluations=effective_max_function_evaluations or config.max_function_evaluations,
                function_evaluations=function_evaluations,
                solver_status=solver_status,
                solver_message=solver_message,
                failure_stdout=_sanitize_fitburst_log(stdout_buffer.getvalue()),
                failure_stderr=_sanitize_fitburst_log(stderr_buffer.getvalue()),
            )

        fit_statistics = dict(getattr(fitter, "fit_statistics", {}))
        fit_statistics.update(
            {
                "function_evaluations": function_evaluations,
                "max_function_evaluations": effective_max_function_evaluations,
                "solver_status": solver_status,
            }
        )
        bestfit_parameters = dict(fit_statistics.get("bestfit_parameters") or {})
        bestfit_uncertainties = _copy_parameter_dict(dict(fit_statistics.get("bestfit_uncertainties") or {}))
        fit_parameters = list(getattr(fitter, "fit_parameters", []))
        current_parameters = _merge_parameter_dicts(current_parameters, bestfit_parameters)
        completed_iterations += 1

    if results is None:
        return _failed_result(
            status="fit_failed",
            message="fitburst could not converge for the current selection.",
            initial_parameters=initial_parameters,
            fit_parameters=fit_parameters,
            free_parameters=config.free_parameters,
            initial_parameter_source=config.initial_parameter_source,
            fixed_parameters=config.fixed_parameters,
            component_count=config.num_components,
            scintillation=config.scintillation,
            weighting_mode=weighting_mode,
            weight_range=weight_range,
            weight_range_basis=weight_range_basis,
            fit_iterations_requested=fit_iterations,
            fit_iterations_completed=completed_iterations,
            factor_time_upsample=factor_time_upsample,
            factor_freq_upsample=factor_freq_upsample,
            ref_freq_mhz=effective_ref_freq_mhz,
            is_folded=config.is_folded,
            exact_jacobian=config.exact_jacobian,
            max_function_evaluations=effective_max_function_evaluations or config.max_function_evaluations,
            function_evaluations=function_evaluations,
            solver_status=solver_status,
            solver_message=solver_message,
        )

    full_best_parameters = current_parameters
    
    _update_model_parameters(model, full_best_parameters)
    model_dynamic_spectrum = np.asarray(model.compute_model(data=fit_data), dtype=float)

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
    fit_statistics = dict(fit_statistics)
    fit_statistics.update(
        {
            "diagnostic_num_freq": int(diag_freq_idx.size),
            "diagnostic_num_time": int(diag_time_idx.size),
            "diagnostic_num_cells": int(diag_data.size),
        }
    )

    width_sec = _parameter_value(full_best_parameters, "burst_width")
    tau_sec = _parameter_value(full_best_parameters, "scattering_timescale")
    width_uncertainty_sec = _parameter_value(bestfit_uncertainties, "burst_width")
    tau_uncertainty_sec = _parameter_value(bestfit_uncertainties, "scattering_timescale")
    uncertainty_details = {
        "width_ms_model": _fitburst_uncertainty_detail(
            value=None if width_uncertainty_sec is None else float(width_uncertainty_sec * 1e3),
            units="ms",
            parameter_label="Intrinsic width",
        ),
        "tau_sc_ms": _fitburst_uncertainty_detail(
            value=None if tau_uncertainty_sec is None else float(tau_uncertainty_sec * 1e3),
            units="ms",
            parameter_label="Scattering timescale",
        ),
    }

    diagnostics = ModelFitDiagnostics(
        status="ok",
        message=str(getattr(results, "message", "") or "").strip() or None,
        fitter="fitburst",
        component_count=config.num_components,
        fit_parameters=fit_parameters,
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
        factor_freq_upsample=factor_freq_upsample,
        ref_freq_mhz=effective_ref_freq_mhz,
        is_folded=config.is_folded,
        exact_jacobian=config.exact_jacobian,
        max_function_evaluations=effective_max_function_evaluations or config.max_function_evaluations,
        function_evaluations=function_evaluations,
        solver_status=solver_status,
        solver_message=solver_message,
        initial_parameters=initial_parameters,
        bestfit_parameters=full_best_parameters,
        bestfit_uncertainties=bestfit_uncertainties,
        fit_statistics=_sanitize_fit_statistics(fit_statistics),
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


def _normalize_dynamic_spectrum(
    data: np.ndarray,
    offpulse_bins: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Normalize each frequency channel to off-pulse signal-to-noise units.

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
        filled = np.where(np.isfinite(row), row, baseline)
        normalized[index, :] = (filled - baseline) / sigma
        good_freq[index] = True
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
    """Seed the fitburst parameter dictionary from the selected event.

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
    width_ms = float(width_guess_ms) if width_guess_ms is not None and np.isfinite(width_guess_ms) else max(tsamp_ms * 2.0, (event_rel_end - event_rel_start) * tsamp_ms / 6.0)
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
    """Return fitburst initial parameters with JSON/API overrides sanitized.

    The web UI submits per-component guesses as plain JSON numbers. This helper
    keeps the adapter tolerant of scalar values, short arrays, long arrays, and
    non-finite entries while ensuring fitburst receives exactly one numeric
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
    allowed = {"current_selection", "previous_fit", "imported_solution", "api"}
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
        parameter for parameter in FIT_PARAMETERS if not (scintillation and parameter in SCINTILLATION_INACTIVE_PARAMETERS)
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
    parameters: dict[str, Any],
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


def _update_model_parameters(model: Any, parameters: dict[str, Any]) -> None:
    try:
        model.update_parameters(parameters, global_parameters=list(GLOBAL_PARAMETERS))
    except TypeError:
        model.update_parameters(parameters)


def _fit_parameter_count(fitter: Any) -> int:
    try:
        return len(fitter.get_fit_parameters_list())
    except Exception:
        return len(getattr(fitter, "fit_parameters", []) or [])


def _effective_max_function_evaluations(fitter: Any, max_function_evaluations: int | None) -> int:
    if max_function_evaluations is not None:
        return int(max_function_evaluations)
    return max(1, 100 * _fit_parameter_count(fitter))


def _run_fitburst_optimizer(
    fitter: Any,
    *,
    exact_jacobian: bool,
    max_function_evaluations: int | None,
) -> None:
    if max_function_evaluations is None:
        fitter.fit(exact_jacobian=exact_jacobian)
        return
    if least_squares is None:
        raise RuntimeError("scipy.optimize.least_squares is not available.")
    parameter_list = fitter.get_fit_parameters_list()
    jacobian = fitter.compute_jacobian if exact_jacobian else "2-point"
    results = least_squares(
        fitter.compute_residuals,
        parameter_list,
        jac=jacobian,
        max_nfev=int(max_function_evaluations),
    )
    fitter.results = results
    if getattr(results, "success", False):
        print("INFO: fit successful!")
    else:
        print("INFO: fit didn't work!")
    fitter._compute_fit_statistics(fitter.data, results)


def _solver_status(results: Any | None) -> int | None:
    if results is None:
        return None
    try:
        return int(getattr(results, "status"))
    except (TypeError, ValueError):
        return None


def _solver_nfev(results: Any | None) -> int | None:
    if results is None:
        return None
    try:
        return int(getattr(results, "nfev"))
    except (TypeError, ValueError):
        return None


def _solver_message(results: Any | None) -> str | None:
    if results is None:
        return None
    message = str(getattr(results, "message", "") or "").strip()
    return message or None


def _parameter_value(parameters: dict[str, list[float] | None], key: str) -> float | None:
    values = parameters.get(key)
    if not values:
        return None
    value = values[0]
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _sanitize_fit_statistics(
    fit_statistics: dict[str, object],
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
        "num_observations",
        "num_time",
        "function_evaluations",
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


def _sanitize_fitburst_log(value: object) -> str | None:
    """Return a bounded, JSON-safe fitburst diagnostic string."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = "".join(
        char
        if char == "\n" or char == "\t" or ord(char) >= 32
        else " "
        for char in text
    )
    sanitized = "\n".join(line.rstrip() for line in sanitized.splitlines()).strip()
    if not sanitized:
        return None
    if len(sanitized) <= MAX_FITBURST_LOG_CHARS:
        return sanitized
    suffix = "\n[fitburst diagnostic output truncated]"
    return sanitized[: MAX_FITBURST_LOG_CHARS - len(suffix)] + suffix


def _failed_result(
    *,
    status: str,
    message: str | None,
    initial_parameters: dict[str, list[float]] | None = None,
    bestfit_parameters: dict[str, list[float] | None] | None = None,
    fit_statistics: dict[str, object] | None = None,
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
    factor_freq_upsample: int | None = None,
    ref_freq_mhz: float | None = None,
    is_folded: bool | None = None,
    exact_jacobian: bool | None = None,
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
        fitter="fitburst" if SpectrumModeler is not None and LSFitter is not None else None,
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
        factor_freq_upsample=factor_freq_upsample,
        ref_freq_mhz=ref_freq_mhz,
        is_folded=is_folded,
        exact_jacobian=exact_jacobian,
        max_function_evaluations=max_function_evaluations,
        function_evaluations=function_evaluations,
        solver_status=solver_status,
        solver_message=solver_message,
        failure_stdout=failure_stdout,
        failure_stderr=failure_stderr,
        failure_exception=failure_exception,
        initial_parameters=initial_parameters or {},
        bestfit_parameters=bestfit_parameters or {},
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
