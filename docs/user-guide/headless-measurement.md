# Headless Measurement

`flits measure` measures bursts without a browser. Each file goes through the
same steps the interface offers, in the order a user takes them:

1. Load at the given DM, with the telescope preset detected from the file.
2. Mask interference automatically with `jess` (`--mask auto`).
3. Localize the burst automatically, choosing the event window, the frequency
   band, and off-pulse noise windows on either side.
4. Measure S/N, peak flux, fluence, widths, and arrival times, with the same
   uncertainty classification as the interface.

Any step can be pinned instead of automated. Results go to standard output as
JSON, JSONL, or CSV, and progress goes to standard error, so the output can be
piped straight into another program.

## Measure a burst

Using the burst from the [Guided Workflow](../guided-workflow.md), which
`flits demo` downloads to `~/.cache/flits/demo`:

```bash
flits measure ~/.cache/flits/demo/flits-tutorial-gbt-frb20240114a-v1.fil --dm 0
```

```json
[
  {
    "file": "/home/user/.cache/flits/demo/flits-tutorial-gbt-frb20240114a-v1.fil",
    "status": "ok",
    "error": null,
    "flits_version": "1.4.0",
    "dm": 0.0,
    "telescope": "gbt",
    "selection": {
      "event_source": "auto_localized",
      "crop_ms": [0.0, 423.4752],
      "event_ms": [234.88512, 249.91744],
      "offpulse_ms": [[0.0, 218.112], [266.69056, 423.4752]],
      "band_mhz": [1286.1328125, 1769.53125],
      "time_factor": 32,
      "freq_factor": 1,
      "mask_profile": "auto",
      "masked_channels": 24
    },
    "localization": {"status": "ok", "detection_snr": 84.24, "warning_flags": [], "...": "..."},
    "measurements": {
      "snr_integrated": 79.37,
      "fluence_jyms": 4.683,
      "width_ms_acf": 5.832,
      "measurement_flags": ["acf", "calibrated", "missing_distance"],
      "uncertainty_details": {"fluence_jyms": {"value": 0.0590, "classification": "statistical_only", "...": "..."}},
      "...": "..."
    },
    "snapshot": null,
    "provenance_warnings": []
  }
]
```

The output is shortened here. `measurements` holds every field of a FLITS
measurement except the diagnostic profiles and autocorrelations; add
`--diagnostics` to include those.

These numbers differ slightly from the guided workflow's because the selection
differs: automatic masking removed 24 interference channels, and the band was
chosen from the data instead of being set to 1300–1750 MHz.

## Pin the selection

Give the event window to skip automatic localization. Off-pulse windows then
default to everything outside the event, and the band to the full band:

```bash
flits measure burst.fil --dm 0 --mask none \
  --event 235 250 --offpulse 221 233 --offpulse 260 280 --band 1300 1750 \
  --time-factor 32 --sefd 10
```

This reproduces the guided workflow's reference values. When the burst is
localized automatically, `--offpulse` and `--band` still replace the values
localization chose. `--crop START END` restricts both the search and the
measurement to a time range.

Times are in milliseconds from the start of the file and frequencies are in MHz,
as in the interface. Other options:

| Option | Effect |
| --- | --- |
| `--telescope KEY` | Use a preset instead of detecting it (`generic`, `gbt`, `nrt`, `stockert`, `lofar`, `chime`). |
| `--sefd JY`, `--sefd-uncertainty FRACTION` | Calibrate with this SEFD; the fractional uncertainty is carried into flux and fluence uncertainties. |
| `--mask fast\|auto\|thorough\|none` | Choose the masking profile, or turn masking off. |
| `--detection-snr SNR` | Detection threshold for automatic localization (default 6). |
| `--widths` | Also run the width analysis. |

!!! warning "Check what automatic localization chose"
    Automatic localization starts from the strongest signal in the data. Without
    masking, interference can outshine the burst. On the tutorial burst with
    `--mask none`, it picks an 18 MHz sliver of interference at the bottom of
    the band (1128–1146 MHz), flags `band_touches_edge`, and reports `low_sn`.
    Keep masking on, and review bursts whose status is `low_sn` or whose
    `localization.warning_flags` are not empty.

## Measure many bursts

Pass several files. A burst that fails does not stop the rest:

```bash
flits measure data/*.fil --dm 527.7 --format csv --output results.csv
```

| `--format` | Output |
| --- | --- |
| `json` (default) | One JSON array, written when every burst is done. |
| `jsonl` | One JSON record per line, written as each burst finishes, so a long batch can be followed and survives an interruption. |
| `csv` | One row per burst with the main measurements, their uncertainties, and the status. Also written as each burst finishes. |

Each record has a `status`:

| Status | Meaning |
| --- | --- |
| `ok` | Measured. |
| `low_sn` | Measured, but flagged as low signal-to-noise. |
| `no_detection` | Automatic localization found nothing to measure; `measurements` is null. |
| `error` | The burst could not be processed; `error` says why. |

The command exits with 0 when every burst was measured (`ok` or `low_sn`), with
1 when at least one was `no_detection` or `error`, and with 2 for an invalid
command line. Output is strict JSON: missing or non-finite numbers are `null`.

For bursts at different DMs, run one command per DM or loop in the shell over a
`path,dm` list without a header row:

```bash
while IFS=, read -r path dm; do
  flits measure "$path" --dm "$dm" --format jsonl
done < bursts.csv > results.jsonl
```

## Review and reproduce

`--snapshot-dir DIR` saves each burst's session: the selection, the mask, the
results, and the software provenance. A saved session can be opened in the
interface and corrected by hand, which is useful for bursts with `no_detection`
or `low_sn`. It can also be [replayed](headless-replay.md) to check that the
numbers still reproduce:

```bash
flits measure data/*.fil --dm 527.7 --snapshot-dir data/snapshots
flits replay data/snapshots/burst_flits_session.json --check
flits --data-dir data
```

The interface lists sessions saved in a folder named `snapshots` under its data
directory, under **Session → Saved Sessions**. An existing snapshot is never
replaced unless you add `--overwrite`; a timestamped copy is saved next to it
instead.

## From Python

The command is a thin layer over `flits.measure_burst`:

```python
from flits import MeasureOptions, measure_burst

record = measure_burst(
    "burst.fil",
    MeasureOptions(dm=527.7, sefd_jy=10.0, sefd_fractional_uncertainty=0.1),
)
print(record["status"], record["measurements"]["fluence_jyms"])
```

`flits.measure_bursts` measures a list of files and yields one record per file.
For finer control, such as component regions, model fitting, drift rates, or
polarization, drive a `BurstSession` directly; see the
[Python API](../developer/api-reference.md).
