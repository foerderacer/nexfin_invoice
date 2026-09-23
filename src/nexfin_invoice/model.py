"""Invoice data model and validation.

:class:`InvoiceData` is the single in-memory representation of an invoice,
shared by every extraction path (ZUGFeRD, text AI, vision AI). Its validators
enforce the rules from ``docs/invoice-format.md`` so that anything that
survives :class:`InvoiceData` construction can be rendered into a bookable
file.
"""

from __future__ import annotations

import re
import warnings
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

__all__ = [
    "InvoiceData",
    "LineItem",
    "iban_problem",
    "parse_decimal_number",
    "structured_output_schema",
]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{13,32}$")
_REQUIRED_TEXT_FIELDS = ("id", "vendor")
_OPTIONAL_TEXT_FIELDS = ("category", "account", "account_holder", "reference")

# Managed by nexfin itself; never part of the AI-facing schema.
_MANAGED_FIELDS = ("status", "booked", "paid_date")

# Converter provenance: written once by nexfin-invoice, never part of the
# AI-facing schema (the AI cannot know how the PDF was parsed).
_CONVERTER_FIELDS = ("parsed_by",)


def parse_decimal_number(raw: Any) -> Decimal:
    """Parse a number from AI/CII/UBL output into a :class:`~decimal.Decimal`.

    Accepts ``Decimal``, ``int``, ``float`` and strings, tolerating decimal
    commas (``"119,00"``) and stray thousands separators (``"1.234,56"`` /
    ``"1,234.56"``).
    """
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, bool) or raw is None:
        raise InvalidOperation(f"not a number: {raw!r}")
    elif isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, float):
        value = Decimal(str(raw))
    elif isinstance(raw, str):
        text = raw.strip().replace("\u00a0", "").replace(" ", "")
        if not text:
            raise InvalidOperation("empty number")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        value = Decimal(text)
    else:
        raise InvalidOperation(f"not a number: {raw!r}")
    if not value.is_finite():
        raise InvalidOperation(f"not a finite number: {raw!r}")
    return value


class LineItem(BaseModel):
    """One line of the optional ``## Line items`` body section."""

    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: Decimal | None = None
    amount: Decimal | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _clean_description(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    @field_validator("quantity", "amount", mode="before")
    @classmethod
    def _coerce_number(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return parse_decimal_number(value)


class InvoiceData(BaseModel):
    """Invoice front matter, exactly as documented in ``docs/invoice-format.md``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    vendor: str
    issued: date
    due: date
    amount: Decimal
    currency: str
    category: str | None = None
    account: str | None = None
    iban: str | None = None
    account_holder: str | None = None
    reference: str | None = None
    due_skonto: date | None = None
    amount_skonto: Decimal | None = None
    status: Literal["open", "paid"] = "open"
    booked: str = ""
    paid_date: str = ""
    parsed_by: Literal["zugferd", "ai-text", "ai-vision"] | None = None
    line_items: list[LineItem] = []

    # -- required string fields -------------------------------------------

    @field_validator("id", "vendor", mode="before")
    @classmethod
    def _require_text(cls, value: Any, info: Any) -> Any:
        if isinstance(value, str):
            value = " ".join(value.split())
        if not isinstance(value, str) or not value:
            raise ValueError(f"{info.field_name} is required and must be non-empty")
        return value

    # -- optional free-text fields ----------------------------------------

    @field_validator(*_OPTIONAL_TEXT_FIELDS, mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = " ".join(value.split())
            if not value:
                return None
        return value

    # -- dates ------------------------------------------------------------

    @field_validator("issued", "due", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> Any:
        return _parse_iso_date(value)

    @field_validator("due_skonto", mode="before")
    @classmethod
    def _parse_skonto_date(cls, value: Any) -> Any:
        # Optional: null/empty means "no skonto terms", never a parse error.
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return _parse_iso_date(value)

    # -- amount -----------------------------------------------------------

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, value: Any) -> Any:
        return parse_decimal_number(value)

    @field_validator("amount_skonto", mode="before")
    @classmethod
    def _coerce_skonto_amount(cls, value: Any) -> Any:
        # Optional: null/empty means "no skonto terms", never a parse error.
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return parse_decimal_number(value)

    @field_validator("amount", "amount_skonto")
    @classmethod
    def _limit_decimals(cls, value: Decimal | None, info: Any) -> Decimal | None:
        if value is None:
            return None
        exponent = value.as_tuple().exponent
        if isinstance(exponent, int) and -exponent > 2:
            raise ValueError(f"{info.field_name} must have at most 2 decimal places, got {value}")
        return value

    # -- currency ---------------------------------------------------------

    @field_validator("currency", mode="before")
    @classmethod
    def _upper_currency(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        if not _CURRENCY_RE.match(value):
            raise ValueError(f"currency must be an ISO 4217 code, got {value!r}")
        return value

    # -- IBAN -------------------------------------------------------------

    @field_validator("iban", mode="before")
    @classmethod
    def _normalize_iban(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.replace(" ", "").replace("-", "").strip().upper()
            if not value:
                return None
        return value

    @field_validator("iban")
    @classmethod
    def _check_iban(cls, value: str | None) -> str | None:
        # Invalid checksums show a warning in the nexfin pay dialog but never
        # block booking, so we mirror that: warn here, never fail.
        if value is not None:
            problem = iban_problem(value)
            if problem is not None:
                warnings.warn(problem, UserWarning, stacklevel=2)
        return value

    # -- skonto (cash discount) -------------------------------------------

    @model_validator(mode="after")
    def _check_skonto_pair(self) -> InvoiceData:
        # Atomic pair: the writer never emits one field without the other.
        # Hard error so the AI path self-heals via its validation-retry loop.
        if (self.due_skonto is None) != (self.amount_skonto is None):
            raise ValueError(
                "due_skonto and amount_skonto must be provided together or both be null"
            )
        if self.due_skonto is None or self.amount_skonto is None:
            return self
        # Sanity checks below are warn-only, mirroring the IBAN precedent:
        # a skonto problem must never block an otherwise bookable invoice.
        if self.due_skonto > self.due:
            warnings.warn(
                f"due_skonto {self.due_skonto} is after the payment due date {self.due}",
                UserWarning,
                stacklevel=2,
            )
        if (
            self.amount != 0
            and self.amount_skonto != 0
            and (self.amount < 0) != (self.amount_skonto < 0)
        ):
            warnings.warn(
                f"amount_skonto {self.amount_skonto} has a different sign than amount "
                f"{self.amount}",
                UserWarning,
                stacklevel=2,
            )
        if abs(self.amount_skonto) > abs(self.amount):
            warnings.warn(
                f"amount_skonto {self.amount_skonto} exceeds the total amount {self.amount}",
                UserWarning,
                stacklevel=2,
            )
        return self


def _parse_iso_date(value: Any) -> date:
    """Parse an ISO ``YYYY-MM-DD`` string (or date) into :class:`~datetime.date`."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"not a valid calendar date: {value!r}") from exc
    raise ValueError(f"date must be an ISO YYYY-MM-DD string, got {value!r}")


def _iban_mod97(iban: str) -> int:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(digits) % 97


def iban_problem(value: str) -> str | None:
    """Return the IBAN problem message for ``value``, or ``None`` when valid.

    Format regex plus the ISO 7064 mod-97 checksum, exactly the checks
    :class:`InvoiceData` warns about. The AI path uses this to decide
    whether the extracted IBAN needs a corrective redo.
    """
    if not _IBAN_RE.match(value):
        return f"iban {value!r} does not look like a valid IBAN"
    if _iban_mod97(value) != 1:
        return f"iban {value!r} fails the ISO 7064 mod-97 checksum"
    return None


def structured_output_schema() -> dict[str, Any]:
    """Build an OpenAI/OpenRouter ``json_schema`` payload for structured output.

    Derived from :class:`InvoiceData` with the nexfin-managed and converter
    fields removed, ``additionalProperties: false`` everywhere and every
    remaining property marked required, as required by strict structured
    outputs.
    """
    schema: dict[str, Any] = InvoiceData.model_json_schema()
    schema.pop("title", None)
    for field_name in (*_MANAGED_FIELDS, *_CONVERTER_FIELDS):
        schema.get("properties", {}).pop(field_name, None)
    for node in [schema, *schema.get("$defs", {}).values()]:
        if node.get("type") == "object":
            properties = node.get("properties", {})
            node["additionalProperties"] = False
            node["required"] = list(properties)
    return {
        "name": "invoice_data",
        "strict": True,
        "schema": schema,
    }
