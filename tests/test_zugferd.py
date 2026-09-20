"""Tests for ZUGFeRD/Factur-X detection and CII parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import (
    CII_CREDIT_NOTE_XML,
    CII_GRAND_TOTAL_ONLY_XML,
    CII_INVOICE_CURRENCY_VARIANT_XML,
    CII_INVOICE_XML,
    CII_MISSING_DUE_XML,
    ZUGFERD1_XML,
    make_zugferd_pdf,
)
from nexfin_invoice.errors import ConversionError, MissingFieldError, UnsupportedFormatError
from nexfin_invoice.extract.zugferd import find_invoice_xml, parse_invoice_xml


def test_finds_and_parses_cii_invoice(tmp_path) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    found = find_invoice_xml(pdf)
    assert found is not None
    assert found[0] == "factur-x.xml"

    extracted = parse_invoice_xml(found[1])
    data = extracted.data
    assert data.id == "RE-2026-0912"
    assert data.vendor == "Muller GmbH"
    assert str(data.issued) == "2026-09-01"
    assert str(data.due) == "2026-09-15"
    assert data.amount == Decimal("119.00")  # absolute, comma decimal parsed
    assert data.currency == "EUR"
    assert data.iban == "DE89370400440532013000"  # spaces/hyphens stripped
    assert data.account_holder == "Muller GmbH"
    assert data.reference == "RE-2026-0912"
    assert extracted.is_credit_note is False


def test_credit_note_381_flagged(tmp_path) -> None:
    extracted = parse_invoice_xml(CII_CREDIT_NOTE_XML)
    assert extracted.is_credit_note is True
    assert extracted.data.amount == Decimal("59.00")


def test_missing_due_is_hard_error() -> None:
    with pytest.raises(MissingFieldError, match="--ai"):
        parse_invoice_xml(CII_MISSING_DUE_XML)


def test_amount_falls_back_to_grand_total() -> None:
    extracted = parse_invoice_xml(CII_GRAND_TOTAL_ONLY_XML)
    assert extracted.data.amount == Decimal("119.00")


def test_invoice_currency_code_variant() -> None:
    extracted = parse_invoice_xml(CII_INVOICE_CURRENCY_VARIANT_XML)
    assert extracted.data.currency == "EUR"


def test_zugferd_1_document_namespace() -> None:
    extracted = parse_invoice_xml(ZUGFERD1_XML)
    data = extracted.data
    assert data.id == "RE-2026-0001"
    assert data.vendor == "Firma AG"
    assert str(data.issued) == "2026-08-01"
    assert str(data.due) == "2026-08-30"
    assert data.amount == Decimal("89.50")
    assert data.currency == "EUR"


def test_no_attachment_returns_none(tmp_path) -> None:
    pdf = make_zugferd_pdf(
        tmp_path / "plain.pdf", b"<unrelated><thing/></unrelated>", "leaflet.xml"
    )
    assert find_invoice_xml(pdf) is None


def test_sniffs_invoice_xml_under_unknown_name(tmp_path) -> None:
    pdf = make_zugferd_pdf(tmp_path / "weird.pdf", CII_INVOICE_XML, "attach_1.xml")
    found = find_invoice_xml(pdf)
    assert found is not None
    assert found[0] == "attach_1.xml"


def test_unreadable_pdf_raises_conversion_error(tmp_path) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"this is not a pdf")
    with pytest.raises(ConversionError):
        find_invoice_xml(garbage)


def test_unknown_xml_root_raises_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        parse_invoice_xml(b"<?xml version='1.0'?><Order><ID>1</ID></Order>")


def test_broken_xml_raises_unsupported() -> None:
    with pytest.raises(UnsupportedFormatError):
        parse_invoice_xml(b"<?xml version='1.0'?><rsm:CrossIndustryInvoice><unclosed>")
