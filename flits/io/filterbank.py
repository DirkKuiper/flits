"""Backwards-compatible shim for the pre-reader-protocol I/O surface.

Prefer [flits/io/reader.py](reader.py) and [flits/io/your_reader.py](your_reader.py)
in new code. This module exists so existing imports (`from flits.io.filterbank
import ...`) keep working.
"""
from __future__ import annotations

from flits.io.reader import (
    FilterbankInspection,
    inspect_filterbank,
    load_filterbank_data,
)
from flits.io.your_reader import YourFilterbankReader, your

__all__ = [
    "FilterbankInspection",
    "YourFilterbankReader",
    "inspect_filterbank",
    "load_filterbank_data",
    "your",
]
