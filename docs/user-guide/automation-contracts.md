# Batch manifests and output contracts

Use a manifest when each burst needs its own DM, calibration or selection.
Save this as `batch.json` next to a `data` directory:

```json
{
  "schema_version": "1.0",
  "defaults": {
    "mask": "auto",
    "sefd_jy": 10.0,
    "sefd_fractional_uncertainty": 0.1
  },
  "bursts": [
    {"id": "first", "file": "data/first.fil", "options": {"dm": 527.7}},
    {"id": "second", "file": "data/second.fil", "options": {"dm": 349.0, "sefd_jy": 15.0}}
  ]
}
```

```bash
flits measure --manifest batch.json --format jsonl --output results.jsonl --snapshot-dir snapshots
```

FLITS validates the whole manifest before measuring or opening the output file.
The output path cannot replace the manifest or an input burst file.
An invalid manifest exits 2, leaving an existing output file intact. A missing
or unreadable **burst file** produces an `error` record and later bursts still
run. As with `flits measure FILE...`, exit 0 means every record is `ok` or
`low_sn`; exit 1 means at least one is `error` or `no_detection`.

## Settings and paths

`defaults` and each burst's `options` use the field names of
[`MeasureOptions`](../developer/api-reference.md#headless-measurement):

| Fields | JSON representation |
| --- | --- |
| `dm` | Required finite number after merging defaults and options; pc cm⁻³. |
| `telescope` | Known preset key, or `null` to detect it from the file. |
| `sefd_jy`, `sefd_fractional_uncertainty` | Positive SEFD in Jy and non-negative fractional uncertainty; `null` uses the usual default. |
| `crop_ms`, `event_ms`, `band_mhz` | `[start, end]`, or `null` to use the ordinary automatic/default selection. |
| `offpulse_ms` | Array of `[start, end]` windows; `[]` uses the usual default. |
| `time_factor`, `freq_factor` | Positive integers, or `null` for the preset default. |
| `mask` | `"auto"`, `"fast"`, `"thorough"`, or `"none"`; default `"auto"`. |
| `detection_snr` | Positive finite number; default `6.0`. |
| `widths`, `include_diagnostics` | Booleans; default `false`. |

Options replace whole default values. For example, `offpulse_ms` replaces the
default list rather than appending to it. Times are milliseconds from the start
of the file's read window; frequencies are MHz. Ranges must be finite,
non-negative and increasing. JSON numbers and booleans are required: `"false"`
is a string and `true` is not a downsampling factor. Unknown fields and duplicate
JSON keys are rejected rather than silently ignored. These strict input rules
apply to the manifest; existing Python and command-line entry points retain
their input conventions.

Relative burst paths resolve from the **manifest's directory**, independently
of the shell's working directory and `--data-dir`. Absolute paths and `~` paths
are accepted. `--data-dir` still sets the root used when recording paths in
saved snapshots. Output and snapshot directories follow the shell's normal
path rules. A manifest selects files explicitly; it does not expand wildcards.

`--manifest` replaces `FILE...`, `--dm` and all other per-burst settings on the
command line. Keep those settings in the manifest. You can still pass
`--format`, `--output`, `--snapshot-dir`, `--overwrite`, `--data-dir` and
`--log-level`.

Each optional `id` must be a unique, non-empty string. It is copied into the
JSON, JSONL or CSV output, including error records, so a failed input can be
matched to its settings. Records retain manifest order. The same file may
appear more than once with different settings; snapshot names remain distinct.
Existing snapshots are preserved unless `--overwrite` is explicitly requested.

## Versioned JSON output

Each new measurement record includes `schema_version: "1.0"`, independently
of the software's `flits_version` and the saved-session schema. This applies to
the records from `measure_burst`, `measure_bursts` and `flits measure`, including
failed inputs. JSON output is an **array** of records; JSONL writes one record
per line. A schema describes one record, not the array wrapper.

Print the schemas offline from the installed package:

```bash
flits schema measurement > measurement-schema.json
flits schema batch-manifest > manifest-schema.json
```

Or read them in Python:

```python
from flits.schema import get_schema

schema = get_schema("measurement")
```

The canonical resources are
[`measurement-v1.json`](https://github.com/DirkKuiper/flits/blob/main/flits/schemas/measurement-v1.json)
and
[`batch-manifest-v1.json`](https://github.com/DirkKuiper/flits/blob/main/flits/schemas/batch-manifest-v1.json).
Both use JSON Schema 2020-12. Install a validator separately if needed; FLITS
does not add one to the analysis runtime:

```python
import json
from jsonschema import Draft202012Validator
from flits.schema import get_schema

validator = Draft202012Validator(get_schema("measurement"))
with open("results.json", encoding="utf-8") as handle:
    records = json.load(handle)
for record in records:
    validator.validate(record)
```

The measurement schema checks the record envelope, status-dependent presence
of measurements, selection and localization fields, primary numeric quantities,
and uncertainty classifications. Provenance and diagnostic objects allow
additional analysis-specific data. A structurally valid record still requires
scientific review: check `status`, `localization.warning_flags`,
`measurements.measurement_flags` and each relevant uncertainty's classification
and warning flags. `null` means unavailable, not zero. In particular,
`statistical_only` does not include a missing calibration systematic.

The manifest schema checks the input structure and types. FLITS additionally
checks merged DMs, identifier uniqueness, preset names, finiteness and range
ordering before analysis. JSON Schema alone does not perform these cross-field
and semantic checks.

Consumers should reject unsupported major versions and tolerate additional
fields within a supported version. Removing fields or changing their meaning
requires a new major output contract. Records produced before explicit
versioning lack `schema_version`; treat them as legacy records rather than
assuming they passed this schema. Their saved sessions remain loadable under
the separate session compatibility policy.

CSV is a flattened projection, not the nested JSON contract. Its existing
columns retain their order; `schema_version` and `id` are appended. `id` is
empty for inputs without an identifier. Nested flags, classified uncertainties
and full provenance are available in JSON/JSONL or saved sessions.
