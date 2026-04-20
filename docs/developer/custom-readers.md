# Writing a Custom Reader

FLITS discovers readers through the `flits.readers` entry-point group. A
third-party package can add support for a new format without patching FLITS —
`pip install your-plugin` is enough for `detect_reader` to pick it up.

## Reader interface

A reader is a zero-argument class implementing the `BurstReader` protocol
defined in `flits/io/reader.py`:

```python
from pathlib import Path
from typing import ClassVar
import numpy as np

from flits.io.reader import FilterbankInspection
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig


class MyFormatReader:
    format_id: ClassVar[str] = "myformat"
    format_id_aliases: ClassVar[tuple[str, ...]] = ()
    extensions: ClassVar[tuple[str, ...]] = (".myfmt",)

    def sniff(self, path: Path) -> bool:
        """Fast magic-byte check. Must not raise on unrelated files."""
        try:
            with open(path, "rb") as fh:
                return fh.read(8) == b"MYFMT\x00\x00\x00"
        except OSError:
            return False

    def inspect(self, path: Path) -> FilterbankInspection:
        """Cheap metadata probe — no bulk data reads."""
        ...

    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        """Return (stokes_i, metadata). stokes_i shape is (nchan, ntime), float32."""
        ...
```

Constructors must take no arguments — readers are instantiated via
`cls()`. Any dependencies your reader needs should be imported lazily (inside
`load` / `inspect`) or guarded at import time so a missing optional dep
doesn't break the whole framework.

## Returning valid metadata

The `FilterbankMetadata` you emit is run through `flits.io.validation.validate_metadata`:

- `tsamp`, `freqres`, `bandwidth_mhz` must all be positive and finite.
- `start_mjd` must fall in a plausible range.
- `freqs_mhz` must be 1-D and strictly monotonic (ascending or descending —
  both are accepted; FLITS is frequency-order-agnostic).
- `npol`, `header_npol` must be at least 1.

Raise `flits.io.errors.CorruptedDataError` with a helpful message if your
file cannot satisfy these invariants — the framework will not silently paper
over bad inputs.

## Registering via entry points

In your package's `pyproject.toml`:

```toml
[project.entry-points."flits.readers"]
myformat = "my_package.readers:MyFormatReader"
```

Once the package is installed in the same environment as FLITS, the reader is
discovered automatically. No FLITS fork, no monkey-patching.

## Registering programmatically (tests only)

For unit tests, register without entry-point plumbing:

```python
from flits.io import register_reader, unregister_reader

register_reader(MyFormatReader)
try:
    ...
finally:
    unregister_reader(MyFormatReader)
```

## Debugging discovery

If your reader is not being picked up, call:

```python
from flits.io import reader_diagnostics
for entry in reader_diagnostics():
    print(entry)
```

This returns the status of every reader the registry tried to load, including
exceptions — usually a missing optional dep or a typo in the entry-point
string.
