"""Prompt templates for the AI extraction paths.

The system prompt carries the format rules from ``docs/invoice-format.md``:
signed two-decimal amounts (expense negative, credit note positive), ISO
dates, no thousands separators, normalized IBANs, verbatim invoice ids.
``categories``/``accounts`` from the config are injected as allowed values
when present; otherwise the model may propose them freely.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MAX_TEXT_CHARS", "build_messages", "build_system_prompt"]

# Extracted text is truncated to this length before being sent.
MAX_TEXT_CHARS = 60_000

_SYSTEM_PROMPT_TEMPLATE = """\
You are an invoice data extraction engine for the nexfin bookkeeping tool.
Extract the data of exactly one invoice from the provided document and return a single
JSON object matching the provided schema. No prose, no markdown fences.

Field rules:
- id: the invoice number exactly as printed in the document (verbatim, no reformatting).
- vendor: the seller/company that issued the invoice (the payee for a purchase invoice).
- issued, due: calendar dates in YYYY-MM-DD format. `due` is the payment due date; never
  leave it out if the document states one.
- amount: the total gross amount due (including taxes and shipping), with exactly 2 decimal
  places and no thousands separators. Sign convention: a purchase invoice (money owed by
  us) is negative (e.g. -119.00); a credit note or refund (money owed to us) is positive
  (e.g. 119.00).
- due_skonto: the last date (YYYY-MM-DD) on which the cash discount (Skonto) price applies;
  null when the document states no skonto terms.
- amount_skonto: the total gross payable within the skonto window (full amount minus the
  discount), with exactly 2 decimal places and no thousands separators, signed exactly like
  `amount` (purchase invoice negative, credit note positive). A stated discount percentage
  and deadline count as skonto terms: compute the reduced total (e.g. 3% within 14 days on
  119.00 → due_skonto = deadline date, amount_skonto = -115.43). Emit `due_skonto` and
  `amount_skonto` together, or leave both null — never only one of them.
- currency: ISO 4217 code (e.g. EUR, USD, CHF).
- iban: the payment IBAN, uppercase, without spaces or hyphens; null when absent. Verify the
  mod-97 checksum; if unsure between similar glyphs (0/O, 1/I/l), prefer the reading that
  yields a valid checksum.
- account_holder: the holder of the payment account (the payee), null when absent.
- reference: the payment reference / Verwendungszweck / transfer reference, null when absent.
- category: {category_rule}
- account: {account_rule}
- line_items: optional; include the individual invoice lines when they are discernible
  (quantity, description, net or gross line amount).

Never invent data. Use null for optional values that are not in the document. Return only
the JSON object."""


def build_system_prompt(
    categories: tuple[str, ...] | list[str] = (),
    accounts: tuple[str, ...] | list[str] = (),
) -> str:
    if categories:
        category_rule = "MUST be one of: " + ", ".join(categories) + "; null when none fits."
    else:
        category_rule = (
            "a short lowercase category label of your choice (e.g. office, travel); "
            "null when unclear."
        )
    if accounts:
        account_rule = "MUST be one of: " + ", ".join(accounts) + "; null when none fits."
    else:
        account_rule = "null (the user books manually)."
    return _SYSTEM_PROMPT_TEMPLATE.format(category_rule=category_rule, account_rule=account_rule)


def build_messages(
    *,
    text: str | None = None,
    images: list[str] | None = None,
    categories: tuple[str, ...] | list[str] = (),
    accounts: tuple[str, ...] | list[str] = (),
) -> list[dict[str, Any]]:
    """Build the chat messages for the text and/or vision extraction path."""
    if not text and not images:
        raise ValueError("build_messages needs text or images")
    content: list[dict[str, Any]] = []
    if text:
        content.append(
            {
                "type": "text",
                "text": f"Invoice document text:\n\n{text[:MAX_TEXT_CHARS]}",
            }
        )
    if images:
        if text:
            content.append(
                {
                    "type": "text",
                    "text": "Additionally, page images of the same invoice PDF follow.",
                }
            )
        else:
            content.append(
                {
                    "type": "text",
                    "text": "Extract the invoice data from these page images of an invoice PDF.",
                }
            )
        for url in images:
            content.append({"type": "image_url", "image_url": {"url": url}})

    return [
        {"role": "system", "content": build_system_prompt(categories, accounts)},
        {"role": "user", "content": content},
    ]
