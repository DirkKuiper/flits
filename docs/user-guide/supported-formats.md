# Supported Input Formats

FLITS reads filterbank-style data through a pluggable reader framework. The
three built-in readers cover the formats most common in the FRB community:

| Format           | Extensions       | Backend                              | Notes                                |
|------------------|------------------|--------------------------------------|--------------------------------------|
| SIGPROC          | `.fil`           | `your` (filterbank)                  | Canonical input; fully supported.    |
| PSRFITS (search) | `.fits`, `.sf`   | `your` + `astropy.io.fits` fallback  | Search-mode only; fold-mode rejected.|
| CHIME/FRB HDF5   | `.h5`, `.hdf5`   | `h5py` (native)                      | `flits_chime_v1`, public catalog, and beamformed `BBData` `tiedbeam_power`. |

No converter is needed in advance. Just point FLITS at the file: the reader
framework detects the format by extension, falling back to magic-byte sniffing
for misnamed files.

## Format detection

Detection cascade (first match wins):

1. **Trusted extension match.** Unique `.fil` files are accepted directly.
2. **Magic-byte / layout sniff.** Container formats such as `.fits`, `.sf`,
   `.h5`, and `.hdf5` must still pass a reader-specific sniff/layout check.
3. **Unknown suffix fallback.** If the suffix is unknown, each reader's
   `sniff()` runs against the file's first bytes or cheap container metadata.
4. **Explicit override.** Pass `format_hint="sigproc"`, `"psrfits"`, or
   `"chime_hdf5"` to `detect_reader` / `load_filterbank_data` to force a
   choice. The override is only rejected if FLITS has positive evidence
   (another reader sniffs the same file as its own) that the hint is wrong.

## Telescope preset detection

Once a reader owns the file, FLITS picks a telescope preset (SEFD defaults,
auto-mask profile, display label) using a **priority cascade** over whatever
metadata the file exposes:

1. **SIGPROC `telescope_id`** — canonical integer tag; strongest signal.
2. **Format / schema signature** — e.g. `chime_frb_catalog_v1` or
   `flits_chime_v1` directly implies CHIME/FRB.
3. **Telescope-name alias** — strings like `TELESCOP = "MeerKAT"` or
   `telescope_name = "CHIME"` (whitespace, hyphens, and case are ignored).
4. **SIGPROC `machine_id`** — weak but disambiguating for some setups.
5. **Generic fallback** — no preset claimed the file.

Each layer is only consulted if no stronger one matched, and ambiguity at any
layer (more than one preset matches) falls through rather than guessing. The
UI surfaces which signal was used via the "Detected: …" label and basis.

## PSRFITS caveats

- Only **search-mode** PSRFITS is supported. Fold-mode files raise
  `FormatDetectionError` — FLITS is a burst-analysis tool, not a timing tool.
- Some non-canonical files (MeerKAT, Parkes variants) leave `TSTART`,
  `TELESCOP`, or `SRC_NAME` unset at the layer `your` reads. FLITS
  automatically patches these in from the FITS primary header via
  `astropy.io.fits`. If a required field is genuinely absent, a
  `MetadataMissingError` names the missing fields so you can fix the file.

## CHIME/FRB HDF5 schema (`flits_chime_v1`)

FLITS-written test fixtures and new HDF5 writers should target this layout:

**Root attributes**

| Attribute        | Type     | Required | Description                               |
|------------------|----------|----------|-------------------------------------------|
| `schema_version` | `str`    | no       | `"flits_chime_v1"` (or recognized alias). |
| `tsamp_s`        | `float`  | yes      | Sample time in seconds.                   |
| `fch1_mhz`       | `float`  | yes      | Frequency of channel 0 in MHz.            |
| `foff_mhz`       | `float`  | yes      | Channel bandwidth (signed).               |
| `tstart_mjd`     | `float`  | yes      | Start MJD.                                |
| `nchan`          | `int`    | no       | Declared channel count (cross-checked).   |
| `npol`           | `int`    | no       | Polarization count (defaults to 1).       |
| `source_name`    | `str`    | no       | Source or FRB name.                       |
| `telescope_id`   | `int`    | no       | SIGPROC-style telescope ID.               |
| `telescope_name` | `str`    | no       | Free-form telescope name.                 |
| `sefd_jy`        | `float`  | no       | System-equivalent flux density, for calibration. |

**Waterfall dataset**

Required at `/wfall` (or the alternates `/frb/wfall`, `/intensity`, `/data`)
with shape `(nchan, ntime)` and float dtype. Chunked storage is preferred —
FLITS reads only the requested time window via HDF5 native slicing, so
multi-GB files do not require loading the full array.

Alternate attribute names (`dt`, `t_sample`, `fch1`, `foff`, `mjd_start`,
`tstart`, `TELESCOP`, `SRC_NAME`, `src_name`) are also recognized, and
attributes on a `/frb` subgroup fall back transparently to the root when
looked up. This keeps the reader compatible with public CHIME releases that
nest metadata differently.

**Unsupported schemas** raise `UnsupportedSchemaError`. To add a new schema,
register a custom reader via an entry point (see
[Custom readers](../developer/custom-readers.md)).

## CHIME/FRB public catalog HDF5

- Public catalog files under `/frb` with `plot_freq`, `extent`, and
  `calibrated_wfall` / `wfall` are supported directly.
- These waterfalls are treated as already dedispersed for FLITS loading.
  In the UI, the detection step suggests `DM = 0`.

## CHIME beamformed `BBData`

FLITS also supports CHIME beamformed `BBData` containers with:

- root attr `__memh5_subclass = "baseband_analysis.core.bbdata.BBData"`
- root attr `delta_time`
- dataset `tiedbeam_power`
- dataset `index_map/freq`
- dataset `time0`

This iteration supports only the beamformed power product
`tiedbeam_power`. Complex-voltage `tiedbeam_baseband` analysis is not yet part
of FLITS.

The reader:

- forms Stokes I from the polarization axis,
- applies the fixed per-channel time alignment encoded in `time0`,
- uses `tiedbeam_power.attrs["DM_coherent"]` as the coherent-dedispersion
  reference,
- then applies **residual** incoherent dedispersion relative to the DM you ask
  FLITS to use.

In practice, this means:

- If you want the file loaded at its native coherent-dedispersion DM, use
  `DM = DM_coherent`.
- The UI auto-suggests `DM = DM_coherent` for these files.

## Programmatic use

```python
from flits.io import detect_reader, load_filterbank_data, reader_diagnostics
from flits.settings import ObservationConfig

reader = detect_reader("burst.h5")
config = ObservationConfig.from_preset(dm=500.0, preset_key="generic")
data, metadata = load_filterbank_data("burst.h5", config)

# If detection fails or a reader won't load (e.g. missing optional dep),
# reader_diagnostics() explains per reader.
for entry in reader_diagnostics():
    print(entry)
```

## Error taxonomy

All I/O errors derive from `flits.io.errors.FlitsReaderError`:

- `UnsupportedFormatError` — no reader recognized the file.
- `FormatDetectionError` — `format_hint` conflicts with sniff results.
- `CorruptedDataError` — file is recognized but contents are inconsistent.
- `MetadataMissingError` — required header fields absent; `.fields` lists them.
- `UnsupportedSchemaError` — HDF5 schema version is not known.
