from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

import numpy as np

from flits.io.errors import (
    FormatDetectionError,
    UnsupportedFormatError,
)
from flits.models import FilterbankMetadata
from flits.settings import ObservationConfig


@dataclass(frozen=True)
class FilterbankInspection:
    source_path: Path
    source_name: str | None
    telescope_id: int | None
    machine_id: int | None
    detected_preset_key: str
    detection_basis: str
    telescope_name: str | None = None
    schema_version: str | None = None
    freq_lo_mhz: float | None = None
    freq_hi_mhz: float | None = None
    coherent_dm: float | None = None


@runtime_checkable
class BurstReader(Protocol):
    """A reader for one or more input formats.

    Implementations must be cheap to instantiate (zero-arg constructor) because
    they may be created once per process. All I/O happens inside `inspect()` /
    `load()` — no state is retained between calls.
    """

    format_id: ClassVar[str]
    format_id_aliases: ClassVar[tuple[str, ...]] = ()
    extensions: ClassVar[tuple[str, ...]]

    def sniff(self, path: Path) -> bool:
        """Return True if this reader can handle the given file.

        Must be fast (magic-byte / root-attr check only) and must not raise on
        unrelated files — return False instead.
        """
        ...

    def inspect(self, path: Path) -> FilterbankInspection:
        ...

    def load(
        self,
        path: Path,
        config: ObservationConfig,
        inspection: FilterbankInspection | None = None,
    ) -> tuple[np.ndarray, FilterbankMetadata]:
        ...


_BUILTIN_READERS: tuple[str, ...] = (
    "flits.io.your_reader:YourFilterbankReader",
    "flits.io.chime_hdf5_reader:ChimeHdf5Reader",
)

_extra_readers: list[type] = []
_cached_readers: list[BurstReader] | None = None
_load_diagnostics: list[dict[str, object]] = []
_TRUSTED_EXTENSION_SUFFIXES: frozenset[str] = frozenset({".fil"})


def register_reader(reader_cls: type) -> None:
    """Register a reader class for use by `detect_reader`.

    Primarily for tests; production readers should register via the
    `flits.readers` entry-point group in pyproject.toml.
    """
    global _cached_readers
    if reader_cls not in _extra_readers:
        _extra_readers.append(reader_cls)
    _cached_readers = None


def unregister_reader(reader_cls: type) -> None:
    global _cached_readers
    if reader_cls in _extra_readers:
        _extra_readers.remove(reader_cls)
    _cached_readers = None


def _load_target(target: str) -> type:
    module_name, _, class_name = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _record_diagnostic(source: str, target: str, error: Exception | None) -> None:
    _load_diagnostics.append(
        {
            "source": source,
            "target": target,
            "status": "error" if error else "ok",
            "error": f"{type(error).__name__}: {error}" if error else None,
        }
    )


def _load_all_readers() -> list[BurstReader]:
    global _cached_readers
    if _cached_readers is not None:
        return _cached_readers

    _load_diagnostics.clear()
    readers: list[BurstReader] = []
    seen_classes: set[type] = set()

    for target in _BUILTIN_READERS:
        try:
            cls = _load_target(target)
        except Exception as exc:
            _record_diagnostic("builtin", target, exc)
            continue
        if cls in seen_classes:
            _record_diagnostic("builtin", target, None)
            continue
        try:
            readers.append(cls())
            seen_classes.add(cls)
            _record_diagnostic("builtin", target, None)
        except Exception as exc:
            _record_diagnostic("builtin", target, exc)

    try:
        eps = importlib.metadata.entry_points(group="flits.readers")
    except Exception:
        eps = []
    for ep in eps:
        target = f"{ep.module}:{ep.attr}" if hasattr(ep, "attr") else ep.value
        try:
            cls = ep.load()
        except Exception as exc:
            _record_diagnostic("entry_point", target, exc)
            continue
        if cls in seen_classes:
            _record_diagnostic("entry_point", target, None)
            continue
        try:
            readers.append(cls())
            seen_classes.add(cls)
            _record_diagnostic("entry_point", target, None)
        except Exception as exc:
            _record_diagnostic("entry_point", target, exc)

    for cls in _extra_readers:
        if cls in seen_classes:
            continue
        try:
            readers.append(cls())
            seen_classes.add(cls)
            _record_diagnostic("register_reader", cls.__name__, None)
        except Exception as exc:
            _record_diagnostic("register_reader", cls.__name__, exc)

    _cached_readers = readers
    return readers


def list_readers() -> list[BurstReader]:
    return list(_load_all_readers())


def reader_diagnostics() -> list[dict[str, object]]:
    """Return a record of each reader load attempt, including failures.

    Useful for surfacing "why didn't my HDF5 file open?" — typically because
    h5py isn't installed.
    """
    _load_all_readers()
    return list(_load_diagnostics)


def _find_by_format_id(format_id: str) -> BurstReader | None:
    target = format_id.strip().lower()
    for reader in _load_all_readers():
        if reader.format_id.lower() == target:
            return reader
        for alias in getattr(reader, "format_id_aliases", ()):
            if alias.lower() == target:
                return reader
    return None


def _safe_sniff(reader: BurstReader, path: Path) -> bool:
    try:
        return bool(reader.sniff(path))
    except Exception:
        return False


def detect_reader(
    path: str | Path,
    *,
    format_hint: str | None = None,
) -> BurstReader:
    """Return the reader that should handle `path`.

    Detection cascade:
      1. If `format_hint` is given, pick the matching reader. Sniff only rejects
         when we have positive evidence (from another reader) that the hint is
         wrong.
      2. Only trust unique extensions for formats with distinctive magic bytes
         (`.fil`). Container formats (`.fits`, `.sf`, `.h5`, `.hdf5`) must
         still pass `sniff()`.
      3. Otherwise, prefer a positive `sniff()` from extension-matching readers.
      4. If no extension-matching reader claims the file, scan every reader's
         `sniff()` (catches misnamed files).
      5. Otherwise, raise UnsupportedFormatError.
    """
    resolved = Path(path).expanduser().resolve()

    if format_hint is not None:
        reader = _find_by_format_id(format_hint)
        if reader is None:
            raise UnsupportedFormatError(
                f"No reader registered with format_id={format_hint!r}",
                path=resolved,
            )
        if _any_other_reader_claims(reader, resolved):
            raise FormatDetectionError(
                f"Reader {format_hint!r} rejected the file at sniff time "
                f"(another registered reader matched instead)",
                path=resolved,
            )
        return reader

    suffix = resolved.suffix.lower()
    readers = _load_all_readers()
    ext_candidates = [r for r in readers if suffix in r.extensions]

    if len(ext_candidates) == 1:
        if suffix in _TRUSTED_EXTENSION_SUFFIXES:
            return ext_candidates[0]
        if _safe_sniff(ext_candidates[0], resolved):
            return ext_candidates[0]

    if len(ext_candidates) > 1:
        sniff_matches = [reader for reader in ext_candidates if _safe_sniff(reader, resolved)]
        if sniff_matches:
            return sniff_matches[0]

    for reader in readers:
        if _safe_sniff(reader, resolved):
            return reader

    raise UnsupportedFormatError(
        f"No registered reader recognized the file (suffix={suffix!r})",
        path=resolved,
    )


def _any_other_reader_claims(reader: BurstReader, path: Path) -> bool:
    """True iff some reader *other* than `reader` sniffs this file as its own.

    Used to guard an explicit format_hint: we only override the user when we
    have positive evidence the file belongs to a different format.
    """
    if _safe_sniff(reader, path):
        return False
    for other in _load_all_readers():
        if other is reader:
            continue
        if _safe_sniff(other, path):
            return True
    return False


def inspect_filterbank(path: str | Path) -> FilterbankInspection:
    resolved = Path(path).expanduser().resolve()
    reader = detect_reader(resolved)
    return reader.inspect(resolved)


def load_filterbank_data(
    path: str | Path,
    config: ObservationConfig,
    inspection: FilterbankInspection | None = None,
) -> tuple[np.ndarray, FilterbankMetadata]:
    resolved = Path(path).expanduser().resolve()
    reader = detect_reader(resolved)
    return reader.load(resolved, config, inspection=inspection)


__all__ = [
    "FilterbankInspection",
    "BurstReader",
    "detect_reader",
    "inspect_filterbank",
    "load_filterbank_data",
    "list_readers",
    "reader_diagnostics",
    "register_reader",
    "unregister_reader",
]
