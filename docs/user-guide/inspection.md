# Inspect files and identify the runtime

Before measuring an unfamiliar input, read its metadata:

```bash
flits inspect burst.fil catalog.h5 beamformed.h5
```

This prints a JSON array with one record per file. It uses the same reader
registry as the interface, including installed reader plugins, and calls each
reader's metadata inspection method. It does not load a waterfall, create a
session, localize a burst, fit a model, or write a snapshot. Readers may read
headers and metadata arrays to identify a file.

Each successful record contains `schema_version` (`"1.0"`), an absolute `file`
path, `status: "ok"`, `format_id`, `metadata`, `stokes_available`, `suggested_dm`
and `dm_guidance`. The metadata includes the detected preset and its detection
basis, frequency bounds when available, coherent DM, sky coordinates and
polarization basis when the file establishes them. Missing metadata is `null`;
it is not inferred from a filename. `metadata.schema_version` describes the
**input format**, separately from the inspection record's version.

`suggested_dm` is a loading convention, not a fitted or estimated DM:

| Input | Suggested loading DM |
| --- | --- |
| CHIME/FRB catalog waterfall | `0.0`, because the waterfall is already dedispersed. |
| CHIME BBData beamformed power with coherent DM metadata | The coherent DM, because FLITS applies residual dispersion relative to that value. |
| Other inputs | `null`; supply a known DM before measuring. |

An unreadable, missing or unsupported file returns `status: "error"`, `error`
and `error_type`; later inputs are still inspected. Exit code 0 means every
file was inspected, 1 means at least one failed, and 2 means invalid command
arguments. The Python equivalent, `flits.inspect_burst(path)`, returns one
successful record or raises an exception for that input.

## Check which installation is running

```bash
flits info
flits info --server http://127.0.0.1:8000
```

`info` prints JSON with the FLITS version, Python version, fitter identifier,
capabilities, a process instance identifier, and a SHA-256 digest of the package's
Python source files. The local report also includes the Python executable and
package path. In a source checkout, the source identity includes a Git commit
and whether the package has local changes. Paths are omitted from the server's
`/api/health` response. The browser shows its server's version and fitter under
the FLITS title.

With `--server`, `same_flits_code` is `true` only when the local interpreter
and server report the same FLITS version, fitter and source digest. A difference
returns `false`. An older server that reports only `status: "ok"` cannot be
identified, so the value is `null`. Connection and JSON errors are reported in
`error`. Exit code 0 means a verified match; any mismatch, unknown identity or
connection failure exits 1. Invalid command arguments exit 2.

The identity is captured once per process. Editing a checkout or upgrading a
package does not update the code already loaded by a running server. Restart
the server from the intended environment after an upgrade. Its new process
has a new instance identifier. Saved fits keep the provenance of the code that
produced them; opening a saved fit does not recompute it with the current fitter.

If the command on your `PATH` is old, invoke the command with the Python
interpreter you intend to use:

```bash
python -m flits info
python -m flits info --server http://127.0.0.1:8000
python -m flits serve --data-dir data --port 8001
```

Check the reported `package_path` too: running Python from an older source
checkout can import that checkout even after another environment was upgraded.
Neither command replaces a running server automatically. A matching FLITS source
digest verifies the FLITS Python code; it does not establish that third-party
dependency versions, input files or scientific selections match. Those belong
to each analysis product's [provenance](exports-and-snapshots.md).

In Python, `flits.runtime_info()` returns the public identity;
`flits.runtime_info(include_paths=True)` adds the local paths.
