#!/usr/bin/env python3
"""Validate FLITS end to end with CIRADA RM-Tools reference Q/U data.

The source data and reference result are downloaded from a pinned RM-Tools
commit and verified by SHA-256 before they are parsed. The generated input is
directly importable in the FLITS browser workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
from urllib.request import urlopen

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flits.analysis import run_rm_synthesis


RMTOOLS_COMMIT = "34cf6fac43f5dac943439fa653327fc7b9fe6c46"
RMTOOLS_ROOT = f"https://raw.githubusercontent.com/CIRADA-Tools/RM-Tools/{RMTOOLS_COMMIT}"
SOURCE_URL = f"{RMTOOLS_ROOT}/tests/RMsynth1D_testdata.dat"
REFERENCE_URL = f"{RMTOOLS_ROOT}/tests/RMsynth1D_referencevalues.json"
LICENSE_URL = f"{RMTOOLS_ROOT}/LICENSE.txt"
SOURCE_SHA256 = "765f0d9cf9935203b88ef93e2dea046a4d1cec9d32746b7b3b54318ecd5cba7a"
REFERENCE_SHA256 = "f9648280658165a042b02a8e57d591c3f8d267c944ed91f3b36582e09652290e"
FLITS_RELEASE_ROOT = "https://github.com/DirkKuiper/flits/releases/download/tutorial-data-v1"
SOURCE_ASSET_NAME = "flits-tutorial-rmtools-reference-v1.dat"
REFERENCE_ASSET_NAME = "flits-tutorial-rmtools-reference-values-v1.json"
SOURCE_DISTRIBUTION_URL = f"{FLITS_RELEASE_ROOT}/{SOURCE_ASSET_NAME}"
REFERENCE_DISTRIBUTION_URL = f"{FLITS_RELEASE_ROOT}/{REFERENCE_ASSET_NAME}"

DEFAULT_INPUT = ROOT / "docs/examples/rmtools-reference-input.json"
DEFAULT_SUMMARY = ROOT / "docs/examples/rmtools-reference-summary.json"
DEFAULT_PLOT = ROOT / "docs/assets/rm-synthesis/rmtools-reference-example.png"
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "flits-rmtools-reference"
DEFAULT_SOURCE = ROOT / "data/RM-Tools" / SOURCE_ASSET_NAME
DEFAULT_REFERENCE = ROOT / "data/RM-Tools" / REFERENCE_ASSET_NAME


def _download_verified(url: str, sha256: str, cache_path: Path) -> bytes:
    """Return a cached or downloaded file only when its checksum matches."""
    if cache_path.exists():
        payload = cache_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() == sha256:
            return payload

    with urlopen(url, timeout=30) as response:
        payload = response.read()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != sha256:
        raise RuntimeError(f"Checksum mismatch for {url}: expected {sha256}, received {actual}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(payload)
    return payload


def _read_or_download_verified(path: Path, url: str, sha256: str, cache_path: Path) -> bytes:
    """Prefer a local tutorial asset and fall back to its release download."""
    if path.exists():
        payload = path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != sha256:
            raise RuntimeError(f"Checksum mismatch for {path}: expected {sha256}, received {actual}")
        return payload
    return _download_verified(url, sha256, cache_path)


def _load_source(payload: bytes) -> np.ndarray:
    values = np.loadtxt(io.BytesIO(payload))
    if values.shape != (288, 7):
        raise RuntimeError(f"Expected the 288 × 7 RM-Tools reference table, received {values.shape}")
    if not np.all(np.isfinite(values)):
        raise RuntimeError("RM-Tools reference table contains non-finite values")
    return values


def _browser_input(values: np.ndarray) -> dict[str, Any]:
    freqs_mhz = values[:, 0] / 1e6
    return {
        "schema_version": "flits_rm_input_v1",
        "freqs_mhz": freqs_mhz.tolist(),
        "stokes_q": values[:, 2].tolist(),
        "stokes_u": values[:, 3].tolist(),
        "sigma_q": values[:, 5].tolist(),
        "sigma_u": values[:, 6].tolist(),
        "channel_width_mhz": float(np.median(np.diff(freqs_mhz))),
        "calibration_status": "calibrated",
        "calibration_basis": "Synthetic validation data with fully specified Q/U uncertainties; instrumental calibration is not applicable.",
        "provenance": {
            "creator": "CIRADA RM-Tools project",
            "data_kind": "official synthetic RM-synthesis validation spectrum",
            "source_url": SOURCE_URL,
            "source_distribution_url": SOURCE_DISTRIBUTION_URL,
            "source_sha256": SOURCE_SHA256,
            "reference_url": REFERENCE_URL,
            "reference_distribution_url": REFERENCE_DISTRIBUTION_URL,
            "reference_sha256": REFERENCE_SHA256,
            "rmtools_commit": RMTOOLS_COMMIT,
            "license": "MIT",
            "license_url": LICENSE_URL,
        },
    }


def _plot(path: Path, values: np.ndarray, result: dict[str, Any], expected_rm: float) -> None:
    phi = np.asarray(result["phi_rad_m2"], dtype=float)
    dirty = np.asarray(result["polarized_amplitude"], dtype=float)
    cleaned = np.asarray(result["cleaned_polarized_amplitude"], dtype=float)
    rmsf = np.asarray(result["rmsf_amplitude"], dtype=float)
    freqs_mhz = values[:, 0] / 1e6
    lambda2 = np.square(299_792_458.0 / (freqs_mhz * 1e6))
    lambda0 = float(result["reference_lambda2_m2"])
    angle = np.deg2rad(float(result["polarization_angle_deg"]))
    phase = 2.0 * (angle + float(result["peak_rm_rad_m2"]) * (lambda2 - lambda0))
    amplitude = float(result["peak_polarized_amplitude"])

    figure, axes = plt.subplots(2, 1, figsize=(9.4, 7.4), constrained_layout=True)
    axes[0].plot(phi, dirty, color="#64338c", lw=1.7, label="Dirty |F(φ)|")
    axes[0].plot(phi, cleaned, color="#cb5b35", lw=1.4, label="RM-CLEAN |F(φ)|")
    axes[0].plot(phi, rmsf, color="#2f7895", lw=1.1, ls="--", label="|RMSF|")
    axes[0].axvline(expected_rm, color="#222222", lw=1.0, ls=":", label="RM-Tools reference")
    axes[0].set(xlabel="Faraday depth φ (rad m⁻²)", ylabel="Amplitude", xlim=(-600, 600))
    axes[0].grid(alpha=0.18)
    axes[0].legend(frameon=False, ncol=2)

    order = np.argsort(lambda2)
    axes[1].errorbar(
        lambda2[order], values[order, 2], yerr=values[order, 5], fmt=".", ms=3.2,
        color="#64338c", alpha=0.55, elinewidth=0.5, label="Q",
    )
    axes[1].errorbar(
        lambda2[order], values[order, 3], yerr=values[order, 6], fmt=".", ms=3.2,
        color="#2f7895", alpha=0.55, elinewidth=0.5, label="U",
    )
    axes[1].plot(lambda2[order], amplitude * np.cos(phase[order]), color="#64338c", lw=1.5)
    axes[1].plot(lambda2[order], amplitude * np.sin(phase[order]), color="#2f7895", lw=1.5)
    axes[1].set(xlabel="Wavelength squared λ² (m²)", ylabel="Stokes Q, U")
    axes[1].grid(alpha=0.18)
    axes[1].legend(frameon=False, ncol=2)
    figure.suptitle("FLITS validation with the official RM-Tools Q/U reference spectrum", fontsize=13.5)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=170)
    plt.close(figure)


def build_example(args: argparse.Namespace) -> dict[str, Any]:
    source_payload = _read_or_download_verified(
        args.source,
        SOURCE_DISTRIBUTION_URL,
        SOURCE_SHA256,
        args.cache_dir / SOURCE_ASSET_NAME,
    )
    reference_payload = _read_or_download_verified(
        args.reference,
        REFERENCE_DISTRIBUTION_URL,
        REFERENCE_SHA256,
        args.cache_dir / REFERENCE_ASSET_NAME,
    )
    values = _load_source(source_payload)
    reference = json.loads(reference_payload)
    browser_input = _browser_input(values)

    result = run_rm_synthesis(
        freqs_mhz=np.asarray(browser_input["freqs_mhz"]),
        stokes_q=np.asarray(browser_input["stokes_q"]),
        stokes_u=np.asarray(browser_input["stokes_u"]),
        sigma_q=np.asarray(browser_input["sigma_q"]),
        sigma_u=np.asarray(browser_input["sigma_u"]),
        channel_width_mhz=float(browser_input["channel_width_mhz"]),
        phi_min_rad_m2=-600.0,
        phi_max_rad_m2=600.0,
        phi_step_rad_m2=1.0,
        clean=True,
    ).to_dict()
    if result["status"] != "ok":
        raise RuntimeError(f"FLITS RM synthesis failed: {result['message']}")

    expected_rm = float(reference["phiPeakPIfit_rm2"])
    rm_difference = abs(float(result["peak_rm_rad_m2"]) - expected_rm)
    amplitude_difference = abs(float(result["peak_polarized_amplitude"]) - float(reference["ampPeakPIfit"]))
    snr_difference = abs(float(result["peak_snr"]) - float(reference["snrPIfit"]))
    checks = {
        "channel_count_matches": int(result["channel_count"]) == int(reference["N_channels"]),
        "rm_difference_below_0_5_rad_m2": rm_difference < 0.5,
        "amplitude_difference_below_0_001": amplitude_difference < 0.001,
        "snr_difference_below_0_1": snr_difference < 0.1,
    }
    if not all(checks.values()):
        raise RuntimeError(f"FLITS did not reproduce the RM-Tools reference: {checks}")

    summary = {
        "schema_version": "flits_rmtools_reference_example_v1",
        "interpretation_status": "validation_passed",
        "provenance": browser_input["provenance"],
        "input": {
            "channel_count": int(result["channel_count"]),
            "frequency_range_mhz": [float(np.min(values[:, 0]) / 1e6), float(np.max(values[:, 0]) / 1e6)],
            "channel_width_mhz": browser_input["channel_width_mhz"],
            "columns": ["frequency_hz", "stokes_i", "stokes_q", "stokes_u", "sigma_i", "sigma_q", "sigma_u"],
        },
        "rmtools_reference": {
            "peak_rm_rad_m2": expected_rm,
            "peak_rm_uncertainty_rad_m2": float(reference["dPhiPeakPIfit_rm2"]),
            "peak_polarized_amplitude": float(reference["ampPeakPIfit"]),
            "peak_snr": float(reference["snrPIfit"]),
            "reference_lambda2_m2": float(reference["lam0Sq_m2"]),
            "rmsf_fwhm_rad_m2": float(reference["fwhmRMSF"]),
        },
        "flits_result": {
            "peak_rm_rad_m2": result["peak_rm_rad_m2"],
            "peak_rm_uncertainty_rad_m2": result["peak_rm_uncertainty_rad_m2"],
            "peak_polarized_amplitude": result["peak_polarized_amplitude"],
            "peak_snr": result["peak_snr"],
            "reference_lambda2_m2": result["reference_lambda2_m2"],
            "rmsf_fwhm_rad_m2": result["rmsf_fwhm_rad_m2"],
            "reduced_chi_square": result["reduced_chi_square"],
            "effective_channel_count": result["effective_channel_count"],
            "clean_iterations": result["clean_iterations"],
        },
        "differences": {
            "absolute_rm_rad_m2": rm_difference,
            "absolute_peak_amplitude": amplitude_difference,
            "absolute_peak_snr": snr_difference,
        },
        "validation_checks": checks,
    }

    args.input.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.input.write_text(json.dumps(browser_input, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    args.summary.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    _plot(args.plot, values, result, expected_rm)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Local raw Q/U table; falls back to the checksum-verified FLITS release asset when absent.",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=DEFAULT_REFERENCE,
        help="Local RM-Tools reference JSON; falls back to the verified FLITS release asset when absent.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--plot", type=Path, default=DEFAULT_PLOT)
    args = parser.parse_args()
    print(json.dumps(build_example(args), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
