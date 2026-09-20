# CONTEXT

Domain glossary and context for this repository. Single-context repo; ADRs
live in `docs/adr/`.

## What this repo is

`nexfin-invoice` is a PyPI package and CLI (`nexfin-invoice`) that converts
invoice PDFs into the **nexfin inbox format**: Markdown files with YAML front
matter, exactly as specified in [`docs/invoice-format.md`](docs/invoice-format.md).
nexfin (the ledger app) watches an inbox folder and books these files.

## Glossary

- **nexfin** — the consuming ledger application. Owns the invoice file
  format contract in `docs/invoice-format.md`; that file is authoritative and
  is never regenerated from code.
- **Inbox / inbox format** — Markdown + YAML front matter files the user
  drops into the watched folder (`Nexfin/Rechnungen/open/`). User-owned:
  nexfin only writes the managed fields.
- **Managed fields** — `status`, `booked`, `paid_date`. Written only by
  nexfin on pay/undo. This package always emits `status: open`,
  `booked: ""`, `paid_date: ""` and never touches them afterwards.
- **Payment fields** — `iban`, `account_holder`, `reference`. Display-only
  in the nexfin pay dialog; never persisted in the nexfin database, read
  live from the file. Unknown front matter keys are preserved by nexfin.
- **Bookable** — a file nexfin can parse completely (all required fields
  valid). Incomplete files land under *needs attention* and are never
  bookable. This package refuses to emit unbookable files: a missing
  required field is a hard error with an `--ai` hint.
- **ZUGFeRD / Factur-X** — German/Franco-German e-invoice standards embedding
  CII XML (CrossIndustryInvoice, ZUGFeRD 2.x; CrossIndustryDocument, ZUGFeRD
  1.0) as a PDF attachment. TypeCode 380 = invoice, 381 = credit note.
- **XRechnung** — German e-invoice standard using UBL XML.
- **CII** — UN/CEFACT Cross Industry Invoice XML; uses decimal commas and
  format-102 dates (`YYYYMMDD`).
- **Credit note** — a refund document. Amount sign convention (finance_md):
  expense negative, income positive; a credit note's amount is stored
  positive.
- **Extraction path** — one of `zugferd` (deterministic XML), `ai-text`
  (PDF text + AI), `ai-vision` (rendered page images + AI). Dispatch order
  is fixed; vision only when no usable text layer.
- **Structured output** — OpenRouter `response_format: json_schema` request
  mode, generated from the `InvoiceData` pydantic model minus the managed
  fields, strict (`additionalProperties: false`, all fields required).

## Invariants worth keeping

- The front-matter writer is hand-rolled (no PyYAML): PyYAML would emit
  `-119.0`, date-typed scalars and different quoting, breaking the
  documented format.
- Filename convention `<id>.md` with `-1`, `-2`, … collision suffixes before
  the extension, matching the nexfin webhook intake.
- Amounts are `Decimal` end to end, rendered with exactly two decimals.
- Multi-invoice PDFs, OCR, paid-state management and webhook/server code are
  explicitly out of scope.
