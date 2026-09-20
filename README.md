# nexfin-invoice

Convert invoice PDFs into [nexfin](https://github.com/Kilo-Org/kilocode) inbox
Markdown files (format: [`docs/invoice-format.md`](docs/invoice-format.md)) —
deterministically when the PDF carries embedded e-invoice XML, otherwise with
an AI model.

## How it works

For each PDF, three extraction strategies are tried in order:

1. **ZUGFeRD / Factur-X / XRechnung** — an embedded invoice XML attachment
   (`factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml`, …) is parsed
   directly. No AI, no network, deterministic output.
2. **Text layer + AI** — the PDF text (pdfplumber) is sent to an OpenRouter
   chat model with a strict JSON schema.
3. **Vision + AI** — no usable text layer (scanned invoice): pages are
   rendered to PNG (pypdfium2, max 5 pages) and sent to a vision-capable
   model.

If ZUGFeRD data is incomplete (e.g. a missing due date), conversion fails
rather than writing a file nexfin would flag as *needs attention* — re-run
with `--ai` to let the model fill the gaps. A signed two-decimal amount is
applied automatically: purchase invoice → negative, credit note (TypeCode
381) → positive.

## Install

```console
pip install nexfin-invoice
```

Requires Python ≥ 3.11.

## Usage

```console
# writes RE-2026-0912.md into out/
nexfin-invoice convert invoice.pdf -o out/

# several files; name collisions get a -1, -2 suffix (webhook convention)
nexfin-invoice convert *.pdf -o Nexfin/Rechnungen/open/

# print instead of write
nexfin-invoice convert invoice.pdf --stdout

# skip ZUGFeRD XML parsing, let the AI fill missing/odd fields
nexfin-invoice convert invoice.pdf -o out/ --ai

# pick a different model for this run
nexfin-invoice convert invoice.pdf -o out/ --model anthropic/claude-3.5-sonnet
```

Exit codes: `0` all converted, `1` at least one failure, `2` usage error.

## Library use

```python
from nexfin_invoice import convert, load_config

config = load_config()
result = convert("invoice.pdf", config)
print(result.method)    # "zugferd" | "ai-text" | "ai-vision"
print(result.markdown)  # full .md file content
```

## Configuration

Precedence per key: **CLI flag > environment variable > TOML config > default**.

Config file: `~/.config/nexfin-invoice/config.toml` (override the location
with `NEXFIN_INVOICE_CONFIG` or `--config`):

```toml
api_key = "sk-or-v1-..."          # OpenRouter API key
model = "google/gemini-2.5-flash" # AI extraction model
categories = ["office", "travel"] # allowed AI-proposed categories
accounts = ["Checking"]           # allowed AI-proposed booking accounts
output_dir = "Nexfin/Rechnungen/open"  # default for -o
```

Environment variables:

- `OPENROUTER_API_KEY` — API key (wins over the config file)
- `NEXFIN_INVOICE_MODEL` — model (wins over the config file)
- `NEXFIN_INVOICE_CONFIG` — config file location

The default model is `google/gemini-2.5-flash` (cheap and vision-capable).
Any OpenRouter model works; vision-capable models are needed for scanned
invoices. Model support for structured output varies — the client falls back
to plain JSON prompting automatically.

## Privacy note

Anything the deterministic ZUGFeRD path cannot handle is sent to OpenRouter
(invoice text, or page images for scanned PDFs). If that is unacceptable for
an invoice, don't convert it with the AI path. The API key is never logged.

## Validation guarantees

Output files match `docs/invoice-format.md`: fixed front-matter key order,
two-decimal raw amount scalars (`amount: -119.00`), quoted empty strings,
`status: open` / `booked: ""` / `paid_date: ""`. Dates are validated calendar
dates, currency an ISO 4217 code, IBANs are normalized (spaces/hyphens
stripped, uppercased) with a warn-only mod-97 checksum check — mirroring the
nexfin pay dialog, which warns but never blocks.

## Development

```console
pip install -e ".[dev]"
ruff check .
mypy
pytest
python -m build && twine check dist/*
```

## License

MIT — see [LICENSE](LICENSE). Dependencies: pikepdf (MPL-2.0), pdfplumber
(MIT), pypdfium2 (Apache-2.0/BSD-3), httpx (BSD-3), pydantic (MIT).
