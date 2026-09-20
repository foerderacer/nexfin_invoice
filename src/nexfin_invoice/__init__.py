"""nexfin-invoice: convert invoice PDFs into nexfin inbox Markdown files.

Extraction order: embedded ZUGFeRD/Factur-X/XRechnung XML (deterministic),
then PDF text + AI, then page images + AI (scanned PDFs). The output matches
``docs/invoice-format.md`` exactly.
"""

from .config import Config, load_config
from .errors import (
    AiError,
    ConfigError,
    ConversionError,
    MissingFieldError,
    NexfinInvoiceError,
    UnsupportedFormatError,
)
from .model import InvoiceData, LineItem
from .pipeline import ConversionResult, convert, convert_many
from .render import render, unique_output_path, write_markdown

__version__ = "0.1.0"

__all__ = [
    "AiError",
    "Config",
    "ConfigError",
    "ConversionError",
    "ConversionResult",
    "InvoiceData",
    "LineItem",
    "MissingFieldError",
    "NexfinInvoiceError",
    "UnsupportedFormatError",
    "__version__",
    "convert",
    "convert_many",
    "load_config",
    "render",
    "unique_output_path",
    "write_markdown",
]
