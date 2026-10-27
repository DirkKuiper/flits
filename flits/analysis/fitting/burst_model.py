"""The fitburst model of a burst's dynamic spectrum, evaluated natively.

FLITS fits the dynamic-spectrum model of Fonseca et al. (2024, ApJS 271, 49),
which the fitburst package (MIT licence, CHIME/FRB Collaboration) implements.
This module is written from the equations in that paper. It evaluates the model
over the whole (channel, time) grid in single array operations, together with
its analytic derivatives, and keeps fitburst's parameter names, units and
conventions so that a solution moves between the two programs unchanged:

- ``amplitude`` is the base-10 logarithm of the component amplitude.
- ``arrival_time``, ``burst_width`` and ``scattering_timescale`` are seconds;
  arrival times are measured on the supplied time axis.
- ``dm`` is the DM offset from the DM the data were dedispersed to, in
  pc cm^-3. It delays frequency ``nu`` by
  ``K_DM * dm * (nu**dm_index - ref_freq**dm_index)`` seconds.
- The scattering timescale is quoted at ``ref_freq`` and scales as
  ``(nu / ref_freq)**scattering_index``.
- The spectrum is a running power law,
  ``(nu / ref_freq)**(spectral_index + spectral_running * ln(nu / ref_freq))``.
- ``dm``, ``dm_index``, ``scattering_timescale`` and ``scattering_index`` are
  shared by all components; ``ref_freq`` is never fitted.

Each component is a Gaussian of width ``burst_width``. With a positive
scattering timescale the Gaussian is convolved with a one-sided exponential,
which gives the profile

    A * exp(-z**2 / 2) * erfcx((r - z) / sqrt(2)),

with ``z = (t - t_arrival(nu)) / burst_width`` and ``r = burst_width / tau(nu)``.
The prefactor ``A`` is ``tau_ref / tau(nu)``, fitburst's default. With
``normalize_pbf=True`` it is ``sqrt(pi / 2) * r`` instead (fitburst's
``normalize_pbf`` flag), which makes the profile tend to the unscattered
Gaussian as the scattering timescale goes to zero.

Two choices differ from fitburst's code, and neither changes the model:

- With scattering on, fitburst clamps times earlier than five widths before
  arrival to that value. That leaves a constant floor of about 1e-5 of the
  peak before each component. FLITS evaluates the expression as written.
- Derivatives are taken on the upsampled grid and then averaged, so the
  Jacobian is exact with time or frequency upsampling. In scintillation mode
  the per-channel amplitudes are solved by linear least squares, and the
  Jacobian accounts for how they move with the nonlinear parameters
  (variable projection, Golub & Pereyra 1973).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from scipy import special

from flits.signal import DM_CONSTANT_S_MHZ2

__all__ = [
    "GLOBAL_PARAMETERS",
    "PARAMETER_NAMES",
    "SCINTILLATION_LINEAR_PARAMETERS",
    "BurstModel",
    "BurstParameters",
    "ParameterLayout",
]

# Fittable parameters, in the order fitburst lays them out in the solver vector.
PARAMETER_NAMES: tuple[str, ...] = (
    "amplitude",
    "arrival_time",
    "burst_width",
    "dm",
    "dm_index",
    "scattering_timescale",
    "scattering_index",
    "spectral_index",
    "spectral_running",
)
GLOBAL_PARAMETERS: tuple[str, ...] = ("dm", "dm_index", "scattering_timescale", "scattering_index")
# In scintillation mode each channel's amplitude is solved for directly, so
# these parameters no longer enter the model.
SCINTILLATION_LINEAR_PARAMETERS: tuple[str, ...] = ("amplitude", "spectral_index", "spectral_running")
REQUIRED_PARAMETERS: tuple[str, ...] = ("amplitude", "arrival_time", "burst_width", "ref_freq")
DEFAULT_PARAMETER_VALUES: dict[str, float] = {
    "dm": 0.0,
    "dm_index": -2.0,
    "scattering_timescale": 0.0,
    "scattering_index": -4.0,
    "spectral_index": 0.0,
    "spectral_running": 0.0,
}
# Fine-grid samples evaluated in one block; larger grids are split by channel
# so that upsampled fits stay within memory.
MAX_BLOCK_SAMPLES = 2_000_000
# Grids with at least this many fine-grid samples are split across threads.
# The work is limited by memory bandwidth, so more than four threads rarely
# helps, and smaller grids run faster on one.
PARALLEL_MIN_SAMPLES = 300_000
DEFAULT_THREADS = max(1, min(4, os.cpu_count() or 1))

_SQRT_2 = float(np.sqrt(2.0))
_SQRT_PI_OVER_2 = float(np.sqrt(np.pi / 2.0))
_LN_10 = float(np.log(10.0))


@dataclass(frozen=True)
class BurstParameters:
    """Model parameters in fitburst's units, one array entry per component."""

    amplitude: np.ndarray
    arrival_time: np.ndarray
    burst_width: np.ndarray
    spectral_index: np.ndarray
    spectral_running: np.ndarray
    ref_freq: np.ndarray
    dm: float = 0.0
    dm_index: float = -2.0
    scattering_timescale: float = 0.0
    scattering_index: float = -4.0

    @property
    def num_components(self) -> int:
        return int(self.amplitude.size)

    @classmethod
    def from_dict(cls, parameters: Mapping[str, object], num_components: int | None = None) -> BurstParameters:
        """Read a fitburst-style parameter dictionary.

        Each value may be a scalar or a list. A per-component list must have
        one entry per component, and a single value is used for every
        component. Shared parameters use their first entry, as fitburst does.
        Missing ``dm``, index, scattering and spectral terms take fitburst's
        defaults.
        """
        missing = [name for name in REQUIRED_PARAMETERS if parameters.get(name) is None]
        if missing:
            raise ValueError(f"Model parameters are missing {', '.join(missing)}.")
        if num_components is None:
            num_components = int(np.asarray(parameters["arrival_time"], dtype=float).size)
        if num_components < 1:
            raise ValueError("The model needs at least one component.")

        def per_component(name: str) -> np.ndarray:
            raw = parameters.get(name)
            values = np.asarray(DEFAULT_PARAMETER_VALUES[name] if raw is None else raw, dtype=float).ravel()
            if values.size == 1:
                values = np.repeat(values, num_components)
            if values.size != num_components:
                raise ValueError(f"{name} has {values.size} values for {num_components} components.")
            return values.astype(float, copy=True)

        def shared(name: str) -> float:
            raw = parameters.get(name)
            values = np.asarray(DEFAULT_PARAMETER_VALUES[name] if raw is None else raw, dtype=float).ravel()
            if values.size == 0:
                raise ValueError(f"{name} has no value.")
            return float(values[0])

        return cls(
            amplitude=per_component("amplitude"),
            arrival_time=per_component("arrival_time"),
            burst_width=per_component("burst_width"),
            spectral_index=per_component("spectral_index"),
            spectral_running=per_component("spectral_running"),
            ref_freq=per_component("ref_freq"),
            dm=shared("dm"),
            dm_index=shared("dm_index"),
            scattering_timescale=shared("scattering_timescale"),
            scattering_index=shared("scattering_index"),
        )

    def to_dict(self) -> dict[str, list[float]]:
        """Return fitburst's ``model_parameters`` layout, shared values repeated per component."""
        count = self.num_components
        return {
            "amplitude": [float(value) for value in self.amplitude],
            "arrival_time": [float(value) for value in self.arrival_time],
            "burst_width": [float(value) for value in self.burst_width],
            "dm": [float(self.dm)] * count,
            "dm_index": [float(self.dm_index)] * count,
            "ref_freq": [float(value) for value in self.ref_freq],
            "scattering_index": [float(self.scattering_index)] * count,
            "scattering_timescale": [float(self.scattering_timescale)] * count,
            "spectral_index": [float(value) for value in self.spectral_index],
            "spectral_running": [float(value) for value in self.spectral_running],
        }

    def value(self, name: str, component: int | None) -> float:
        attribute = getattr(self, name)
        if name in GLOBAL_PARAMETERS:
            return float(attribute)
        return float(np.asarray(attribute)[0 if component is None else component])


@dataclass(frozen=True)
class ParameterLayout:
    """Where each fitted parameter sits in the optimizer's vector.

    The order is fitburst's: parameters in :data:`PARAMETER_NAMES` order, a
    shared parameter once, and a per-component parameter once per component.
    """

    fit_parameters: tuple[str, ...]
    num_components: int
    entries: tuple[tuple[str, int | None], ...] = field(init=False)

    def __post_init__(self) -> None:
        names = tuple(str(name) for name in self.fit_parameters)
        unknown = sorted(set(names) - set(PARAMETER_NAMES))
        if unknown:
            raise ValueError(f"Unknown model parameters: {', '.join(unknown)}.")
        if len(set(names)) != len(names):
            raise ValueError("Each model parameter can be fitted only once.")
        if self.num_components < 1:
            raise ValueError("The model needs at least one component.")
        entries: list[tuple[str, int | None]] = []
        for name in PARAMETER_NAMES:
            if name not in names:
                continue
            if name in GLOBAL_PARAMETERS:
                entries.append((name, None))
            else:
                entries.extend((name, index) for index in range(self.num_components))
        object.__setattr__(self, "fit_parameters", tuple(name for name in PARAMETER_NAMES if name in names))
        object.__setattr__(self, "entries", tuple(entries))

    @property
    def size(self) -> int:
        return len(self.entries)

    @property
    def labels(self) -> tuple[str, ...]:
        """fitburst's covariance labels: ``dm`` for a shared parameter, ``burst_width2`` for component 2."""
        return tuple(name if component is None else f"{name}{component + 1}" for name, component in self.entries)

    def vector(self, parameters: BurstParameters) -> np.ndarray:
        return np.array([parameters.value(name, component) for name, component in self.entries], dtype=float)

    def with_vector(self, parameters: BurstParameters, vector: np.ndarray) -> BurstParameters:
        values = np.asarray(vector, dtype=float).ravel()
        if values.size != self.size:
            raise ValueError(f"Expected {self.size} parameter values, got {values.size}.")
        updates: dict[str, object] = {}
        for (name, component), value in zip(self.entries, values, strict=True):
            if component is None:
                updates[name] = float(value)
            else:
                array = updates.get(name)
                if not isinstance(array, np.ndarray):
                    array = np.array(getattr(parameters, name), dtype=float, copy=True)
                    updates[name] = array
                array[component] = float(value)
        return _replace(parameters, updates)

    def grouped(self, vector: np.ndarray) -> dict[str, list[float]]:
        """Group a vector by parameter name, as fitburst reports ``bestfit_parameters``."""
        values = np.asarray(vector, dtype=float).ravel()
        grouped: dict[str, list[float]] = {}
        for (name, _component), value in zip(self.entries, values, strict=True):
            grouped.setdefault(name, []).append(float(value))
        return grouped


def _replace(parameters: BurstParameters, updates: Mapping[str, object]) -> BurstParameters:
    fields = {
        "amplitude": parameters.amplitude,
        "arrival_time": parameters.arrival_time,
        "burst_width": parameters.burst_width,
        "spectral_index": parameters.spectral_index,
        "spectral_running": parameters.spectral_running,
        "ref_freq": parameters.ref_freq,
        "dm": parameters.dm,
        "dm_index": parameters.dm_index,
        "scattering_timescale": parameters.scattering_timescale,
        "scattering_index": parameters.scattering_index,
    }
    fields.update(updates)
    return BurstParameters(**fields)  # type: ignore[arg-type]


# Above this argument erfcx's asymptotic series is more accurate than the
# direct expressions, which subtract nearly equal terms.
_SERIES_THRESHOLD = 25.0
_INV_SQRT_PI = float(1.0 / np.sqrt(np.pi))


def _scattering_terms(
    argument: np.ndarray, gaussian: np.ndarray, shape: np.ndarray, scaled: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``g * phi(x)`` and ``g * chi(x)`` for the scattered-profile derivatives.

    ``phi(x) = x erfcx(x) - 1/sqrt(pi)`` and ``chi(x) = d(x erfcx(x))/dx``;
    ``g`` is the Gaussian factor and ``shape`` the profile ``g erfcx(x)``.
    ``scaled`` is ``erfcx(|x|)``. Both terms are accurate to about 1e-10.
    """
    x = argument
    positive = x >= 0.0
    phi = x * scaled - _INV_SQRT_PI
    chi = 2.0 * x * phi + scaled
    safe = np.maximum(x, _SERIES_THRESHOLD)
    y = 1.0 / (safe * safe)
    phi_series = _INV_SQRT_PI * y * (-0.5 + y * (0.75 + y * (-1.875 + y * (6.5625 - 29.53125 * y))))
    chi_series = _INV_SQRT_PI * (y / safe) * (1.0 + y * (-3.0 + y * (11.25 + y * (-52.5 + 295.3125 * y))))
    large = x >= _SERIES_THRESHOLD
    phi = np.where(large, phi_series, phi)
    chi = np.where(large, chi_series, chi)
    # For x < 0 the profile is the scattering tail; there g phi = x h - g/sqrt(pi)
    # and g chi = 2 x g phi + h, with no cancellation.
    g_phi = np.where(positive, gaussian * phi, x * shape - _INV_SQRT_PI * gaussian)
    g_chi = np.where(positive, gaussian * chi, 2.0 * x * g_phi + shape)
    return g_phi, g_chi


@dataclass
class _Evaluation:
    model: np.ndarray
    jacobian: np.ndarray | None
    components: np.ndarray | None
    amplitudes: np.ndarray | None
    normal: tuple[float, np.ndarray, np.ndarray] | None = None


@dataclass
class _Block:
    """Model terms for one block of channels on the data grid, shaped (component, channel, time)."""

    components: np.ndarray
    derivatives: dict[str, np.ndarray]


class BurstModel:
    """The fitburst model on a fixed frequency and time grid.

    Parameters
    ----------
    freqs_mhz
        Channel centre frequencies in MHz, shape ``(n_channels,)``.
    times_s
        Sample times in seconds, shape ``(n_time,)``, or ``(n_channels, n_time)``
        when each channel has its own time axis (data that are not dedispersed).
    dm_incoherent
        DM the data were dedispersed to. With frequency upsampling the model
        then includes the dispersion smearing within each channel.
    factor_freq_upsample, factor_time_upsample
        Number of sub-channels and sub-samples the model is evaluated on before
        it is averaged to the data grid.
    is_folded
        Treat the time axis as one pulse period and add the next period's
        profile to it, as fitburst does for folded pulsar data.
    scintillation
        Solve each channel's component amplitudes from the data instead of
        modelling them with ``amplitude`` and the running power law.
    normalize_pbf
        Use fitburst's normalized scattered profile (see the module notes).
    channel_width_mhz
        Width used to place sub-channels. Defaults to the spacing of the first
        two channels, which is what fitburst uses.
    threads
        Threads used for large grids. Defaults to the number of CPUs, up to 4.
    """

    def __init__(
        self,
        freqs_mhz: Iterable[float] | np.ndarray,
        times_s: Iterable[float] | np.ndarray,
        *,
        dm_incoherent: float = 0.0,
        factor_freq_upsample: int = 1,
        factor_time_upsample: int = 1,
        is_folded: bool = False,
        scintillation: bool = False,
        normalize_pbf: bool = False,
        channel_width_mhz: float | None = None,
        threads: int | None = None,
    ) -> None:
        freqs = np.asarray(freqs_mhz, dtype=float).ravel()
        times = np.asarray(times_s, dtype=float)
        if freqs.size == 0:
            raise ValueError("The model needs at least one frequency channel.")
        if not np.all(np.isfinite(freqs)) or np.any(freqs <= 0):
            raise ValueError("Channel frequencies must be positive and finite.")
        if times.ndim not in (1, 2) or times.shape[-1] < 2:
            raise ValueError("times_s must have shape (n_time,) or (n_channels, n_time) with n_time >= 2.")
        if times.ndim == 2 and times.shape[0] != freqs.size:
            raise ValueError("A per-channel time axis needs one row per frequency channel.")
        if not np.all(np.isfinite(times)):
            raise ValueError("Sample times must be finite.")
        self.factor_freq_upsample = max(1, int(factor_freq_upsample))
        self.factor_time_upsample = max(1, int(factor_time_upsample))
        if channel_width_mhz is None:
            channel_width_mhz = float(abs(freqs[1] - freqs[0])) if freqs.size > 1 else 0.0
        if self.factor_freq_upsample > 1 and not channel_width_mhz > 0:
            raise ValueError("Frequency upsampling needs a positive channel width.")

        self.freqs_mhz = freqs
        self.times_s = times
        self.dm_incoherent = float(dm_incoherent)
        self.is_folded = bool(is_folded)
        self.scintillation = bool(scintillation)
        self.normalize_pbf = bool(normalize_pbf)
        self.channel_width_mhz = float(channel_width_mhz)
        self.threads = DEFAULT_THREADS if threads is None else max(1, int(threads))
        self.num_freq = int(freqs.size)
        self.num_time = int(times.shape[-1])
        self.has_freq_dependent_times = times.ndim == 2

        sub_offsets = (np.arange(self.factor_freq_upsample) + 0.5) / self.factor_freq_upsample - 0.5
        self._fine_freqs = freqs[:, None] + self.channel_width_mhz * sub_offsets[None, :]
        self._fine_times = self._upsample_times(times)

    @property
    def shape(self) -> tuple[int, int]:
        return self.num_freq, self.num_time

    def _upsample_times(self, times: np.ndarray) -> np.ndarray:
        factor = self.factor_time_upsample
        rows = np.atleast_2d(times)
        resolution = np.abs(rows[:, 1] - rows[:, 0])
        num_fine = self.num_time * factor
        # fitburst spreads the sub-samples evenly across the outer bin edges.
        start = rows[:, 0] - resolution / 2.0 + resolution / (2.0 * factor)
        stop = rows[:, -1] + resolution / 2.0 - resolution / (2.0 * factor)
        fine = np.linspace(start, stop, num=num_fine, axis=1)
        if self.is_folded:
            period = (resolution / factor * num_fine)[:, None]
            fine = np.concatenate([fine, fine + period], axis=1)
        return fine if times.ndim == 2 else fine[0]

    # ------------------------------------------------------------------
    # Public evaluation

    def evaluate(self, parameters: BurstParameters, data: np.ndarray | None = None) -> np.ndarray:
        """Return the model dynamic spectrum, shape ``(n_channels, n_time)``.

        Scintillation mode solves each channel's amplitudes against ``data``.
        """
        return self._run(parameters, None, data).model

    def components(self, parameters: BurstParameters, data: np.ndarray | None = None) -> np.ndarray:
        """Return each component's dynamic spectrum, shape ``(n_components, n_channels, n_time)``."""
        components = self._run(parameters, None, data, keep_components=True).components
        assert components is not None
        return components

    def channel_amplitudes(self, parameters: BurstParameters, data: np.ndarray) -> np.ndarray:
        """Return the per-channel component amplitudes solved in scintillation mode, shape ``(n_channels, K)``."""
        if not self.scintillation:
            raise ValueError("Per-channel amplitudes exist only in scintillation mode.")
        amplitudes = self._run(parameters, None, data).amplitudes
        assert amplitudes is not None
        return amplitudes

    def evaluate_with_jacobian(
        self,
        parameters: BurstParameters,
        layout: ParameterLayout,
        data: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return the model and its derivatives with respect to ``layout``'s parameters.

        The Jacobian has shape ``(layout.size, n_channels, n_time)``.
        """
        if layout.num_components != parameters.num_components:
            raise ValueError("The parameter layout and the parameters disagree on the number of components.")
        if self.scintillation:
            inactive = sorted(set(layout.fit_parameters) & set(SCINTILLATION_LINEAR_PARAMETERS))
            if inactive:
                raise ValueError(f"{', '.join(inactive)} are solved per channel in scintillation mode.")
        evaluation = self._run(parameters, layout, data)
        assert evaluation.jacobian is not None
        return evaluation.model, evaluation.jacobian

    def normal_equations(
        self,
        parameters: BurstParameters,
        layout: ParameterLayout,
        data: np.ndarray,
        weights: np.ndarray,
    ) -> tuple[float, np.ndarray, np.ndarray]:
        """Return chi-squared, ``J^T J`` and ``J^T r`` for ``r = weights * (data - model)``.

        ``J`` is the Jacobian of ``r`` with respect to ``layout``'s parameters
        and ``weights`` holds one value per channel. The sums are accumulated
        block by block, so the full Jacobian is never held in memory.
        """
        if layout.num_components != parameters.num_components:
            raise ValueError("The parameter layout and the parameters disagree on the number of components.")
        channel_weights = np.asarray(weights, dtype=float).ravel()
        if channel_weights.size != self.num_freq:
            raise ValueError("weights needs one value per channel.")
        evaluation = self._run(parameters, layout, data, reduce_weights=channel_weights, values=data)
        assert evaluation.normal is not None
        return evaluation.normal

    # ------------------------------------------------------------------
    # Block evaluation

    def _require_data(self, data: np.ndarray | None) -> np.ndarray:
        if data is None:
            raise ValueError("Scintillation mode needs the data to solve per-channel amplitudes.")
        values = np.asarray(data, dtype=float)
        if values.shape != self.shape:
            raise ValueError(f"Data shape {values.shape} does not match the model grid {self.shape}.")
        return values

    def _block_ranges(self, num_components: int) -> list[tuple[int, int]]:
        samples_per_channel = num_components * self.factor_freq_upsample * self._fine_times.shape[-1]
        channels = max(1, MAX_BLOCK_SAMPLES // max(1, samples_per_channel))
        if self.threads > 1 and samples_per_channel * self.num_freq >= PARALLEL_MIN_SAMPLES:
            channels = min(channels, -(-self.num_freq // self.threads))
        return [(start, min(start + channels, self.num_freq)) for start in range(0, self.num_freq, channels)]

    def _run(
        self,
        parameters: BurstParameters,
        layout: ParameterLayout | None,
        data: np.ndarray | None,
        *,
        keep_components: bool = False,
        reduce_weights: np.ndarray | None = None,
        values: np.ndarray | None = None,
    ) -> _Evaluation:
        """Evaluate the model block by block, in parallel threads when the grid is large.

        With ``reduce_weights`` each block's Jacobian is folded into the normal
        equations for the weighted residual against ``values`` and discarded.
        """
        count = parameters.num_components
        if self.scintillation or values is not None:
            values = self._require_data(data if values is None else values)
        names = () if layout is None else layout.fit_parameters
        reduce = reduce_weights is not None and layout is not None
        model = np.empty(self.shape, dtype=float)
        jacobian = None if layout is None or reduce else np.empty((layout.size, *self.shape), dtype=float)
        components = np.empty((count, *self.shape), dtype=float) if keep_components else None
        amplitudes = np.empty((self.num_freq, count), dtype=float) if self.scintillation else None
        ranges = self._block_ranges(count)
        partials: list[tuple[float, np.ndarray, np.ndarray] | None] = [None] * len(ranges)

        def work(index: int) -> None:
            start, stop = ranges[index]
            block = self._block(parameters, start, stop, names)
            terms = block.components
            columns: np.ndarray | None = None
            if layout is not None:
                columns = (
                    np.empty((layout.size, stop - start, self.num_time), dtype=float)
                    if jacobian is None
                    else jacobian[:, start:stop]
                )
            if self.scintillation:
                assert amplitudes is not None and values is not None
                local = values[start:stop]
                basis = terms.transpose(2, 1, 0)  # (t, c, K)
                gram_inverse = np.linalg.pinv(np.einsum("tck,tcl->ckl", basis, basis))
                solved = np.einsum("ckl,cl->ck", gram_inverse, np.einsum("tck,ct->ck", basis, local))
                amplitudes[start:stop] = solved
                model[start:stop] = np.einsum("tck,ck->ct", basis, solved)
                if components is not None:
                    components[:, start:stop] = terms * solved.T[:, :, None]
                if columns is not None and layout is not None:
                    residual = local - model[start:stop]
                    self._projected_columns(columns, layout, block.derivatives, basis, gram_inverse, solved, residual)
            else:
                np.sum(terms, axis=0, out=model[start:stop])
                if components is not None:
                    components[:, start:stop] = terms
                if columns is not None and layout is not None:
                    for column, (name, component) in enumerate(layout.entries):
                        derivative = block.derivatives[name]
                        if component is None:
                            np.sum(derivative, axis=0, out=columns[column])
                        else:
                            columns[column] = derivative[component]
            if reduce and columns is not None:
                assert reduce_weights is not None and values is not None
                local_weights = reduce_weights[start:stop, None]
                residual_vector = (local_weights * (values[start:stop] - model[start:stop])).ravel()
                columns *= -local_weights[None, :, :]
                flat = columns.reshape(columns.shape[0], -1)
                partials[index] = (float(residual_vector @ residual_vector), flat @ flat.T, flat @ residual_vector)

        if self.threads > 1 and len(ranges) > 1:
            # NumPy and SciPy release the GIL inside their array loops, so the
            # blocks run concurrently.
            with ThreadPoolExecutor(max_workers=min(self.threads, len(ranges))) as pool:
                for _ in pool.map(work, range(len(ranges))):
                    pass
        else:
            for index in range(len(ranges)):
                work(index)
        normal: tuple[float, np.ndarray, np.ndarray] | None = None
        if reduce:
            finished = [partial for partial in partials if partial is not None]
            normal = (
                float(sum(partial[0] for partial in finished)),
                np.sum([partial[1] for partial in finished], axis=0),
                np.sum([partial[2] for partial in finished], axis=0),
            )
        return _Evaluation(model=model, jacobian=jacobian, components=components, amplitudes=amplitudes, normal=normal)

    def _coarse(self, fine: np.ndarray) -> np.ndarray:
        """Average a fine-grid array ``(K, c, sub-channel, fine time)`` onto the data grid."""
        values = fine
        if self.is_folded:
            half = values.shape[-1] // 2
            values = 0.5 * (values[..., :half] + values[..., half:])
        values = values[:, :, 0, :] if self.factor_freq_upsample == 1 else values.mean(axis=2)
        if self.factor_time_upsample > 1:
            values = values.reshape(*values.shape[:-1], self.num_time, self.factor_time_upsample).mean(axis=-1)
        return values

    def _block(self, parameters: BurstParameters, start: int, stop: int, names: tuple[str, ...]) -> _Block:
        # Shapes: K components, c channels, F sub-channels, N fine samples.
        fine_freqs = self._fine_freqs[start:stop]  # (c, F)
        if self.has_freq_dependent_times:
            times = self._fine_times[start:stop][None, :, None, :]  # (1, c, 1, N)
        else:
            times = self._fine_times[None, None, None, :]  # (1, 1, 1, N)
        freqs = fine_freqs[None, :, :]  # (1, c, F)
        ref_freq = parameters.ref_freq[:, None, None]  # (K, 1, 1)
        width = parameters.burst_width[:, None, None, None]  # (K, 1, 1, 1)
        dm_index = parameters.dm_index
        dm_total = self.dm_incoherent + parameters.dm

        freq_power = freqs**dm_index
        ref_power = ref_freq**dm_index
        delay = DM_CONSTANT_S_MHZ2 * dm_total * (freq_power - ref_power)
        removes_incoherent = not self.has_freq_dependent_times and self.dm_incoherent != 0.0
        if removes_incoherent:
            # The shared time axis is already aligned at each channel centre for
            # the incoherent DM; only the residual DM and the smearing inside a
            # channel remain.
            centre_power = self.freqs_mhz[start:stop][None, :, None] ** dm_index
            delay = delay - DM_CONSTANT_S_MHZ2 * self.dm_incoherent * (centre_power - ref_power)
        arrival = parameters.arrival_time[:, None, None] + delay  # (K, c, F)
        z = (times - arrival[..., None]) / width  # (K, c, F, N)

        log_ratio = np.log(freqs / ref_freq)  # (K, c, F)
        gaussian = np.exp(-0.5 * z * z)
        scattered = parameters.scattering_timescale > 0.0
        if scattered:
            tau = parameters.scattering_timescale * np.exp(parameters.scattering_index * log_ratio)
            ratio = (width[..., 0] / tau)[..., None]  # (K, c, F, 1)
            argument = (ratio - z) / _SQRT_2
            scaled = special.erfcx(np.abs(argument))
            negative = argument < 0.0
            with np.errstate(over="ignore"):
                tail = np.exp(np.where(negative, ratio * (0.5 * ratio - z), -np.inf))
            # erfcx(x) = 2 exp(x^2) - erfcx(-x); written this way neither branch overflows.
            shape = np.where(negative, 2.0 * tail - gaussian * scaled, gaussian * scaled)
            if self.normalize_pbf:
                prefactor = _SQRT_PI_OVER_2 * ratio
            else:
                prefactor = np.exp(-parameters.scattering_index * log_ratio)[..., None]
        else:
            ratio = None
            shape = gaussian
            prefactor = None

        if self.scintillation:
            scale = None if prefactor is None else prefactor
        else:
            spectrum = np.exp(
                parameters.spectral_index[:, None, None] * log_ratio
                + parameters.spectral_running[:, None, None] * log_ratio**2
            )
            weight = (10.0**parameters.amplitude)[:, None, None] * spectrum
            scale = weight[..., None] if prefactor is None else weight[..., None] * prefactor
        fine = shape if scale is None else scale * shape
        components = self._coarse(fine)
        if not names:
            return _Block(components=components, derivatives={})

        # Derivatives of the profile shape h = exp(-z^2/2) erfcx(x), x = (r - z)/sqrt(2).
        # With g = exp(-z^2/2), phi(x) = x erfcx(x) - 1/sqrt(pi) and chi = d(x erfcx(x))/dx:
        #   dh/dz = -z h - sqrt(2) g phi,   dh/dr = sqrt(2) g phi,
        # and sqrt(2) r g phi + h = g (chi + sqrt(2) z phi). In these forms no
        # derivative is a difference of nearly equal terms, even when r = width / tau
        # is huge and the profile is all but the unscattered Gaussian.
        d_shape_dw: np.ndarray
        d_shape_dtau: np.ndarray | None = None
        d_shape_ds: np.ndarray | None = None
        if scattered:
            assert ratio is not None
            g_phi, g_chi = _scattering_terms(argument, gaussian, shape, scaled)
            g_psi = g_chi + _SQRT_2 * z * g_phi
            d_shape_dz = -z * shape - _SQRT_2 * g_phi
            if self.normalize_pbf:
                # The prefactor sqrt(pi/2) r grows with the width and falls with tau.
                d_shape_dw = z * z * shape + _SQRT_2 * z * g_phi + g_psi
                d_shape_dtau = -g_psi
            else:
                d_shape_dw = z * z * shape + _SQRT_2 * g_phi * (z + ratio)
                d_shape_dtau = -_SQRT_2 * ratio * g_phi
            d_shape_ds = -g_psi
        else:
            d_shape_dz = -z * gaussian
            d_shape_dw = z * z * gaussian

        def scaled_by_model(values: np.ndarray) -> np.ndarray:
            return values if scale is None else scale * values

        derivatives: dict[str, np.ndarray] = {}
        for name in names:
            if name == "amplitude":
                value = _LN_10 * fine
            elif name == "spectral_index":
                value = log_ratio[..., None] * fine
            elif name == "spectral_running":
                value = (log_ratio**2)[..., None] * fine
            elif name == "arrival_time":
                value = -scaled_by_model(d_shape_dz) / width
            elif name == "burst_width":
                value = scaled_by_model(d_shape_dw) / width
            elif name == "dm":
                d_delay = DM_CONSTANT_S_MHZ2 * (freq_power - ref_power)
                value = -scaled_by_model(d_shape_dz) * (d_delay[..., None] / width)
            elif name == "dm_index":
                d_delay = DM_CONSTANT_S_MHZ2 * dm_total * (freq_power * np.log(freqs) - ref_power * np.log(ref_freq))
                if removes_incoherent:
                    centre = self.freqs_mhz[start:stop][None, :, None]
                    d_delay = d_delay - DM_CONSTANT_S_MHZ2 * self.dm_incoherent * (
                        centre**dm_index * np.log(centre) - ref_power * np.log(ref_freq)
                    )
                value = -scaled_by_model(d_shape_dz) * (d_delay[..., None] / width)
            elif name == "scattering_timescale":
                if d_shape_dtau is None:
                    value = np.zeros_like(fine)
                else:
                    value = scaled_by_model(d_shape_dtau) / parameters.scattering_timescale
            elif name == "scattering_index":
                if d_shape_ds is None:
                    value = np.zeros_like(fine)
                else:
                    value = scaled_by_model(d_shape_ds) * log_ratio[..., None]
            else:  # pragma: no cover - ParameterLayout rejects unknown names
                raise ValueError(f"Unknown model parameter {name}.")
            derivatives[name] = self._coarse(np.broadcast_to(value, fine.shape))
        return _Block(components=components, derivatives=derivatives)

    # ------------------------------------------------------------------
    # Scintillation: per-channel amplitudes by variable projection

    @staticmethod
    def _projected_columns(
        out: np.ndarray,
        layout: ParameterLayout,
        derivatives: dict[str, np.ndarray],
        basis: np.ndarray,
        gram_inverse: np.ndarray,
        amplitudes: np.ndarray,
        residual: np.ndarray,
    ) -> None:
        """Write the model's derivatives for one block of channels into ``out`` (P, c, t).

        Each channel's amplitudes solve ``min ||data - B a||``, with ``B`` the
        component profiles. Moving a nonlinear parameter moves ``B`` and with
        it the solved amplitudes; Golub and Pereyra's expression for the
        resulting change of the model is
        ``P_perp dB a + (B^+)^T dB^T r``.
        """
        count = amplitudes.shape[1]
        for column, (name, component) in enumerate(layout.entries):
            derivative = derivatives[name]  # (K, c, t)
            if component is None:
                moved = np.einsum("kct,ck->ct", derivative, amplitudes)
                transposed = np.einsum("kct,ct->ck", derivative, residual)
            else:
                moved = derivative[component] * amplitudes[:, component : component + 1]
                transposed = np.zeros((amplitudes.shape[0], count), dtype=float)
                transposed[:, component] = np.einsum("ct,ct->c", derivative[component], residual)
            correction = np.einsum("tck,ct->ck", basis, moved) - transposed
            out[column] = moved - np.einsum("tck,ck->ct", basis, np.einsum("ckl,cl->ck", gram_inverse, correction))
