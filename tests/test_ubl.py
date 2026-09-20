"""Tests for XRechnung (UBL) parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import (
    UBL_CREDIT_NOTE_XML,
    UBL_FALLBACK_AMOUNT_XML,
    UBL_INVOICE_XML,
    UBL_MISSING_DUE_XML,
)
from nexfin_invoice.errors import MissingFieldError
from nexfin_invoice.extract.zugferd import parse_invoice_xml


def test_ubl_invoice_fields() -> None:
    extracted = parse_invoice_xml(UBL_INVOICE_XML)
    data = extracted.data
    assert data.id == "XR-2026-042"
    assert data.vendor == "Franz Handel GmbH"
    assert str(data.issued) == "2026-09-05"
    assert str(data.due) == "2026-09-30"
    assert data.amount == Decimal("238.00")
    assert data.currency == "EUR"
    assert data.iban == "DE02120300000000202051"
    assert data.reference == "XR-2026-042 Zahlungsziel 30 Tage"
    assert data.account_holder == "Franz Handel GmbH"
    assert extracted.is_credit_note is False


def test_ubl_credit_note_root() -> None:
    extracted = parse_invoice_xml(UBL_CREDIT_NOTE_XML)
    assert extracted.is_credit_note is True
    assert extracted.data.id == "CN-2026-007"
    assert extracted.data.amount == Decimal("50.00")


def test_ubl_missing_due_errors() -> None:
    with pytest.raises(MissingFieldError, match="--ai"):
        parse_invoice_xml(UBL_MISSING_DUE_XML)


def test_ubl_amount_fallback_to_tax_inclusive() -> None:
    extracted = parse_invoice_xml(UBL_FALLBACK_AMOUNT_XML)
    assert extracted.data.amount == Decimal("238.00")
