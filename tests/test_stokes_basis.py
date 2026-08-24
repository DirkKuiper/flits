"""The polarization-basis layer: which four products a file actually holds."""

from __future__ import annotations

import numpy as np
import pytest

from flits.stokes import (
    BASIS_COHERENCY_CIRCULAR,
    BASIS_COHERENCY_LINEAR,
    BASIS_IQUV,
    PolarizationBasisError,
    basis_from_psrfits,
    normalize_polarization_basis,
    stokes_from_products,
)


def test_normalize_polarization_basis_accepts_aliases_and_rejects_typos() -> None:
    assert normalize_polarization_basis("IQUV") == BASIS_IQUV
    assert normalize_polarization_basis("AABBCRCI") == BASIS_COHERENCY_LINEAR
    assert normalize_polarization_basis("Coherency-Circular") == BASIS_COHERENCY_CIRCULAR
    assert normalize_polarization_basis(None) is None
    assert normalize_polarization_basis("") is None
    assert normalize_polarization_basis("auto") is None
    with pytest.raises(PolarizationBasisError):
        normalize_polarization_basis("iquvv")


def test_basis_from_psrfits_needs_the_feed_to_disambiguate_coherency() -> None:
    assert basis_from_psrfits("IQUV") == BASIS_IQUV
    assert basis_from_psrfits("AABBCRCI", "LIN") == BASIS_COHERENCY_LINEAR
    assert basis_from_psrfits("AABBCRCI", "CIRC") == BASIS_COHERENCY_CIRCULAR
    # Without FD_POLN, AABBCRCI does not say whether AA-BB is Q or V.
    assert basis_from_psrfits("AABBCRCI") is None
    # Total-intensity products can never make a Stokes cube.
    assert basis_from_psrfits("AA+BB", "LIN") is None
    assert basis_from_psrfits("INTEN") is None
    assert basis_from_psrfits(None) is None


def test_linear_feed_conversion_matches_the_psrfits_convention() -> None:
    aa = np.array([[3.0]])
    bb = np.array([[1.0]])
    cr = np.array([[0.5]])
    ci = np.array([[-0.25]])
    stokes = stokes_from_products(np.stack([aa, bb, cr, ci]), BASIS_COHERENCY_LINEAR)
    assert stokes.shape == (4, 1, 1)
    np.testing.assert_allclose(stokes[:, 0, 0], [4.0, 2.0, 1.0, -0.5])


def test_circular_feed_puts_the_product_difference_in_stokes_v() -> None:
    products = np.stack([np.array([[3.0]]), np.array([[1.0]]), np.array([[0.5]]), np.array([[-0.25]])])
    stokes = stokes_from_products(products, BASIS_COHERENCY_CIRCULAR)
    np.testing.assert_allclose(stokes[:, 0, 0], [4.0, 1.0, -0.5, 2.0])


def test_iquv_passes_through_and_does_not_alias_the_input() -> None:
    products = np.arange(4 * 2 * 3, dtype=float).reshape(4, 2, 3)
    stokes = stokes_from_products(products, "iquv")
    np.testing.assert_allclose(stokes, products)
    stokes[0, 0, 0] = -999.0
    assert products[0, 0, 0] == 0.0


def test_stokes_from_products_refuses_without_four_products_or_a_basis() -> None:
    with pytest.raises(PolarizationBasisError):
        stokes_from_products(np.zeros((2, 4, 4)), BASIS_COHERENCY_LINEAR)
    with pytest.raises(PolarizationBasisError):
        stokes_from_products(np.zeros((4, 4, 4)), None)  # type: ignore[arg-type]
