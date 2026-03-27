from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import DM_phase

from flits.analysis.dm_optimization import dm_trial_grid
from flits.session import BurstSession


def _run_upstream_dm_phase(
    waterfall: np.ndarray,
    trial_dms: np.ndarray,
    tsamp_sec: float,
    freqs_mhz: np.ndarray,
) -> tuple[float, float]:
    original_arange = DM_phase.np.arange

    def _compat_arange(start, *args, **kwargs):
        values = [start, *args]
        normalized: list[object] = []
        for value in values:
            if isinstance(value, np.ndarray):
                array = np.asarray(value)
                if array.size == 1:
                    normalized.append(int(array.reshape(-1)[0]))
                    continue
            normalized.append(value)
        return original_arange(*normalized, **kwargs)

    DM_phase.np.arange = _compat_arange
    try:
        return DM_phase.get_dm(
            waterfall,
            trial_dms,
            tsamp_sec,
            freqs_mhz,
            no_plots=True,
        )
    finally:
        DM_phase.np.arange = original_arange


def _compute_upstream_dmphase_curve(
    waterfall: np.ndarray,
    trial_dms: np.ndarray,
    tsamp_sec: float,
    freqs_mhz: np.ndarray,
) -> np.ndarray:
    trial_dms = np.asarray(trial_dms, dtype=float)
    nbin = waterfall.shape[1] // 2
    power_spectra = np.zeros((nbin, trial_dms.size), dtype=float)
    for index, dm in enumerate(trial_dms):
        dedispersed = DM_phase._dedisperse_waterfall(waterfall, float(dm), freqs_mhz, float(tsamp_sec))
        power_spectrum = DM_phase._get_coherent_power_spectrum(dedispersed)
        power_spectra[:, index] = np.asarray(power_spectrum[:nbin], dtype=float)

    fluctuation_index = np.arange(0, nbin, dtype=float)
    dpower_spectra = power_spectra * fluctuation_index[:, np.newaxis] ** 2
    dm_curve, _, snr = DM_phase._get_dm_curve(power_spectra, dpower_spectra, len(freqs_mhz))
    dm_curve = np.asarray(dm_curve, dtype=float)
    snr = np.asarray(snr, dtype=float)
    dm_curve[snr < 5.0] = dm_curve[snr < 5.0] / 1e6
    return dm_curve


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare FLITS DMphase against upstream DM_phase on a real burst.")
    parser.add_argument("bfile", help="Path to the filterbank file.")
    parser.add_argument("--dm", type=float, required=True, help="Current/applied DM used to load the session.")
    parser.add_argument("--center-dm", type=float, default=None, help="Center DM for the sweep. Defaults to --dm.")
    parser.add_argument("--half-range", type=float, default=2.0, help="Sweep half-range in pc/cm^3.")
    parser.add_argument("--step", type=float, default=0.05, help="Sweep step in pc/cm^3.")
    parser.add_argument("--event-half-width-ms", type=float, default=2.0, help="Half-width around the auto peak for the event window.")
    parser.add_argument("--component-half-width-ms", type=float, default=0.5, help="Half-width for the component region around the auto peak.")
    parser.add_argument("--telescope", default=None, help="Optional FLITS telescope preset override.")
    parser.add_argument("--dump-csv", default=None, help="Optional path to write trial-by-trial FLITS vs upstream DMphase curves.")
    args = parser.parse_args()

    center_dm = float(args.dm if args.center_dm is None else args.center_dm)
    session = BurstSession.from_file(str(Path(args.bfile).expanduser().resolve()), dm=float(args.dm), telescope=args.telescope)
    peak_ms = float(session.get_view()["state"]["peak_ms"][0])
    session.set_event_ms(peak_ms - float(args.event_half_width_ms), peak_ms + float(args.event_half_width_ms))
    session.add_region_ms(peak_ms - float(args.component_half_width_ms), peak_ms + float(args.component_half_width_ms))

    trial_dms, _ = dm_trial_grid(center_dm, float(args.half_range), float(args.step))
    flits_result = session.optimize_dm(
        center_dm=center_dm,
        half_range=float(args.half_range),
        step=float(args.step),
        metric="dm_phase",
    )

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
    upstream_curve = _compute_upstream_dmphase_curve(
        waterfall,
        trial_dms,
        grid.effective_tsamp_ms / 1000.0,
        freqs,
    )
    flits_index = int(np.nanargmax(flits_result.snr))
    upstream_refined_index = int(np.argmin(np.abs(trial_dms - upstream_dm)))
    upstream_curve_index = int(np.nanargmax(upstream_curve))

    print(f"file: {Path(args.bfile).name}")
    print(f"peak_ms: {peak_ms:.6f}")
    print(f"event_ms: [{peak_ms - float(args.event_half_width_ms):.6f}, {peak_ms + float(args.event_half_width_ms):.6f}]")
    print(f"trial_count: {len(trial_dms)}")
    print(f"FLITS sampled best DM: {flits_result.sampled_best_dm:.6f}")
    print(f"FLITS refined best DM: {flits_result.best_dm:.6f}")
    print(f"FLITS refined uncertainty: {flits_result.best_dm_uncertainty}")
    print(f"FLITS sampled DMphase score: {flits_result.sampled_best_sn:.6f}")
    print(f"DM_phase best DM: {upstream_dm:.6f} +/- {upstream_err:.6f}")
    print(f"|sampled-upstream|: {abs(flits_result.sampled_best_dm - upstream_dm):.6f}")
    print(f"|refined-upstream|: {abs(flits_result.best_dm - upstream_dm):.6f}")
    print(f"curve-peak delta: {abs(flits_index - upstream_curve_index)}")
    print(f"refined-index delta: {abs(flits_index - upstream_refined_index)}")
    print(
        "top-curve bins: "
        f"FLITS={trial_dms[flits_index]:.6f}, "
        f"upstream_curve={trial_dms[upstream_curve_index]:.6f}"
    )

    if args.dump_csv:
        dump_path = Path(args.dump_csv).expanduser().resolve()
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        with dump_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["trial_dm", "flits_score", "upstream_score"])
            for dm, flits_score, upstream_score in zip(trial_dms, flits_result.snr, upstream_curve):
                writer.writerow([f"{float(dm):.12f}", f"{float(flits_score):.12f}", f"{float(upstream_score):.12f}"])
        print(f"curve_csv: {dump_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
