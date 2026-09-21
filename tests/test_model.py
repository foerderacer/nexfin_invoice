"""Tests for the InvoiceData model, validators and the strict JSON schema."""

from __future__ import annotations

import warnings
from decimal import Decimal

import pytest
from pydantic import ValidationError

from nexfin_invoice.model import (
    InvoiceData,
    LineItem,
    parse_decimal_number,
    structured_output_schema,
)


def base_data(**overrides: object) -> dict:
    data: dict = {
        "id": "RE-2026-0912",
        "vendor": "Muller GmbH",
        "issued": "2026-09-01",
        "due": "2026-09-15",
        "amount": "-119,00",
        "currency": "eur",
    }
    data.update(overrides)
    return data


def test_minimal_defaults() -> None:
    invoice = InvoiceData(**base_data())
    assert invoice.status == "open"
    assert invoice.booked == ""
    assert invoice.paid_date == ""
    assert invoice.line_items == []
    assert invoice.amount == Decimal("-119.00")
    assert invoice.currency == "EUR"
    assert invoice.iban is None


def test_whitespace_collapsed() -> None:
    invoice = InvoiceData(**base_data(id="  RE-2026-0912\n ", vendor="Muller\n  GmbH"))
    assert invoice.id == "RE-2026-0912"
    assert invoice.vendor == "Muller GmbH"


def test_required_text_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        InvoiceData(**base_data(id="   "))


def test_amount_accepts_comma_and_thousands() -> None:
    assert InvoiceData(**base_data(amount="1.234,56")).amount == Decimal("1234.56")
    assert InvoiceData(**base_data(amount="1,234.56")).amount == Decimal("1234.56")


def test_amount_rejects_three_decimals() -> None:
    with pytest.raises(ValidationError, match="2 decimal"):
        InvoiceData(**base_data(amount="119.005"))


def test_dates_strict_iso() -> None:
    invoice = InvoiceData(**base_data())
    assert str(invoice.issued) == "2026-09-01"
    with pytest.raises(ValidationError):
        InvoiceData(**base_data(issued="20260901"))
    with pytest.raises(ValidationError):
        InvoiceData(**base_data(due="2026-13-40"))


def test_currency_rejects_non_iso() -> None:
    with pytest.raises(ValidationError):
        InvoiceData(**base_data(currency="EURO"))


def test_iban_normalization() -> None:
    invoice = InvoiceData(**base_data(iban=" de89 3704-0044 0532 0130 00 "))
    assert invoice.iban == "DE89370400440532013000"


def test_iban_bad_checksum_warns_only() -> None:
    with pytest.warns(UserWarning, match="mod-97"):
        invoice = InvoiceData(**base_data(iban="DE89370400440532013001"))
    assert invoice.iban == "DE89370400440532013001"


def test_iban_bad_format_warns_only() -> None:
    with pytest.warns(UserWarning, match="valid IBAN"):
        InvoiceData(**base_data(iban="XX00"))


def test_empty_optional_strings_become_none() -> None:
    invoice = InvoiceData(**base_data(category="", reference=" "))
    assert invoice.category is None
    assert invoice.reference is None


# -- skonto pair ----------------------------------------------------------


def test_skonto_pair_parses() -> None:
    invoice = InvoiceData(
        **base_data(due_skonto="2026-09-10", amount_skonto="-110.00")
    )
    assert str(invoice.due_skonto) == "2026-09-10"
    assert invoice.amount_skonto == Decimal("-110.00")


def test_skonto_pair_defaults_absent() -> None:
    invoice = InvoiceData(**base_data())
    assert invoice.due_skonto is None
    assert invoice.amount_skonto is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"due_skonto": "2026-09-10"},
        {"amount_skonto": "-110.00"},
        {"due_skonto": "2026-09-10", "amount_skonto": None},
        {"due_skonto": None, "amount_skonto": "-110.00"},
    ],
)
def test_partial_skonto_pair_is_hard_error(overrides: dict) -> None:
    data = base_data()
    data.update(overrides)
    with pytest.raises(ValidationError, match="together"):
        InvoiceData(**data)


def test_skonto_zero_amount_counts_as_set() -> None:
    invoice = InvoiceData(**base_data(due_skonto="2026-09-10", amount_skonto=0))
    assert invoice.amount_skonto == Decimal("0.00")


def test_skonto_empty_strings_mean_absent() -> None:
    invoice = InvoiceData(**base_data(due_skonto="", amount_skonto=" "))
    assert invoice.due_skonto is None
    assert invoice.amount_skonto is None


def test_skonto_bad_date_rejected() -> None:
    with pytest.raises(ValidationError, match="ISO YYYY-MM-DD"):
        InvoiceData(
            **base_data(due_skonto="2026-09-7", amount_skonto="-110.00")
        )


def test_skonto_rejects_three_decimals() -> None:
    with pytest.raises(ValidationError, match="amount_skonto"):
        InvoiceData(
            **base_data(due_skonto="2026-09-10", amount_skonto="-110.005")
        )


def test_skonto_after_due_warns_only() -> None:
    with pytest.warns(UserWarning, match="after the payment due date"):
        invoice = InvoiceData(
            **base_data(due_skonto="2026-09-16", amount_skonto="-110.00")
        )
    assert invoice.due_skonto is not None  # kept, never blocks booking


def test_skonto_sign_mismatch_warns_only() -> None:
    with pytest.warns(UserWarning, match="different sign"):
        InvoiceData(
            **base_data(due_skonto="2026-09-10", amount_skonto="110.00")
        )


def test_skonto_oversize_warns_only() -> None:
    with pytest.warns(UserWarning, match="exceeds the total amount"):
        invoice = InvoiceData(
            **base_data(due_skonto="2026-09-10", amount_skonto="-200.00")
        )
    assert invoice.amount_skonto == Decimal("-200.00")


def test_skonto_equal_dates_and_matching_sign_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        InvoiceData(
            **base_data(due_skonto="2026-09-15", amount_skonto="-119.00")
        )


def test_line_items_coerce_numbers() -> None:
    invoice = InvoiceData(
        **base_data(
            line_items=[
                {"description": "Widget", "quantity": "2", "amount": "45,00"},
                {"description": "Shipping"},
            ]
        )
    )
    assert invoice.line_items[0].quantity == Decimal("2")
    assert invoice.line_items[0].amount == Decimal("45.00")
    assert invoice.line_items[1].quantity is None
    assert invoice.line_items[1].amount is None


def test_line_item_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LineItem(description="x", bogus=1)


def test_parse_decimal_number() -> None:
    assert parse_decimal_number(" 119,00 ") == Decimal("119.00")
    assert parse_decimal_number(119) == Decimal("119")
    assert parse_decimal_number(-119.0) == Decimal("-119")
    with pytest.raises(Exception):  # noqa: B017 — InvalidOperation
        parse_decimal_number("abc")


def test_structured_output_schema_is_strict() -> None:
    schema = structured_output_schema()
    assert schema["name"] == "invoice_data"
    assert schema["strict"] is True
    inner = schema["schema"]
    assert inner["additionalProperties"] is False
    properties = inner["properties"]
    for managed in ("status", "booked", "paid_date"):
        assert managed not in properties
    assert "due_skonto" in properties
    assert "amount_skonto" in properties
    assert inner["required"] == list(properties)
    line_item = inner["$defs"]["LineItem"]
    assert line_item["additionalProperties"] is False
    assert line_item["required"] == list(line_item["properties"])
