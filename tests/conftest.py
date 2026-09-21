"""Shared fixtures: synthetic PDFs (pikepdf-built), invoice XML fixtures,
and helpers to fake the OpenRouter API with httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pikepdf
import pytest

from nexfin_invoice.ai.openrouter import OpenRouterClient

# --------------------------------------------------------------------------
# Invoice XML fixtures
# --------------------------------------------------------------------------

CII_INVOICE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>RE-2026-0912</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20260901</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Muller GmbH</ram:Name>
      </ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:PaymentReference>RE-2026-0912</ram:PaymentReference>
      <ram:SpecifiedTradeSettlementPaymentMeans>
        <ram:PayeePartyCreditorFinancialAccount>
          <ram:IBANID>DE89 3704-0044 0532 0130 00</ram:IBANID>
        </ram:PayeePartyCreditorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>
      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>
      <ram:DocumentCurrencyCode>EUR</ram:DocumentCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxBasisTotalAmount>100.00</ram:TaxBasisTotalAmount>
        <ram:GrandTotalAmount>119,00</ram:GrandTotalAmount>
        <ram:DuePayableAmount>119,00</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""

CII_CREDIT_NOTE_XML = CII_INVOICE_XML.replace(
    b"<ram:ID>RE-2026-0912</ram:ID>", b"<ram:ID>RE-2026-0912-GS</ram:ID>"
)
CII_CREDIT_NOTE_XML = CII_CREDIT_NOTE_XML.replace(
    b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>"
)
CII_CREDIT_NOTE_XML = CII_CREDIT_NOTE_XML.replace(
    b"<ram:DuePayableAmount>119,00</ram:DuePayableAmount>",
    b"<ram:DuePayableAmount>59,00</ram:DuePayableAmount>",
)

CII_MISSING_DUE_XML = CII_INVOICE_XML.replace(
    b"""
      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>""",
    b"",
)

CII_GRAND_TOTAL_ONLY_XML = CII_INVOICE_XML.replace(
    b"<ram:DuePayableAmount>119,00</ram:DuePayableAmount>", b""
)

# Older CII generators use InvoiceCurrencyCode instead of DocumentCurrencyCode
# (seen in real-world Factur-X files, e.g. Würth).
CII_INVOICE_CURRENCY_VARIANT_XML = CII_INVOICE_XML.replace(
    b"<ram:DocumentCurrencyCode>EUR</ram:DocumentCurrencyCode>",
    b"<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>",
)

# Skonto terms with a printed discount amount and an absolute deadline
# (CII D16A element name ApplicableTradePaymentDiscountTerms).
CII_SKONTO_XML = CII_INVOICE_XML.replace(
    b"""      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>""",
    b"""      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
        <ram:ApplicableTradePaymentDiscountTerms>
          <ram:BasisDateTime>
            <udt:DateTimeString format="102">20260910</udt:DateTimeString>
          </ram:BasisDateTime>
          <ram:ActualDiscountAmount>7,14</ram:ActualDiscountAmount>
        </ram:ApplicableTradePaymentDiscountTerms>
      </ram:SpecifiedTradePaymentTerms>""",
)

# Skonto stated as percentage only (3% off the payable amount).
CII_SKONTO_PERCENT_XML = CII_INVOICE_XML.replace(
    b"""      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>""",
    b"""      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
        <ram:ApplicableTradePaymentDiscountTerms>
          <ram:BasisDateTime>
            <udt:DateTimeString format="102">20260910</udt:DateTimeString>
          </ram:BasisDateTime>
          <ram:BasisAmount>119,00</ram:BasisAmount>
          <ram:CalculationPercent>3.00</ram:CalculationPercent>
        </ram:ApplicableTradePaymentDiscountTerms>
      </ram:SpecifiedTradePaymentTerms>""",
)

# Discount with no absolute deadline ("within 14 days" only) — omitted + warning.
CII_SKONTO_PERIOD_ONLY_XML = CII_INVOICE_XML.replace(
    b"""      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>""",
    b"""      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20260915</udt:DateTimeString>
        </ram:DueDateDateTime>
        <ram:ApplicableTradePaymentDiscountTerms>
          <ram:BasisPeriodMeasure unitCode="DAY">14</ram:BasisPeriodMeasure>
          <ram:ActualDiscountAmount>7,14</ram:ActualDiscountAmount>
        </ram:ApplicableTradePaymentDiscountTerms>
      </ram:SpecifiedTradePaymentTerms>""",
)

# Nonsense: discount larger than the payable amount — omitted + warning.
CII_SKONTO_OVERSIZE_XML = CII_SKONTO_XML.replace(
    b"<ram:ActualDiscountAmount>7,14</ram:ActualDiscountAmount>",
    b"<ram:ActualDiscountAmount>200,00</ram:ActualDiscountAmount>",
)

# Zero discount states no skonto price — omitted silently.
CII_SKONTO_ZERO_XML = CII_SKONTO_XML.replace(
    b"<ram:ActualDiscountAmount>7,14</ram:ActualDiscountAmount>",
    b"<ram:ActualDiscountAmount>0,00</ram:ActualDiscountAmount>",
)

CII_SKONTO_CREDIT_NOTE_XML = CII_SKONTO_XML.replace(
    b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>"
)

ZUGFERD1_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CrossIndustryDocument xmlns="urn:ferd:CrossIndustryDocument:invoice:1p0">
  <SpecifiedExchangedDocumentContext>
    <GuidelineParameter>urn:ferd:invoice:rc:comfort</GuidelineParameter>
  </SpecifiedExchangedDocumentContext>
  <HeaderExchangedDocument>
    <ID>RE-2026-0001</ID>
    <TypeCode>380</TypeCode>
    <IssueDateTime>
      <DateTimeString format="102">20260801</DateTimeString>
    </IssueDateTime>
  </HeaderExchangedDocument>
  <SpecifiedSupplyChainTradeTransaction>
    <ApplicableSupplyChainTradeAgreement>
      <SellerTradeParty>
        <Name>Firma AG</Name>
      </SellerTradeParty>
    </ApplicableSupplyChainTradeAgreement>
    <ApplicableSupplyChainTradeSettlement>
      <CurrencyCode>EUR</CurrencyCode>
      <SpecifiedTradePaymentTerms>
        <DueDateDateTime>
          <DateTimeString format="102">20260830</DateTimeString>
        </DueDateDateTime>
      </SpecifiedTradePaymentTerms>
      <SpecifiedTradeSettlementMonetarySummation>
        <DuePayableAmount>89,50</DuePayableAmount>
      </SpecifiedTradeSettlementMonetarySummation>
    </ApplicableSupplyChainTradeSettlement>
  </SpecifiedSupplyChainTradeTransaction>
</CrossIndustryDocument>
"""

# ZUGFeRD 1.0 skonto variant with a plain BasisDate fallback element.
ZUGFERD1_SKONTO_XML = ZUGFERD1_XML.replace(
    b"""      <SpecifiedTradePaymentTerms>
        <DueDateDateTime>
          <DateTimeString format="102">20260830</DateTimeString>
        </DueDateDateTime>
      </SpecifiedTradePaymentTerms>""",
    b"""      <SpecifiedTradePaymentTerms>
        <DueDateDateTime>
          <DateTimeString format="102">20260830</DateTimeString>
        </DueDateDateTime>
        <SpecifiedTradePaymentDiscountTerms>
          <BasisDate>20260815</BasisDate>
          <ActualDiscountAmount>4,50</ActualDiscountAmount>
        </SpecifiedTradePaymentDiscountTerms>
      </SpecifiedTradePaymentTerms>""",
)

UBL_INVOICE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ubl:Invoice
    xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>XR-2026-042</cbc:ID>
  <cbc:IssueDate>2026-09-05</cbc:IssueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cbc:PaymentID>XR-2026-042 Zahlungsziel 30 Tage</cbc:PaymentID>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName>
        <cbc:Name>Franz Handel GmbH</cbc:Name>
      </cac:PartyName>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Franz Handel GmbH</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:PaymentMeans>
    <cbc:PaymentDueDate>2026-09-30</cbc:PaymentDueDate>
    <cac:PayeeFinancialAccount>
      <cbc:ID>DE02120300000000202051</cbc:ID>
      <cbc:Name>Franz Handel GmbH</cbc:Name>
    </cac:PayeeFinancialAccount>
  </cac:PaymentMeans>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount currencyID="EUR">200.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">238.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">238.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</ubl:Invoice>
"""

UBL_CREDIT_NOTE_XML = UBL_INVOICE_XML.replace(
    b"<ubl:Invoice", b"<ubl:CreditNote", 1
).replace(
    b'xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"',
    b'xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"',
).replace(
    b"</ubl:Invoice>", b"</ubl:CreditNote>"
).replace(
    b"<cbc:ID>XR-2026-042</cbc:ID>", b"<cbc:ID>CN-2026-007</cbc:ID>"
).replace(
    b"<cbc:PayableAmount currencyID=\"EUR\">238.00</cbc:PayableAmount>",
    b"<cbc:PayableAmount currencyID=\"EUR\">50.00</cbc:PayableAmount>",
)

UBL_MISSING_DUE_XML = UBL_INVOICE_XML.replace(
    b"<cbc:PaymentDueDate>2026-09-30</cbc:PaymentDueDate>", b""
).replace(b"<cbc:DueDate>...</cbc:DueDate>", b"")

UBL_FALLBACK_AMOUNT_XML = UBL_INVOICE_XML.replace(
    b"<cbc:PayableAmount currencyID=\"EUR\">238.00</cbc:PayableAmount>", b""
)

# UBL skonto terms: percent + settlement period with an absolute end date.
UBL_SKONTO_XML = UBL_INVOICE_XML.replace(
    b"""  <cac:LegalMonetaryTotal>""",
    b"""  <cac:PaymentTerms>
    <cbc:SettlementDiscountPercent>2.50</cbc:SettlementDiscountPercent>
    <cac:SettlementPeriod>
      <cbc:EndDate>2026-09-20</cbc:EndDate>
    </cac:SettlementPeriod>
  </cac:PaymentTerms>
  <cac:LegalMonetaryTotal>""",
)

# --------------------------------------------------------------------------
# Synthetic PDF builders
# --------------------------------------------------------------------------


def make_zugferd_pdf(
    path: Path,
    xml: bytes = CII_INVOICE_XML,
    attachment_name: str = "factur-x.xml",
    text_lines: list[str] | None = None,
) -> Path:
    """Build a minimal PDF with an embedded invoice XML attachment."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    if text_lines:
        font = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type1"),
            BaseFont=pikepdf.Name("/Helvetica"),
        )
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        operations = []
        y = 720
        for line in text_lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            operations.append(f"BT /F1 12 Tf 72 {y} Td ({escaped}) Tj ET".encode("ascii"))
            y -= 24
        page.contents_add(pdf.make_stream(b"\n".join(operations)))
    filespec = pikepdf.AttachedFileSpec(
        pdf,
        xml,
        description="invoice xml",
        filename=attachment_name,
        mime_type="text/xml",
    )
    pdf.attachments[attachment_name] = filespec
    pdf.save(path)
    return path


def make_text_pdf(path: Path, lines: list[str]) -> Path:
    """Build a minimal PDF with a real text layer (pdfplumber can read it)."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    font = pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/Helvetica"),
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    operations = []
    y = 720
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        operations.append(f"BT /F1 12 Tf 72 {y} Td ({escaped}) Tj ET".encode("ascii"))
        y -= 24
    page.contents_add(pdf.make_stream(b"\n".join(operations)))
    pdf.save(path)
    return path


INVOICE_TEXT_LINES = [
    "Invoice RE-2026-0777",
    "Muller GmbH, Musterstrasse 4, 12345 Berlin",
    "Invoice date: 2026-09-01   Due date: 2026-09-15",
    "1x Office chair 89.00 EUR, 19% VAT 16.91, total 105.91 EUR",
    "Please transfer the total amount of 105.91 EUR until 2026-09-15.",
    "IBAN DE89370400440532013000, reference RE-2026-0777.",
]


def make_blank_pdf(path: Path) -> Path:
    """Build a PDF with no text at all (the 'scanned document' case)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)
    return path


# --------------------------------------------------------------------------
# OpenRouter mock helpers
# --------------------------------------------------------------------------


def completion(content: str) -> dict[str, Any]:
    """OpenRouter-shaped chat completion response body."""
    return {
        "id": "chatcmpl-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"total_tokens": 1},
    }


def invoice_json(**overrides: Any) -> str:
    """A valid model answer as JSON string, overridable per field."""
    data: dict[str, Any] = {
        "id": "RE-2026-0777",
        "vendor": "Muller GmbH",
        "issued": "2026-09-01",
        "due": "2026-09-15",
        "amount": -105.91,
        "currency": "EUR",
        "category": None,
        "account": None,
        "iban": "DE89370400440532013000",
        "account_holder": "Muller GmbH",
        "reference": "RE-2026-0777",
        "due_skonto": None,
        "amount_skonto": None,
        "line_items": [{"description": "Office chair", "quantity": 1, "amount": 89.0}],
    }
    data.update(overrides)
    return json.dumps(data)


class RequestLog:
    """Records request payloads posted through the mock transport."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def last(self) -> dict[str, Any]:
        return self.payloads[-1]


def make_mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "test-key",
    model: str = "test-model",
) -> tuple[OpenRouterClient, RequestLog]:
    log = RequestLog()

    def logging_handler(request: httpx.Request) -> httpx.Response:
        log.payloads.append(json.loads(request.content.decode("utf-8")))
        return handler(request)

    client = OpenRouterClient(
        api_key, model, transport=httpx.MockTransport(logging_handler)
    )
    return client, log


def patch_ai_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> RequestLog:
    """Route the pipeline's OpenRouterClient through a MockTransport handler."""
    client, log = make_mock_client(handler)

    def factory(api_key: str, model: str, **_kwargs: object) -> OpenRouterClient:
        assert api_key == "k"
        return client

    monkeypatch.setattr("nexfin_invoice.ai.openrouter.OpenRouterClient", factory)
    return log


@pytest.fixture
def isolate_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point config resolution away from the developer's real environment."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NEXFIN_INVOICE_MODEL", raising=False)
    monkeypatch.setenv("NEXFIN_INVOICE_CONFIG", str(Path("<missing>") / "config.toml"))
