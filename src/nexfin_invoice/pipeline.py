"""Conversion pipeline: ZUGFeRD first, then text AI, then vision AI.

Dispatch order (unless ``force_ai``):

1. ZUGFeRD / Factur-X / XRechnung — embedded invoice XML, deterministic,
   no AI. A *missing required field* here is a hard error (the file would
   never be bookable); a *parse failure* falls through to the AI path.
2. Text layer + AI — pdfplumber text sent to an OpenRouter model.
3. Vision + AI — no usable text layer: render pages to PNG and send them
   to a vision-capable model.

Sign convention: ZUGFeRD amounts are stored absolute, so the pipeline signs
them (invoice → negative, credit note 381 → positive). The AI paths return
the amount already signed per the prompt rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .config import Config, load_config
from .errors import ConversionError, MissingFieldError
from .extract import textlayer, vision, zugferd
from .model import InvoiceData
from .render import render

__all__ = ["ConversionResult", "convert", "convert_many"]


@dataclass(frozen=True)
class ConversionResult:
    """Result of converting one PDF."""

    source: Path
    markdown: str
    invoice: InvoiceData
    method: str  # "zugferd" | "ai-text" | "ai-vision"


def convert(
    pdf_path: str | Path,
    config: Config | None = None,
    *,
    force_ai: bool = False,
) -> ConversionResult:
    """Convert one invoice PDF into nexfin inbox markdown.

    This is side-effect free: it returns the markdown string; writing files
    is the caller's (CLI's) job.
    """
    cfg = config if config is not None else load_config()
    path = Path(pdf_path)

    if not force_ai:
        xml_reason: str | None = None
        try:
            extracted = zugferd.extract(path)
        except ConversionError as exc:
            if isinstance(exc, MissingFieldError):
                raise
            xml_reason = str(exc)
        else:
            if extracted is not None:
                invoice = _signed(extracted.data, is_credit_note=extracted.is_credit_note)
                return ConversionResult(
                    source=path, markdown=render(invoice), invoice=invoice, method="zugferd"
                )
            xml_reason = None
        return _convert_with_ai(path, cfg, xml_reason=xml_reason)

    return _convert_with_ai(path, cfg, xml_reason=None)


def convert_many(
    pdf_paths: Sequence[str | Path],
    config: Config | None = None,
    *,
    force_ai: bool = False,
) -> list[ConversionResult]:
    """Convert several PDFs; raises on the first failure."""
    return [convert(path, config, force_ai=force_ai) for path in pdf_paths]


def _signed(invoice: InvoiceData, *, is_credit_note: bool) -> InvoiceData:
    update: dict[str, Decimal] = {}
    amount = abs(invoice.amount)
    if not is_credit_note:
        amount = -amount
    if amount != invoice.amount:
        update["amount"] = amount
    if invoice.amount_skonto is not None:
        skonto = abs(invoice.amount_skonto)
        if not is_credit_note:
            skonto = -skonto
        if skonto != invoice.amount_skonto:
            update["amount_skonto"] = skonto
    if not update:
        return invoice
    return invoice.model_copy(update=update)


def _convert_with_ai(path: Path, config: Config, *, xml_reason: str | None) -> ConversionResult:
    from .ai.openrouter import OpenRouterClient, extract_invoice_data

    if not config.api_key:
        hint = f" (ZUGFeRD extraction unavailable: {xml_reason})" if xml_reason else ""
        raise ConversionError(
            f"{path}: AI extraction needs an OpenRouter API key{hint}. "
            "Set OPENROUTER_API_KEY or api_key in the config file."
        )

    text: str | None = None
    images: list[str] | None = None
    try:
        text = textlayer.extract_text(path)
        if text is None:
            images = vision.render_page_images(path)
        client = OpenRouterClient(config.api_key, config.model)
        try:
            invoice = extract_invoice_data(
                client,
                text=text,
                images=images,
                categories=config.categories,
                accounts=config.accounts,
            )
        finally:
            client.close()
    except ValueError as exc:
        raise ConversionError(str(exc)) from exc

    method = "ai-vision" if images else "ai-text"
    return ConversionResult(source=path, markdown=render(invoice), invoice=invoice, method=method)
