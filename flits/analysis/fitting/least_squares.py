"""Least-squares fits of the fitburst model, and comparisons between fits.

The objective is fitburst's: the residual of each sample is
``weight[channel] * (data - model)``, masked channels have zero weight, and the
fit statistics carry fitburst's names and definitions. A FLITS fit and a
fitburst fit of the same data therefore minimise the same chi-squared. What
differs is how the minimum is found and described:

- The Jacobian is analytic and exact (:mod:`flits.analysis.fitting.burst_model`).
- The solver is Levenberg-Marquardt on the normal equations ``J^T J`` and
  ``J^T r``, which the model accumulates block by block. A fit has a handful
  of parameters and up to hundreds of thousands of samples, so this replaces
  a factorization of the full Jacobian at every step with one of a matrix the
  size of the parameter count. Steps are scaled by the Jacobian's column norms
  (Marquardt's scaling), and the convergence tests and status codes are
  SciPy's ``least_squares``.
- Widths and the scattering timescale stay positive unless ``bounded=False``:
  the solver steps in their logarithm. fitburst has no bounds.
- With the scattering timescale fitted in fitburst's default profile
  normalization, the solver steps each amplitude as that of the Gaussian the
  scattered profile tends to as the timescale goes to zero. In fitburst's
  convention the amplitude grows without bound as the timescale shrinks, so the
  two are degenerate for unresolved scattering; the change of coordinates is
  exact and results are reported in fitburst's convention.
- The covariance is the inverse of the observed Hessian of chi-squared / 2, as
  in fitburst. The Hessian comes from differencing the analytic gradient. If it
  is not positive definite, the Gauss-Newton matrix ``J^T J`` is inverted
  instead, and ``covariance_basis`` records which one was used. Neither is
  scaled by the reduced chi-squared.
- The full covariance matrix, the parameters left at a bound, and AIC and BIC
  for comparing models are returned with the fit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import special

from flits.analysis.fitting.burst_model import (
    SCINTILLATION_LINEAR_PARAMETERS,
    BurstModel,
    BurstParameters,
    ParameterLayout,
)

__all__ = [
    "BurstFitResult",
    "ModelComparison",
    "channel_weights",
    "compare_fit_statistics",
    "compare_fits",
    "fit_burst_model",
]

LOWER_BOUNDS: dict[str, float] = {"burst_width": 0.0, "scattering_timescale": 0.0}
# A handful of correlated physical parameters can need several hundred steps.
# Give small models headroom while retaining parameter-count scaling for larger
# models. This is a ceiling: convergence still stops the solver early.
MIN_AUTO_FUNCTION_EVALUATIONS = 1000
FUNCTION_EVALUATIONS_PER_PARAMETER = 100
# Gradient-difference step for the observed Hessian, as a fraction of each
# parameter's Gauss-Newton 1-sigma uncertainty.
HESSIAN_STEP_FRACTION = 1e-3
_SQRT_PI_OVER_2 = float(np.sqrt(np.pi / 2.0))
_LN_10 = float(np.log(10.0))


@dataclass(frozen=True)
class BurstFitResult:
    """Outcome of :func:`fit_burst_model`.

    ``values``, ``uncertainties`` and the rows of ``covariance`` follow
    ``layout.entries``; ``labels`` names them the way fitburst does.
    """

    success: bool
    status: int | None
    message: str
    layout: ParameterLayout
    initial_parameters: BurstParameters
    parameters: BurstParameters
    values: np.ndarray
    uncertainties: np.ndarray | None
    covariance: np.ndarray | None
    covariance_basis: str | None
    parameters_at_bounds: tuple[str, ...]
    function_evaluations: int | None
    jacobian_evaluations: int | None
    max_function_evaluations: int
    fit_statistics: dict[str, float | int | None]
    model: np.ndarray | None
    weights: np.ndarray
    channel_amplitudes: np.ndarray | None = None

    @property
    def labels(self) -> tuple[str, ...]:
        return self.layout.labels

    @property
    def bestfit_parameters(self) -> dict[str, list[float]]:
        """Fitted values grouped by name, as fitburst's ``bestfit_parameters``."""
        return self.layout.grouped(self.values)

    @property
    def bestfit_uncertainties(self) -> dict[str, list[float]]:
        """1-sigma uncertainties grouped by name, empty without a covariance."""
        if self.uncertainties is None:
            return {}
        return self.layout.grouped(self.uncertainties)

    @property
    def correlation(self) -> np.ndarray | None:
        if self.covariance is None or self.uncertainties is None:
            return None
        scale = np.outer(self.uncertainties, self.uncertainties)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.asarray(self.covariance / scale, dtype=float)


@dataclass(frozen=True)
class ModelComparison:
    """How one fit compares with the others in :func:`compare_fits`.

    ``f_test_p_value`` is the probability of the chi-squared improvement over
    the previous, simpler fit if the extra parameters were not needed. It
    assumes nested models and Gaussian noise of the weighted scale, and adding
    a component is not a strictly nested change, so read it, like AIC and BIC,
    as guidance.
    """

    index: int
    num_parameters: int
    num_samples: int
    chisq: float
    aic: float
    bic: float
    delta_aic: float
    delta_bic: float
    f_test_p_value: float | None


def channel_weights(
    data: np.ndarray,
    good_freq: np.ndarray,
    *,
    weighted: bool = True,
    weight_range: Sequence[int] | None = None,
) -> np.ndarray:
    """Return fitburst's per-channel weights.

    Weighted fits use the inverse RMS of each channel over ``weight_range``
    (time-bin indices, end exclusive) or, without one, over all but the last
    bin, as fitburst's ``LSFitter`` does. Unweighted fits give good channels
    a weight of one. Masked channels, and channels whose RMS is zero or not
    finite, get zero weight.
    """
    values = np.asarray(data, dtype=float)
    good = np.asarray(good_freq, dtype=bool).ravel()
    if values.ndim != 2 or good.size != values.shape[0]:
        raise ValueError("data must be (n_channels, n_time) with one good_freq flag per channel.")
    weights = np.zeros(values.shape[0], dtype=float)
    if not weighted:
        weights[good] = 1.0
        return weights
    start, stop = (0, values.shape[1] - 1) if weight_range is None else (int(weight_range[0]), int(weight_range[1]))
    with np.errstate(invalid="ignore", divide="ignore"):
        rms = np.sqrt(np.mean(values[:, start:stop] ** 2, axis=1))
        usable = good & np.isfinite(rms) & (rms > 0)
        weights[usable] = 1.0 / rms[usable]
    return weights


def fit_burst_model(
    model: BurstModel,
    data: np.ndarray,
    initial: BurstParameters,
    fit_parameters: Sequence[str],
    *,
    weights: np.ndarray | None = None,
    exact_jacobian: bool = True,
    max_function_evaluations: int | None = None,
    bounded: bool = True,
    observed_hessian: bool = True,
) -> BurstFitResult:
    """Fit ``model`` to ``data`` by least squares, starting from ``initial``.

    Parameters
    ----------
    model
        Model on the data grid.
    data
        Dynamic spectrum, shape ``(n_channels, n_time)``. Non-finite samples
        are treated as zero; give their channels zero weight.
    initial
        Starting parameters. Parameters not in ``fit_parameters`` stay fixed
        at these values.
    fit_parameters
        Names of the parameters to fit. In scintillation mode ``amplitude``,
        ``spectral_index`` and ``spectral_running`` are solved per channel and
        cannot be listed.
    weights
        Per-channel weights; zero masks a channel. Defaults to one for every
        channel (see :func:`channel_weights`).
    exact_jacobian
        Use the analytic Jacobian. ``False`` uses two-point finite differences.
    max_function_evaluations
        Solver budget per pass. Auto allows at least 1000 evaluations, or 100
        times the number of fitted values when that is larger. Explicit budgets
        are respected, including smaller ones. Convergence can stop a pass early.
    bounded
        Keep widths and the scattering timescale non-negative.
    observed_hessian
        Invert the observed Hessian for the covariance, as fitburst does.
        ``False`` uses the Gauss-Newton matrix directly.
    """
    values = np.asarray(data, dtype=float)
    if values.shape != model.shape:
        raise ValueError(f"Data shape {values.shape} does not match the model grid {model.shape}.")
    values = np.where(np.isfinite(values), values, 0.0)
    if initial.num_components < 1:
        raise ValueError("The model needs at least one component.")
    if model.scintillation:
        solved = sorted(set(fit_parameters) & set(SCINTILLATION_LINEAR_PARAMETERS))
        if solved:
            raise ValueError(f"{', '.join(solved)} are solved per channel in scintillation mode and cannot be fitted.")
    layout = ParameterLayout(tuple(fit_parameters), initial.num_components)
    if layout.size == 0:
        raise ValueError("At least one model parameter must be fitted.")
    for name, component in layout.entries:
        if name in LOWER_BOUNDS and bounded and not initial.value(name, component) > LOWER_BOUNDS[name]:
            raise ValueError(f"Start {name} above zero to fit it.")

    channel_weight = np.ones(model.num_freq, dtype=float) if weights is None else np.asarray(weights, dtype=float)
    if channel_weight.shape != (model.num_freq,):
        raise ValueError("weights needs one value per channel.")
    channel_weight = np.where(np.isfinite(channel_weight) & (channel_weight > 0), channel_weight, 0.0)
    sample_weight = channel_weight[:, None]
    scintillation_data = values if model.scintillation else None

    def residual(vector: np.ndarray) -> np.ndarray:
        parameters = layout.with_vector(initial, vector)
        return np.asarray((sample_weight * (values - model.evaluate(parameters, scintillation_data))).ravel())

    def normal_equations(vector: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        if exact_jacobian:
            return model.normal_equations(layout.with_vector(initial, vector), layout, values, channel_weight)
        return _finite_difference_normal_equations(residual, vector)

    lower = np.array(
        [LOWER_BOUNDS.get(name, -np.inf) if bounded else -np.inf for name, _component in layout.entries], dtype=float
    )
    x0 = layout.vector(initial)
    budget = (
        max(MIN_AUTO_FUNCTION_EVALUATIONS, FUNCTION_EVALUATIONS_PER_PARAMETER * layout.size)
        if max_function_evaluations is None
        else max(1, int(max_function_evaluations))
    )
    good_channels = int(np.count_nonzero(channel_weight > 0))
    num_samples = good_channels * model.num_time
    chisq_initial = float(np.sum((sample_weight * values) ** 2))

    def failure(message: str) -> BurstFitResult:
        return BurstFitResult(
            success=False,
            status=None,
            message=message,
            layout=layout,
            initial_parameters=initial,
            parameters=initial,
            values=x0,
            uncertainties=None,
            covariance=None,
            covariance_basis=None,
            parameters_at_bounds=(),
            function_evaluations=None,
            jacobian_evaluations=None,
            max_function_evaluations=budget,
            fit_statistics=_statistics(
                model, layout, good_channels, num_samples, chisq_initial, None, function_evaluations=None
            ),
            model=None,
            weights=channel_weight,
        )

    if good_channels == 0:
        return failure("Every channel is masked or has zero weight.")
    coordinates = _Coordinates(layout, initial, lower, linked=not (model.scintillation or model.normalize_pbf))

    def normal_equations_y(y: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        chisq, normal, gradient = normal_equations(coordinates.x_from_y(y))
        chain = coordinates.dx_dy(y)
        return chisq, chain.T @ normal @ chain, chain.T @ gradient

    def normal_equations_u(u: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        chisq, normal, gradient = normal_equations_y(coordinates.y_from_u(u))
        chain = coordinates.dy_du(u)
        return chisq, normal * np.outer(chain, chain), gradient * chain

    try:
        with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
            y0 = coordinates.y_from_x(x0)
            solution = _levenberg_marquardt(normal_equations_u, coordinates.u_from_y(y0), budget)
            y_best = coordinates.y_from_u(solution.u)
            chain_u = coordinates.dy_du(solution.u)
            covariance_y, basis = _covariance(
                normal_equations_y,
                y_best,
                solution.normal / np.outer(chain_u, chain_u),
                solution.gradient / chain_u,
                observed_hessian,
            )
            x_best = coordinates.x_from_y(y_best)
            chain_y = coordinates.dx_dy(y_best)
            covariance = None if covariance_y is None else chain_y @ covariance_y @ chain_y.T
    except (ValueError, np.linalg.LinAlgError) as exc:
        return failure(f"The solver stopped: {exc}")

    best = layout.with_vector(initial, x_best)
    uncertainties = None if covariance is None else np.sqrt(np.diag(covariance))
    # A bounded parameter is at its bound when it ended many orders of
    # magnitude closer to it than it started, or well inside 1 sigma of it.
    room = y_best - lower
    sigma_y = np.full(layout.size, np.inf) if covariance_y is None else np.sqrt(np.diag(covariance_y))
    at_bound = np.isfinite(lower) & ((room <= 1e-6 * (y0 - lower)) | (room < 1e-3 * sigma_y))
    with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
        prediction = model.evaluate(best, scintillation_data)
    return BurstFitResult(
        success=solution.status > 0,
        status=solution.status,
        message=solution.message,
        layout=layout,
        initial_parameters=initial,
        parameters=best,
        values=x_best,
        uncertainties=uncertainties,
        covariance=covariance,
        covariance_basis=basis,
        parameters_at_bounds=tuple(label for label, flag in zip(layout.labels, at_bound, strict=True) if flag),
        function_evaluations=solution.function_evaluations,
        jacobian_evaluations=solution.function_evaluations,
        max_function_evaluations=budget,
        fit_statistics=_statistics(
            model,
            layout,
            good_channels,
            num_samples,
            chisq_initial,
            solution.chisq,
            function_evaluations=solution.function_evaluations,
        ),
        model=prediction,
        weights=channel_weight,
        channel_amplitudes=model.channel_amplitudes(best, values) if model.scintillation else None,
    )


class _Coordinates:
    """The three parameter spaces of a fit.

    ``x`` holds the model's parameters in fitburst's convention. ``y`` is ``x``
    with each linked amplitude replaced by the log-amplitude of the Gaussian
    that its scattered profile tends to as the scattering timescale goes to
    zero, ``A - log10(sqrt(pi / 2) * width / tau)``. ``u`` steps bounded
    parameters in ``log(y - lower)``. The solver works in ``u``, the covariance
    is taken in ``y``, and both are reported in ``x``.
    """

    def __init__(self, layout: ParameterLayout, initial: BurstParameters, lower: np.ndarray, *, linked: bool) -> None:
        self.lower = lower
        self.positive = np.isfinite(lower)
        position = {entry: index for index, entry in enumerate(layout.entries)}
        tau_index = position.get(("scattering_timescale", None))
        # (amplitude index, width index or None, width when fixed, tau index)
        self.links: list[tuple[int, int | None, float, int]] = []
        if linked and tau_index is not None:
            for component in range(layout.num_components):
                amplitude_index = position.get(("amplitude", component))
                if amplitude_index is not None:
                    self.links.append(
                        (
                            amplitude_index,
                            position.get(("burst_width", component)),
                            float(initial.burst_width[component]),
                            tau_index,
                        )
                    )

    def _offsets(self, values: np.ndarray) -> np.ndarray:
        # Widths and the timescale are the same in x and y, so either works here.
        offsets = np.zeros_like(values)
        for amplitude_index, width_index, fixed_width, tau_index in self.links:
            width = fixed_width if width_index is None else values[width_index]
            offsets[amplitude_index] = np.log10(_SQRT_PI_OVER_2 * width / values[tau_index])
        return offsets

    def x_from_y(self, y: np.ndarray) -> np.ndarray:
        return y + self._offsets(y)

    def y_from_x(self, x: np.ndarray) -> np.ndarray:
        return x - self._offsets(x)

    def dx_dy(self, y: np.ndarray) -> np.ndarray:
        chain = np.eye(y.size)
        for amplitude_index, width_index, _fixed_width, tau_index in self.links:
            if width_index is not None:
                chain[amplitude_index, width_index] = 1.0 / (y[width_index] * _LN_10)
            chain[amplitude_index, tau_index] = -1.0 / (y[tau_index] * _LN_10)
        return chain

    def y_from_u(self, u: np.ndarray) -> np.ndarray:
        y = u.copy()
        y[self.positive] = self.lower[self.positive] + np.exp(u[self.positive])
        return y

    def u_from_y(self, y: np.ndarray) -> np.ndarray:
        u = y.copy()
        u[self.positive] = np.log(y[self.positive] - self.lower[self.positive])
        return u

    def dy_du(self, u: np.ndarray) -> np.ndarray:
        chain = np.ones_like(u)
        chain[self.positive] = np.exp(u[self.positive])
        return chain


@dataclass(frozen=True)
class _Solution:
    u: np.ndarray
    chisq: float
    normal: np.ndarray
    gradient: np.ndarray
    status: int
    message: str
    function_evaluations: int


_MESSAGES = {
    0: "The maximum number of function evaluations is exceeded.",
    1: "`gtol` termination condition is satisfied.",
    2: "`ftol` termination condition is satisfied.",
    3: "`xtol` termination condition is satisfied.",
    4: "Both `ftol` and `xtol` termination conditions are satisfied.",
}


def _levenberg_marquardt(
    normal_equations: Callable[[np.ndarray], tuple[float, np.ndarray, np.ndarray]],
    u0: np.ndarray,
    max_function_evaluations: int,
    *,
    ftol: float = 1e-8,
    xtol: float = 1e-8,
    gtol: float = 1e-8,
) -> _Solution:
    """Minimise chi-squared from its normal equations (Madsen, Nielsen & Tingleff 2004, sect. 3.2).

    ``normal_equations(u)`` returns chi-squared, ``J^T J`` and ``J^T r``.
    The tolerances and status codes match :func:`scipy.optimize.least_squares`.
    """
    u = np.asarray(u0, dtype=float).copy()
    chisq, normal, gradient = normal_equations(u)
    evaluations = 1
    if not (np.isfinite(chisq) and np.all(np.isfinite(normal)) and np.all(np.isfinite(gradient))):
        raise ValueError("the model is not finite at the starting parameters.")
    column_norms = np.sqrt(np.clip(np.diag(normal), 0.0, None))
    scale = np.where(column_norms > 0, column_norms, 1.0)
    damping = 1e-3
    growth = 2.0
    status = 0
    while True:
        scaled_gradient = gradient / scale
        residual_norm = float(np.sqrt(chisq))
        if residual_norm == 0 or np.max(np.abs(scaled_gradient)) <= gtol * residual_norm:
            status = 1
            break
        if evaluations >= max_function_evaluations:
            break
        scaled_normal = normal / np.outer(scale, scale)
        try:
            factor = np.linalg.cholesky(scaled_normal + damping * np.eye(u.size))
        except np.linalg.LinAlgError:
            damping *= growth
            growth *= 2.0
            if damping > 1e30:
                status = 3
                break
            continue
        step = -np.linalg.solve(factor.T, np.linalg.solve(factor, scaled_gradient))
        step_norm = float(np.linalg.norm(step))
        u_norm = float(np.linalg.norm(scale * u))
        trial = u + step / scale
        trial_chisq, trial_normal, trial_gradient = normal_equations(trial)
        evaluations += 1
        cost = 0.5 * chisq
        predicted = 0.5 * float(step @ (damping * step - scaled_gradient))
        finite = np.isfinite(trial_chisq) and np.all(np.isfinite(trial_normal)) and np.all(np.isfinite(trial_gradient))
        actual = cost - 0.5 * trial_chisq if finite else -np.inf
        ratio = actual / predicted if predicted > 0 else -np.inf
        small_step = step_norm < xtol * (xtol + u_norm)
        if ratio > 0:
            small_change = actual < ftol * cost and ratio > 0.25
            u = trial
            chisq, normal, gradient = float(trial_chisq), trial_normal, trial_gradient
            scale = np.maximum(scale, np.sqrt(np.clip(np.diag(normal), 0.0, None)))
            damping *= max(1.0 / 3.0, 1.0 - (2.0 * ratio - 1.0) ** 3)
            growth = 2.0
            if small_change or small_step:
                status = 4 if small_change and small_step else 2 if small_change else 3
                break
        else:
            if small_step:
                status = 3
                break
            damping *= growth
            growth *= 2.0
    return _Solution(
        u=u,
        chisq=float(chisq),
        normal=normal,
        gradient=gradient,
        status=status,
        message=_MESSAGES[status],
        function_evaluations=evaluations,
    )


def _finite_difference_normal_equations(
    residual: Callable[[np.ndarray], np.ndarray], x: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray]:
    """Normal equations from a forward-difference Jacobian, SciPy's ``2-point`` step."""
    base = residual(x)
    jacobian = np.empty((base.size, x.size), dtype=float)
    for index in range(x.size):
        step = float(np.sqrt(np.finfo(float).eps) * max(1.0, abs(x[index])))
        shifted = x.copy()
        shifted[index] += step
        jacobian[:, index] = (residual(shifted) - base) / step
    return float(base @ base), jacobian.T @ jacobian, jacobian.T @ base


def _statistics(
    model: BurstModel,
    layout: ParameterLayout,
    good_channels: int,
    num_samples: int,
    chisq_initial: float,
    chisq_final: float | None,
    *,
    function_evaluations: int | None,
) -> dict[str, float | int | None]:
    """fitburst's fit statistics, plus the counts and criteria FLITS adds."""
    num_fit = layout.size
    num_linear = layout.num_components * good_channels if model.scintillation else 0
    num_observations = num_samples - num_fit
    statistics: dict[str, float | int | None] = {
        "num_freq": model.num_freq,
        "num_freq_good": good_channels,
        "num_fit_parameters": num_fit,
        "num_linear_parameters": num_linear,
        "num_observations": num_observations,
        "num_time": model.num_time,
        "chisq_initial": chisq_initial,
        "chisq_final": chisq_final,
        "chisq_final_reduced": None,
        "snr": None,
        "aic": None,
        "bic": None,
        "function_evaluations": function_evaluations,
    }
    if chisq_final is not None and np.isfinite(chisq_final):
        num_parameters = num_fit + num_linear
        statistics["chisq_final_reduced"] = chisq_final / num_observations if num_observations > 0 else None
        statistics["snr"] = float(np.sqrt(max(chisq_initial - chisq_final, 0.0)))
        statistics["aic"] = chisq_final + 2.0 * num_parameters
        statistics["bic"] = chisq_final + num_parameters * float(np.log(max(num_samples, 1)))
    return statistics


def _covariance(
    normal_equations: Callable[[np.ndarray], tuple[float, np.ndarray, np.ndarray]],
    x: np.ndarray,
    normal: np.ndarray,
    gradient: np.ndarray,
    observed_hessian: bool,
) -> tuple[np.ndarray | None, str | None]:
    """Invert the observed Hessian of chi-squared / 2, falling back to Gauss-Newton."""
    gauss_newton_covariance = _inverse_if_positive_definite(normal)
    if not observed_hessian or gauss_newton_covariance is None:
        return gauss_newton_covariance, None if gauss_newton_covariance is None else "gauss_newton"

    sigma = np.sqrt(np.diag(gauss_newton_covariance))
    hessian = np.empty_like(normal)
    for index in range(x.size):
        step = HESSIAN_STEP_FRACTION * sigma[index]
        if not np.isfinite(step) or step <= 0:
            return gauss_newton_covariance, "gauss_newton"
        # The gradient of chi-squared / 2 is J^T r. Step up, away from a lower bound.
        shifted = x.copy()
        shifted[index] += step
        hessian[:, index] = (normal_equations(shifted)[2] - gradient) / step
    hessian = 0.5 * (hessian + hessian.T)
    covariance = _inverse_if_positive_definite(hessian)
    if covariance is None:
        return gauss_newton_covariance, "gauss_newton"
    return covariance, "observed_hessian"


def _inverse_if_positive_definite(matrix: np.ndarray) -> np.ndarray | None:
    if not np.all(np.isfinite(matrix)):
        return None
    # Invert in the scale of the diagonal so that parameters in seconds and in
    # dex do not make a well-posed matrix look singular.
    diagonal = np.diag(matrix)
    if np.any(diagonal <= 0):
        return None
    scale = 1.0 / np.sqrt(diagonal)
    scaled = matrix * np.outer(scale, scale)
    try:
        factor = np.linalg.cholesky(scaled)
    except np.linalg.LinAlgError:
        return None
    identity = np.eye(matrix.shape[0])
    inverse_factor = np.linalg.solve(factor, identity)
    return np.asarray((inverse_factor.T @ inverse_factor) * np.outer(scale, scale), dtype=float)


def compare_fits(fits: Sequence[BurstFitResult]) -> list[ModelComparison]:
    """Compare fits of the same data, ordered from fewest to most parameters.

    Each fit must have fitted the same samples with the same weights. AIC and
    BIC count every fitted value, including per-channel amplitudes in
    scintillation mode; deltas are relative to the lowest value.
    """
    for index, fit in enumerate(fits):
        if not fit.success:
            raise ValueError(f"Fit {index} did not converge, so it cannot be compared.")
    return compare_fit_statistics([fit.fit_statistics for fit in fits])


def compare_fit_statistics(statistics: Sequence[Mapping[str, float | int | None]]) -> list[ModelComparison]:
    """:func:`compare_fits` from each fit's ``fit_statistics``, as stored with a fit."""
    rows: list[tuple[int, int, int, float]] = []
    for index, values in enumerate(statistics):
        chisq = values.get("chisq_final")
        if chisq is None:
            raise ValueError(f"Fit {index} did not converge, so it cannot be compared.")
        num_parameters = int(values.get("num_fit_parameters") or 0) + int(values.get("num_linear_parameters") or 0)
        num_samples = int(values.get("num_freq_good") or 0) * int(values.get("num_time") or 0)
        rows.append((index, num_parameters, num_samples, float(chisq)))
    if len({row[2] for row in rows}) > 1:
        raise ValueError("The fits do not cover the same samples.")
    rows.sort(key=lambda row: row[1])
    aic = [chisq + 2.0 * count for _index, count, _samples, chisq in rows]
    bic = [chisq + count * float(np.log(max(samples, 1))) for _index, count, samples, chisq in rows]
    comparisons: list[ModelComparison] = []
    for position, (index, count, samples, chisq) in enumerate(rows):
        p_value: float | None = None
        if position > 0:
            _previous_index, previous_count, _previous_samples, previous_chisq = rows[position - 1]
            extra = count - previous_count
            remaining = samples - count
            if extra > 0 and remaining > 0 and chisq > 0:
                statistic = max(previous_chisq - chisq, 0.0) / extra / (chisq / remaining)
                p_value = float(special.fdtrc(extra, remaining, statistic))
        comparisons.append(
            ModelComparison(
                index=index,
                num_parameters=count,
                num_samples=samples,
                chisq=chisq,
                aic=aic[position],
                bic=bic[position],
                delta_aic=aic[position] - min(aic),
                delta_bic=bic[position] - min(bic),
                f_test_p_value=p_value,
            )
        )
    return comparisons
