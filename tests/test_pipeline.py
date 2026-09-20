"""Tests for the conversion pipeline dispatch: ZUGFeRD → text AI → vision AI."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from conftest import (
    CII_CREDIT_NOTE_XML,
    CII_INVOICE_XML,
    CII_MISSING_DUE_XML,
    INVOICE_TEXT_LINES,
    completion,
    invoice_json,
    make_blank_pdf,
    make_text_pdf,
    make_zugferd_pdf,
    patch_ai_client,
)
from nexfin_invoice.config import Config
from nexfin_invoice.errors import ConversionError, MissingFieldError
from nexfin_invoice.pipeline import convert


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=completion(invoice_json()))


def test_zugferd_path_signs_invoice_negative(tmp_path) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    result = convert(pdf, Config())
    assert result.method == "zugferd"
    assert result.invoice.amount == Decimal("-119.00")
    assert "amount: -119.00" in result.markdown
    assert "# RE-2026-0912 — Muller GmbH" in result.markdown


def test_zugferd_path_keeps_credit_note_positive(tmp_path) -> None:
    pdf = make_zugferd_pdf(tmp_path / "credit.pdf", CII_CREDIT_NOTE_XML)
    result = convert(pdf, Config())
    assert result.invoice.amount == Decimal("59.00")


def test_missing_due_is_hard_error_even_with_api_key(tmp_path) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_MISSING_DUE_XML)
    with pytest.raises(MissingFieldError, match="--ai"):
        convert(pdf, Config(api_key="k"))


def test_force_ai_uses_text_path(tmp_path, monkeypatch) -> None:
    pdf = make_text_pdf(tmp_path / "invoice.pdf", INVOICE_TEXT_LINES)
    log = patch_ai_client(monkeypatch, ok)
    result = convert(pdf, Config(api_key="k"), force_ai=True)
    assert result.method == "ai-text"
    assert result.invoice.id == "RE-2026-0777"
    sent_text = log.payloads[0]["messages"][1]["content"][0]["text"]
    assert "RE-2026-0777" in sent_text


def test_scanned_pdf_falls_back_to_vision(tmp_path, monkeypatch) -> None:
    pdf = make_blank_pdf(tmp_path / "scan.pdf")
    log = patch_ai_client(monkeypatch, ok)
    result = convert(pdf, Config(api_key="k"))
    assert result.method == "ai-vision"
    user_content = log.payloads[0]["messages"][1]["content"]
    image_parts = [part for part in user_content if part["type"] == "image_url"]
    assert image_parts
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_broken_xml_falls_back_to_ai(tmp_path, monkeypatch) -> None:
    pdf = make_zugferd_pdf(
        tmp_path / "invoice.pdf",
        b"<CrossIndustryInvoice><broken>",
        text_lines=INVOICE_TEXT_LINES,
    )
    patch_ai_client(monkeypatch, ok)
    result = convert(pdf, Config(api_key="k"))
    assert result.method == "ai-text"


def test_broken_xml_without_api_key_mentions_both(tmp_path) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", b"<CrossIndustryInvoice><broken>")
    with pytest.raises(ConversionError, match="OPENROUTER_API_KEY") as excinfo:
        convert(pdf, Config())
    assert "ZUGFeRD" in str(excinfo.value)


def test_no_api_key_plain_error(tmp_path) -> None:
    pdf = make_text_pdf(tmp_path / "invoice.pdf", INVOICE_TEXT_LINES)
    with pytest.raises(ConversionError, match="OPENROUTER_API_KEY"):
        convert(pdf, Config())


def test_unreadable_pdf_with_ai_key_gives_conversion_error(tmp_path) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"junk")
    with pytest.raises(ConversionError):
        convert(garbage, Config(api_key="k"))


def test_ai_failure_propagates_as_ai_error(tmp_path, monkeypatch) -> None:
    pdf = make_text_pdf(tmp_path / "invoice.pdf", INVOICE_TEXT_LINES)
    patch_ai_client(monkeypatch, lambda request: httpx.Response(503, text="overloaded"))
    with pytest.raises(ConversionError, match="503"):
        convert(pdf, Config(api_key="k"), force_ai=True)
