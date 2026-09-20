"""ZUGFeRD / Factur-X / XRechnung extraction.

Strategy:

1. Scan PDF attachments for an embedded invoice XML (``factur-x.xml``,
   ``zugferd-invoice.xml``, ``xrechnung.xml``, or any XML attachment whose
   root element sniffs as CII/UBL).
2. Parse CII (ZUGFeRD 1.0 and 2.x / Factur-X) or UBL (XRechnung) with
   local-name matching, tolerating profile and namespace variations.
3. Fail loudly on missing required fields (a silently incomplete file would
   land under "needs attention" and never be bookable) — callers can fall
   back to the AI path or surface the ``--ai`` hint.

Numbers in CII use decimal commas; dates arrive as format-102
(``YYYYMMDD``) strings. Both are normalized here. Amounts come back as
absolute values plus an ``is_credit_note`` flag; the pipeline applies the
sign.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..errors import ConversionError, MissingFieldError, UnsupportedFormatError
from ..model import InvoiceData, parse_decimal_number

__all__ = ["ExtractedInvoice", "extract", "find_invoice_xml", "parse_invoice_xml"]

_KNOWN_ATTACHMENT_NAMES = frozenset(
    {"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "facturplus.xml", "efactur.xml"}
)
_XML_MARKERS = (
    b"CrossIndustryInvoice",
    b"CrossIndustryDocument",
    b"urn:oasis:names:specification:ubl",
)
_HEAD_SNIFF_BYTES = 8192

_DATE_102_RE = re.compile(r"^\d{8}$")

_CII_DOC_CONTAINER = ("ExchangedDocument", "HeaderExchangedDocument")
_CII_SETTLEMENT = (
    "ApplicableHeaderTradeSettlement",
    "ApplicableSupplyChainTradeSettlement",
    "SpecifiedSupplyChainTradeSettlement",
)
_CII_AGREEMENT = ("ApplicableHeaderTradeAgreement", "ApplicableSupplyChainTradeAgreement")
_CII_SUMMATION = (
    "SpecifiedTradeSettlementHeaderMonetarySummation",
    "SpecifiedTradeSettlementMonetarySummation",
)


@dataclass(frozen=True)
class ExtractedInvoice:
    """Structured extraction result before sign normalization."""

    data: InvoiceData  # amount is the absolute value from the document
    is_credit_note: bool


def extract(pdf_path: str | Path) -> ExtractedInvoice | None:
    """Find and parse an embedded invoice XML.

    Returns ``None`` when the PDF carries no invoice attachment.
    """
    found = find_invoice_xml(pdf_path)
    if found is None:
        return None
    _name, xml_bytes = found
    return parse_invoice_xml(xml_bytes)


def find_invoice_xml(pdf_path: str | Path) -> tuple[str, bytes] | None:
    """Return ``(attachment_name, xml_bytes)`` for the first invoice XML attachment."""
    import pikepdf

    try:
        with pikepdf.open(pdf_path) as pdf:
            entries: list[tuple[str, bytes]] = []
            for name, attachment in pdf.attachments.items():
                try:
                    entries.append((str(name), _attachment_payload(attachment)))
                except Exception:
                    continue
    except pikepdf.PdfError as exc:
        raise ConversionError(f"{pdf_path}: not a readable PDF ({exc})") from exc

    known: tuple[tuple[str, bytes], ...] = ()
    sniffed: tuple[tuple[str, bytes], ...] = ()
    for name, data in entries:
        if not _looks_like_invoice_xml(data):
            continue
        if name.lower() in _KNOWN_ATTACHMENT_NAMES:
            known += ((name, data),)
        else:
            sniffed += ((name, data),)
    if known:
        return known[0]
    if sniffed:
        return sniffed[0]
    return None


def _attachment_payload(attachment: Any) -> bytes:
    """Read attachment bytes across pikepdf API generations.

    pikepdf >= 10 exposes ``AttachedFileSpec.get_file().read_bytes()``;
    older versions expose ``Attachment.read_bytes()`` directly.
    """
    read_bytes = getattr(attachment, "read_bytes", None)
    if callable(read_bytes):
        return bytes(read_bytes())
    get_file = getattr(attachment, "get_file", None)
    if callable(get_file):
        return bytes(get_file().read_bytes())
    raise ConversionError("cannot read PDF attachment with this pikepdf version")


def _looks_like_invoice_xml(data: bytes) -> bool:
    head = data[:_HEAD_SNIFF_BYTES]
    return any(marker in head for marker in _XML_MARKERS)


def parse_invoice_xml(xml_bytes: bytes) -> ExtractedInvoice:
    """Parse CII or UBL invoice XML into an :class:`ExtractedInvoice`."""
    root = _parse_xml(xml_bytes)
    local = _local(root.tag)
    if local in ("CrossIndustryInvoice", "CrossIndustryDocument"):
        return _parse_cii(root)
    if local in ("Invoice", "CreditNote"):
        return _parse_ubl(root, credit_note=local == "CreditNote")
    raise UnsupportedFormatError(f"unsupported invoice XML root element {local!r}")


def _parse_xml(xml_bytes: bytes) -> ET.Element:
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError:
        try:
            return ET.fromstring(xml_bytes.decode("utf-8-sig").strip())
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise UnsupportedFormatError(f"invoice XML is not well-formed: {exc}") from exc


# -- XML helpers (namespace-agnostic) --------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(elem: ET.Element, *names: str) -> list[ET.Element]:
    wanted = set(names)
    return [child for child in elem if _local(child.tag) in wanted]


def _first_child(elem: ET.Element | None, *names: str) -> ET.Element | None:
    if elem is None:
        return None
    for name in names:
        for child in elem:
            if _local(child.tag) == name:
                return child
    return None


def _child_text(elem: ET.Element | None, *names: str) -> str | None:
    child = _first_child(elem, *names)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _first_descendant(elem: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    for node in elem.iter():
        if node is not elem and _local(node.tag) in wanted:
            return node
    return None


def _to_date(text: str) -> date:
    """Convert a CII format-102 date (``YYYYMMDD``) or ISO date to :class:`date`."""
    if _DATE_102_RE.match(text):
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise UnsupportedFormatError(f"unrecognized invoice date {text!r}") from exc


# -- CII (ZUGFeRD 1.0 + 2.x / Factur-X) -------------------------------------


def _parse_cii(root: ET.Element) -> ExtractedInvoice:
    doc = _first_descendant(root, *_CII_DOC_CONTAINER)
    settlement = _first_descendant(root, *_CII_SETTLEMENT)
    agreement = _first_descendant(root, *_CII_AGREEMENT)

    missing: list[str] = []
    invoice_id = _child_text(doc, "ID")
    issued_raw = _child_text(_first_child(doc, "IssueDateTime"), "DateTimeString")
    due_raw = _due_date_cii(settlement)
    # D16B uses DocumentCurrencyCode; older CII variants InvoiceCurrencyCode;
    # ZUGFeRD 1.0 CurrencyCode.
    currency = _child_text(
        settlement, "DocumentCurrencyCode", "InvoiceCurrencyCode", "CurrencyCode"
    )
    amount = _amount_cii(settlement)

    if not invoice_id:
        missing.append("id")
    if not issued_raw:
        missing.append("issued")
    if not due_raw:
        missing.append("due")
    if not currency:
        missing.append("currency")
    if amount is None:
        missing.append("amount")
    if missing:
        raise MissingFieldError(_missing_message(missing))

    assert doc is not None and settlement is not None
    assert invoice_id is not None and issued_raw is not None and due_raw is not None
    assert currency is not None and amount is not None
    vendor = _seller_name_cii(agreement)
    if not vendor:
        missing.append("vendor")
        raise MissingFieldError(_missing_message(missing))

    type_code = _child_text(doc, "TypeCode") or ""
    data = InvoiceData(
        id=invoice_id,
        vendor=vendor,
        issued=_to_date(issued_raw),
        due=_to_date(due_raw),
        amount=amount,
        currency=currency.upper(),
        iban=_iban_cii(settlement),
        account_holder=_account_holder_cii(settlement, vendor),
        reference=_child_text(settlement, "PaymentReference"),
    )
    return ExtractedInvoice(data=data, is_credit_note=type_code == "381")


def _due_date_cii(settlement: ET.Element | None) -> str | None:
    if settlement is None:
        return None
    for terms in _children(settlement, "SpecifiedTradePaymentTerms"):
        raw = _child_text(_first_child(terms, "DueDateDateTime"), "DateTimeString")
        if raw:
            return raw
    return None


def _amount_cii(settlement: ET.Element | None) -> Decimal | None:
    if settlement is None:
        return None
    summation = _first_descendant(settlement, *_CII_SUMMATION)
    if summation is None:
        return None
    for name in ("DuePayableAmount", "GrandTotalAmount", "TaxBasisTotalAmount"):
        raw = _child_text(summation, name)
        if raw:
            try:
                return abs(parse_decimal_number(raw))
            except ArithmeticError:
                continue
    return None


def _iban_cii(settlement: ET.Element) -> str | None:
    account = _first_descendant(settlement, "PayeePartyCreditorFinancialAccount")
    return _child_text(account, "IBANID")


def _account_holder_cii(settlement: ET.Element, fallback: str) -> str | None:
    payee = _first_descendant(settlement, "PayeeTradeParty")
    return _child_text(payee, "Name") or fallback


def _seller_name_cii(agreement: ET.Element | None) -> str | None:
    seller = _first_child(agreement, "SellerTradeParty")
    return _child_text(seller, "Name")


# -- UBL (XRechnung) ---------------------------------------------------------


def _parse_ubl(root: ET.Element, *, credit_note: bool) -> ExtractedInvoice:
    missing: list[str] = []

    invoice_id = _child_text(root, "ID")
    issued_raw = _child_text(root, "IssueDate")
    currency = _child_text(root, "DocumentCurrencyCode")
    due_raw = _due_date_ubl(root)
    amount = _amount_ubl(root)
    vendor = _vendor_ubl(root)

    if not invoice_id:
        missing.append("id")
    if not issued_raw:
        missing.append("issued")
    if not due_raw:
        missing.append("due")
    if not currency:
        missing.append("currency")
    if amount is None:
        missing.append("amount")
    if not vendor:
        missing.append("vendor")
    if missing:
        raise MissingFieldError(_missing_message(missing))

    assert invoice_id is not None and issued_raw is not None and due_raw is not None
    assert currency is not None and amount is not None and vendor is not None
    type_code = _child_text(root, "InvoiceTypeCode") or ""
    data = InvoiceData(
        id=invoice_id,
        vendor=vendor,
        issued=_to_date(issued_raw),
        due=_to_date(due_raw),
        amount=amount,
        currency=currency.upper(),
        iban=_iban_ubl(root),
        account_holder=_account_holder_ubl(root, vendor),
        reference=_reference_ubl(root),
    )
    is_credit = credit_note or type_code == "381"
    return ExtractedInvoice(data=data, is_credit_note=is_credit)


def _due_date_ubl(root: ET.Element) -> str | None:
    payment_means = _first_descendant(root, "PaymentMeans")
    due = _child_text(payment_means, "PaymentDueDate")
    if due:
        return due
    return _child_text(root, "DueDate")


def _amount_ubl(root: ET.Element) -> Decimal | None:
    totals = _first_descendant(root, "LegalMonetaryTotal")
    if totals is None:
        return None
    for name in ("PayableAmount", "TaxInclusiveAmount", "TaxExclusiveAmount"):
        raw = _child_text(totals, name)
        if raw:
            try:
                return abs(parse_decimal_number(raw))
            except ArithmeticError:
                continue
    return None


def _vendor_ubl(root: ET.Element) -> str | None:
    supplier = _first_descendant(root, "AccountingSupplierParty")
    party = _first_descendant(supplier, "Party") if supplier is not None else None
    if party is None:
        return None
    legal_entity = _first_child(party, "PartyLegalEntity")
    name = _child_text(legal_entity, "RegistrationName")
    if name:
        return name
    party_name = _first_child(party, "PartyName")
    return _child_text(party_name, "Name")


def _iban_ubl(root: ET.Element) -> str | None:
    account = _first_descendant(root, "PayeeFinancialAccount")
    return _child_text(account, "ID")


def _account_holder_ubl(root: ET.Element, fallback: str) -> str | None:
    payee = _first_descendant(root, "PayeeTradeParty")
    if payee is not None:
        name = _child_text(_first_child(payee, "PartyName"), "Name")
        if name:
            return name
        name = _child_text(_first_child(payee, "PartyLegalEntity"), "RegistrationName")
        if name:
            return name
    return fallback


def _reference_ubl(root: ET.Element) -> str | None:
    payment_means = _first_descendant(root, "PaymentMeans")
    reference = _child_text(payment_means, "PaymentID")
    if reference:
        return reference
    return _child_text(root, "PaymentID")


# -- error messages ----------------------------------------------------------


def _missing_message(missing: list[str]) -> str:
    fields = ", ".join(sorted(set(missing)))
    return (
        f"invoice XML is missing required field(s): {fields}. "
        "Writing an incomplete file would leave it unbookable in nexfin. "
        "Re-run with --ai to extract the missing fields from the PDF with an AI model."
    )
