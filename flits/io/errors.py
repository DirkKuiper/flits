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


__all__ = [
    "FlitsReaderError",
    "UnsupportedFormatError",
    "FormatDetectionError",
    "CorruptedDataError",
    "MetadataMissingError",
    "UnsupportedSchemaError",
]
