from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from flits.analysis.dm_optimization import dm_trial_grid

TEST_HELPERS_PATH = ROOT / "tests" / "test_dm_optimization.py"
SPEC = importlib.util.spec_from_file_location("verify_dmphase_test_helpers", TEST_HELPERS_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load test helpers from {TEST_HELPERS_PATH}")
TEST_HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TEST_HELPERS)

_run_upstream_dm_phase = TEST_HELPERS._run_upstream_dm_phase
_synthetic_complex_dm_session = TEST_HELPERS._synthetic_complex_dm_session
_synthetic_dm_session = TEST_HELPERS._synthetic_dm_session


def _comparison_row(name: str, session, *, center_dm: float = 50.0, half_range: float = 8.0, step: float = 0.5) -> str:
    trial_dms, _ = dm_trial_grid(center_dm, half_range, step)
    flits_result = session.optimize_dm(center_dm=center_dm, half_range=half_range, step=step, metric="dm_phase")

    grid = session._reduced_analysis_grid()
    waterfall = np.asarray(grid.masked[grid.spec_lo : grid.spec_hi + 1, :], dtype=float)
    freqs = np.asarray(grid.freqs_mhz[grid.spec_lo : grid.spec_hi + 1], dtype=float)
    valid_rows = np.isfinite(waterfall).all(axis=1)
    waterfall = waterfall[valid_rows]
    freqs = freqs[valid_rows]
    order = np.argsort(freqs)
    waterfall = waterfall[order]
    freqs = freqs[order]

    upstream_dm, upstream_err = _run_upstream_dm_phase(
        waterfall,
        trial_dms,
        grid.effective_tsamp_ms / 1000.0,
        freqs,
    )
    sampled_delta = abs(flits_result.sampled_best_dm - upstream_dm)
    refined_delta = abs(flits_result.best_dm - upstream_dm)
    upstream_index = int(np.argmin(np.abs(trial_dms - upstream_dm)))
    flits_index = int(np.nanargmax(flits_result.snr))
    index_delta = abs(flits_index - upstream_index)
    return (
        f"{name}: "
        f"FLITS sampled={flits_result.sampled_best_dm:.6f}, "
        f"FLITS refined={flits_result.best_dm:.6f}, "
        f"DM_phase={upstream_dm:.6f} +/- {upstream_err:.6f}, "
        f"|sampled-upstream|={sampled_delta:.6f}, "
        f"|refined-upstream|={refined_delta:.6f}, "
        f"peak-index delta={index_delta}"
    )


def main() -> int:
    rows = [
        _comparison_row("single", _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)),
        _comparison_row("complex", _synthetic_complex_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)),
    ]

    masked = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
    masked.mask_channel_freq(float(masked.freqs[3]))
    masked.mask_channel_freq(float(masked.freqs[7]))
    rows.append(_comparison_row("masked", masked))

    reduced = _synthetic_dm_session(true_dm=50.0, num_channels=24, noise_std=0.03)
    reduced.set_time_factor(4)
    reduced.set_freq_factor(2)
    rows.append(_comparison_row("reduced", reduced))

    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
