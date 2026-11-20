"""Exception types raised by the input layer."""

from __future__ import annotations

from pathlib import Path


class FlitsReaderError(Exception):
    """Base class for FLITS I/O errors.

    Carries the offending path on every raise so callers can surface it in UI/logs
    without re-threading it through the exception chain.
    """

    def __init__(self, message: str, *, path: Path | str | None = None) -> None:
        super().__init__(message)
        self.path = Path(path) if path is not None else None


class UnsupportedFormatError(FlitsReaderError):
    """No registered reader matched the file (neither by extension nor by sniff)."""


class FormatDetectionError(FlitsReaderError):
    """Detection was ambiguous, or an explicit format_hint did not match the file."""


class CorruptedDataError(FlitsReaderError):
    """The file was recognized as a supported format but its contents are inconsistent.

    Examples: tsamp <= 0, nchans mismatches data shape, truncated data block.
    """


class MetadataMissingError(FlitsReaderError):
    """A required header field is absent and could not be recovered.

    The `fields` attribute lists the missing field names so callers can report precisely.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | str | None = None,
        fields: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message, path=path)
        self.fields = tuple(fields)


class PolarizationUnavailableError(FlitsReaderError):
    """The file cannot yield a Stokes cube.

    Raised when a reader has no full-Stokes path at all, when the file holds
    fewer than four polarization products, or when nothing -- header, preset, or
    operator override -- establishes which four products those are. The `reason`
    attribute distinguishes the cases so callers can tell "this file is Stokes I"
    from "tell FLITS which basis this file uses".
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | str | None = None,
        reason: str = "unsupported",
    ) -> None:
        super().__init__(message, path=path)
        self.reason = reason


class UnsupportedSchemaError(FlitsReaderError):
    """The file's container format is supported but its schema/version is not.

    Used by HDF5 readers that dispatch on schema-version attributes.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | str | None = None,
        detected_schema: str | None = None,
    ) -> None:
        super().__init__(message, path=path)
        self.detected_schema = detected_schema


class LoadTooLargeError(FlitsReaderError):
    """Loading the requested read window would take more memory than the load budget allows.

    Raised before any sample is read. `estimated_load_bytes` is FLITS's
    estimate of the peak memory the load needs and `budget_bytes` the budget it
    exceeds; `max_window_sec` is the longest read window that would fit, when it
    can be worked out.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | str | None = None,
        estimated_load_bytes: int,
        budget_bytes: int,
        max_window_sec: float | None = None,
    ) -> None:
        super().__init__(message, path=path)
        self.estimated_load_bytes = int(estimated_load_bytes)
        self.budget_bytes = int(budget_bytes)
        self.max_window_sec = max_window_sec


__all__ = [
    "CorruptedDataError",
    "FlitsReaderError",
    "FormatDetectionError",
    "LoadTooLargeError",
    "MetadataMissingError",
    "PolarizationUnavailableError",
    "UnsupportedFormatError",
    "UnsupportedSchemaError",
]
