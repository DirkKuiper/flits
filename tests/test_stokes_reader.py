"""The optional full-Stokes reader path, and what it refuses to guess."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import FullStokesWaterfall, write_full_stokes_filterbank

from flits.io import detect_reader, load_filterbank_data, load_stokes_data, reader_supports_stokes
from flits.io.errors import PolarizationUnavailableError
from flits.settings import ObservationConfig


def test_the_sigproc_reader_advertises_a_full_stokes_path(full_stokes_waterfall: FullStokesWaterfall) -> None:
    reader = detect_reader(full_stokes_waterfall.path)
    assert reader_supports_stokes(reader)
    inspection = reader.inspect(full_stokes_waterfall.path)
    assert inspection.polarization_products == 4
    assert inspection.polarization_basis == "coherency_linear"
    assert inspection.polarization_basis_source == "preset:nrt"
    assert inspection.stokes_available


def test_a_single_pol_file_reports_no_stokes_even_though_the_reader_supports_it(synthetic_waterfall) -> None:
    reader = detect_reader(synthetic_waterfall.path)
    inspection = reader.inspect(synthetic_waterfall.path)
    assert inspection.polarization_products == 1
    assert not inspection.stokes_available

    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic")
    with pytest.raises(PolarizationUnavailableError) as excinfo:
        load_stokes_data(synthetic_waterfall.path, config)
    assert excinfo.value.reason == "insufficient_products"


def test_stokes_cube_shares_its_axes_with_the_stokes_i_load(full_stokes_waterfall: FullStokesWaterfall) -> None:
    config = ObservationConfig.from_preset(dm=0.0, preset_key="nrt")
    stokes_i, metadata_i = load_filterbank_data(full_stokes_waterfall.path, config)
    cube, metadata_cube = load_stokes_data(full_stokes_waterfall.path, config)

    assert cube.shape == (4, *stokes_i.shape)
    np.testing.assert_allclose(metadata_cube.freqs_mhz, metadata_i.freqs_mhz)
    assert metadata_cube.tsamp == metadata_i.tsamp
    assert metadata_cube.polarization_basis == "coherency_linear"
    assert metadata_cube.polarization_basis_source == "preset:nrt"
    assert metadata_i.polarization_basis is None

    # The burst lands in the same time bin in both.
    assert int(np.argmax(np.nansum(cube[0], axis=0))) == int(np.argmax(np.nansum(stokes_i, axis=0)))


def test_an_unknown_basis_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    # The generic preset declares no basis, and SIGPROC records none either.
    waterfall = write_full_stokes_filterbank(tmp_path / "unknown_basis.fil", telescope_id=0)
    config = ObservationConfig.from_preset(dm=0.0, preset_key="generic")
    with pytest.raises(PolarizationUnavailableError) as excinfo:
        load_stokes_data(waterfall.path, config)
    assert excinfo.value.reason == "unknown_basis"

    stated = ObservationConfig.from_preset(dm=0.0, preset_key="generic", polarization_basis="coherency_linear")
    cube, metadata = load_stokes_data(waterfall.path, stated)
    assert cube.shape[0] == 4
    assert metadata.polarization_basis_source == "config_override"


def test_an_explicit_basis_overrides_the_preset(full_stokes_waterfall: FullStokesWaterfall) -> None:
    linear = ObservationConfig.from_preset(dm=0.0, preset_key="nrt")
    circular = ObservationConfig.from_preset(dm=0.0, preset_key="nrt", polarization_basis="coherency_circular")
    linear_cube, _ = load_stokes_data(full_stokes_waterfall.path, linear)
    circular_cube, circular_meta = load_stokes_data(full_stokes_waterfall.path, circular)

    assert circular_meta.polarization_basis == "coherency_circular"
    # The circular reading puts what the linear reading calls Q into V.
    np.testing.assert_allclose(linear_cube[1], circular_cube[3], rtol=1e-5, atol=1e-5)


def test_normalization_keeps_the_polarization_fraction_intact(full_stokes_waterfall: FullStokesWaterfall) -> None:
    config = ObservationConfig.from_preset(dm=0.0, preset_key="nrt")
    cube, _ = load_stokes_data(full_stokes_waterfall.path, config)
    burst = full_stokes_waterfall.burst_time_idx
    integrated = np.sum(cube[:, :, burst - 8 : burst + 8], axis=2)
    linear = np.hypot(integrated[1], integrated[2])
    fraction = float(np.sum(linear) / np.sum(integrated[0]))
    # Per-channel scaling must not move the injected linear fraction.
    assert fraction == pytest.approx(full_stokes_waterfall.linear_fraction, abs=0.1)
