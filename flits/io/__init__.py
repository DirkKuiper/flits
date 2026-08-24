"""Input layer: format detection and burst loading.

Readers are discovered through ``flits.readers`` entry points, so a third party
can add a format without modifying FLITS. See ``flits.io.reader`` for the
protocol a reader must satisfy."""

from flits.io.reader import (
    BurstReader,
    FilterbankInspection,
    detect_reader,
    inspect_filterbank,
    list_readers,
    load_filterbank_data,
    reader_diagnostics,
    register_reader,
    unregister_reader,
)

__all__ = [
    "BurstReader",
    "FilterbankInspection",
    "detect_reader",
    "inspect_filterbank",
    "list_readers",
    "load_filterbank_data",
    "reader_diagnostics",
    "register_reader",
    "unregister_reader",
]
