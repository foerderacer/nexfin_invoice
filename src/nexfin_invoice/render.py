"""Deterministic front-matter writer for the nexfin inbox format.

The output must match ``docs/invoice-format.md`` byte-for-byte in shape:
fixed key order, amounts as raw two-decimal scalars (``amount: -119.00``),
quoted empty strings (``booked: ""``) and minimal quoting otherwise. This is
deliberately hand-rolled — PyYAML would emit ``-119.0``, date-typed scalars
and unnecessary quotes, none of which nexfin documents.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from .model import InvoiceData, LineItem

__all__ = ["output_filename", "render", "unique_output_path", "write_markdown"]

_FIELD_ORDER = (
    "id",
    "vendor",
    "issued",
    "due",
    "amount",
    "currency",
    "category",
    "account",
    "iban",
    "account_holder",
    "reference",
    "status",
    "booked",
    "paid_date",
)

# Optional fields: omitted entirely when empty/None.
_OPTIONAL_FIELDS = frozenset({"category", "account", "iban", "account_holder", "reference"})

# YAML leading indicators that force quoting, plus "looks like a number" and
# "looks like a bool/null" values which YAML would otherwise misread.
_YAML_SPECIAL_LEADS = "-?:,[]{}#&*!|>'\"%@`"
_YAML_RESERVED_VALUES = frozenset(
    {"true", "false", "yes", "no", "on", "off", "null", "none", "~"}
)
_NUMERIC_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")

_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def render(invoice: InvoiceData) -> str:
    """Render :class:`InvoiceData` into the full markdown file content."""
    fields: dict[str, str] = {
        "id": invoice.id,
        "vendor": invoice.vendor,
        "issued": _date_text(invoice.issued),
        "due": _date_text(invoice.due),
        "amount": f"{invoice.amount:.2f}",
        "currency": invoice.currency,
        "category": invoice.category or "",
        "account": invoice.account or "",
        "iban": invoice.iban or "",
        "account_holder": invoice.account_holder or "",
        "reference": invoice.reference or "",
        "status": invoice.status,
        "booked": invoice.booked,
        "paid_date": invoice.paid_date,
    }

    lines = ["---"]
    for key in _FIELD_ORDER:
        value = fields[key]
        if key in _OPTIONAL_FIELDS and not value:
            continue
        if key == "amount":
            lines.append(f"amount: {value}")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")

    parts = ["\n".join(lines), _body(invoice)]
    return "\n\n".join(parts).rstrip("\n") + "\n"


def _date_text(value: date) -> str:
    return value.isoformat()


def _scalar(value: str) -> str:
    """Quote a scalar only when plain style would change its YAML meaning."""
    if value == "":
        return '""'
    needs_quotes = (
        value != value.strip()
        or value[0] in _YAML_SPECIAL_LEADS
        or value.endswith(":")
        or ": " in value
        or " #" in value
        or value.lower() in _YAML_RESERVED_VALUES
        or _NUMERIC_RE.match(value) is not None
    )
    if needs_quotes:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _body(invoice: InvoiceData) -> str:
    lines = [f"# {invoice.id} — {invoice.vendor}"]
    if invoice.line_items:
        lines.append("")
        lines.append("## Line items")
        lines.append("")
        for item in invoice.line_items:
            lines.append(f"- {_line_item(item)}")
    return "\n".join(lines)


def _line_item(item: LineItem) -> str:
    prefix = f"{_quantity_text(item.quantity)}x " if item.quantity is not None else ""
    if item.amount is None:
        return f"{prefix}{item.description}"
    return f"{prefix}{item.description} ... {item.amount:.2f}"


def _quantity_text(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{value:.0f}"
    return format(value, "f")


def output_filename(invoice_id: str) -> str:
    """Filesystem-safe ``<id>.md`` stem for an invoice id."""
    stem = _INVALID_FILENAME_RE.sub("_", invoice_id).strip(" .")
    if not stem:
        stem = "invoice"
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return stem[:150]


def unique_output_path(directory: Path, invoice_id: str) -> Path:
    """Pick ``<id>.md`` in ``directory``, suffixing ``-1``, ``-2``, ... on collision.

    Same convention as the nexfin webhook intake: the suffix goes before the
    ``.md`` extension.
    """
    stem = output_filename(invoice_id)
    candidate = directory / f"{stem}.md"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = directory / f"{stem}-{counter}.md"
    return candidate


def write_markdown(path: Path, markdown: str) -> None:
    """Write markdown with LF newlines and UTF-8, regardless of platform."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
