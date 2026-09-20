"""Error types for nexfin-invoice.

All errors raised deliberately by this package derive from
:class:`NexfinInvoiceError`, so library callers can catch a single base class.
"""

from __future__ import annotations

__all__ = [
    "AiError",
    "ConfigError",
    "ConversionError",
    "MissingFieldError",
    "NexfinInvoiceError",
    "UnsupportedFormatError",
]


class NexfinInvoiceError(Exception):
    """Base class for all nexfin-invoice errors."""


class ConfigError(NexfinInvoiceError):
    """The configuration (CLI flag, environment, TOML file) is invalid."""


class ConversionError(NexfinInvoiceError):
    """A PDF could not be converted into a bookable invoice file."""


class MissingFieldError(ConversionError):
    """Structured invoice data is missing a field required by the format.

    The generated file would land under "needs attention" and never be
    bookable, so we refuse to write it instead.
    """


class UnsupportedFormatError(ConversionError):
    """The PDF carries no invoice data this extractor understands."""


class AiError(ConversionError):
    """The AI extraction path failed (API error or unusable model output)."""
