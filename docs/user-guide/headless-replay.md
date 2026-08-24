# Headless Replay

`flits replay` re-runs a saved session snapshot without a browser. It reopens
the burst the snapshot names, restores every selection it records — DM, crop,
event window, off-pulse regions, channel mask, spectral extent, calibration
inputs — recomputes the measurements, and can write the same export bundle the
interface produces.

This is what turns the snapshot from a record of an analysis into a reproducible
one. Anyone with the snapshot and the data file can obtain the numbers again,
and check that they still come out the same.

## Reproduce an analysis

```bash
flits replay gbt_burst_flits_session.json
```

```text
snapshot   gbt_burst_flits_session.json
burst      /data/GBT-L/blc_s_guppi_60385_53711_FRB20240114A.fil
dm         527.650000
recomputed width_analysis, results
width_ms   1.8342
fluence_jyms 0.7421
snr        18.63
```

If the burst has moved since the snapshot was saved, point FLITS at its new
home:

```bash
flits replay session.json --data-dir /new/location
```

Snapshot resolution prefers a copy inside the data directory whose size and
SHA-256 match the one recorded at save time, so moving or renaming the file is
usually handled without any flag at all. The same data-directory containment
that applies to the server applies here.

## Verify that the numbers still reproduce

`--check` recomputes the measurements and compares them against the values
stored in the snapshot, field by field. It exits non-zero if anything differs,
which makes it usable in CI or in a pre-submission check over a burst catalogue.

```bash
flits replay session.json --check
```

```text
check      OK - recomputed measurements match the snapshot
```

Use `--tolerance` to set the relative tolerance (default `1e-9`). A difference
means either the data changed, the environment changed, or a FLITS version
changed a result — all three are worth knowing about before a paper goes out.

```bash
# Check a whole directory of snapshots and report the first failure.
for snapshot in sessions/*_flits_session.json; do
  flits replay "$snapshot" --check --log-level warning || echo "DIFFERS: $snapshot"
done
```

## Write an export bundle

```bash
flits replay session.json --export out/
```

Every ready artifact is written into `out/`, alongside a `manifest.json`
describing the bundle. Restrict what is produced with `--include`, which accepts
the same artifact kinds as the interface (`json`, `csv`, `npz`, `plots`,
`window`) and can be repeated:

```bash
flits replay session.json --export out/ --include json --include csv
```

## Machine-readable output

`--json` prints the whole replay report — burst path, DM, what was recomputed,
the measurements, the files written, and any differences found by `--check` — as
a single JSON document:

```bash
flits replay session.json --check --json > replay-report.json
```

That makes it straightforward to build a table across many bursts:

```bash
for snapshot in sessions/*.json; do
  flits replay "$snapshot" --json --log-level error
done | jq -s '[.[] | {burst: .burst_file, dm: .dm, fluence: .measurements.fluence_jyms}]'
```

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | Replay succeeded; with `--check`, the measurements matched |
| 1 | `--check` found differences |
| 2 | The snapshot could not be read, or the burst could not be reopened |

## What replay does not do

Replay restores selections and recomputes measurements and width analysis. It
does not re-run the DM sweep, the temporal or spectral structure analyses, or
model fitting — those are expensive and parameterized, and the snapshot keeps
their stored results. Run them in the interface when you need them refreshed.
