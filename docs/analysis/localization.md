# Automatic Burst Localization

The **Auto Localize** button (and the `auto_localize` session action) finds
where the burst lives in the current crop and sets the event window, the
spectral window, and the off-pulse windows in one step. It replaces the
first, most error-prone part of the manual workflow; every selection it
makes can still be refined by hand afterwards.

## What it does

1. Per-channel robust normalization (median/MAD computed off-event).
2. Matched filtering of the band-integrated S/N profile over a ladder of
   boxcar widths to find the burst and its characteristic width.
3. The event window is the contiguous above-noise region around the
   matched peak, unioned with any nearby independently detected
   components, with the boundaries refined at a finer smoothing scale so
   they land at the true signal edge rather than the matched-filter
   shoulder.
4. The spectral window comes from the on-burst channel S/N over the tight
   event window: the contiguous significant band around the strongest
   channel, merged with nearby significant sub-bands (so patchy
   scintillation stays inside one window). If the result covers ≳95% of
   the usable band the burst is treated as broadband and the full band is
   kept.
5. The time and frequency searches iterate — a band-limited burst is
   re-detected against the noise of its own sub-band, which typically
   raises the integrated S/N substantially — until the selections are
   stable.
6. Off-pulse windows are placed on both sides of the event with a guard
   margin.

## Reading the result

The toast (and the `localization` field in the action response) reports:

- `status` — `ok`, `low_sn` (detected, but integrated S/N below the
  threshold), or `no_detection` (nothing crossed the detection threshold;
  the session selections are left unchanged).
- `detection_snr` / `integrated_snr` — matched-filter and event-integrated
  S/N over the selected band.
- `band_limited` — whether a sub-band was selected.
- `warning_flags` — `event_near_edge`, `wide_event`, `band_touches_edge`,
  `low_integrated_snr`, `no_offpulse_window`. Treat flagged selections as
  candidates for manual review.

## Notes

- Localization runs at the native time resolution of the crop, regardless
  of the current display reduction.
- Measurements are still computed at the session's time/frequency factors;
  for sub-millisecond structure work set the time factor to 1 before
  `Compute`.
- The detection threshold defaults to S/N 6 and can be passed in the
  action payload (`detection_snr_threshold`).
