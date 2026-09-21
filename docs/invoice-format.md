# Invoice file format

nexfin invoices are markdown files with a YAML front matter block, stored in
the watched inbox folder of the ledger owner (default `Nexfin/Rechnungen/open/`, configurable via the base/inbox folder settings).

> These files are **user-owned** — nexfin only touches the managed front
> matter fields (`status`, `booked`, `paid_date`) during pay/undo. They are a
> different class of file from the finance_md generated views (which carry a
> `GENERATED FILE` banner and are regenerated from the database).

## Example

```markdown
---
id: RE-2026-0912        # unique invoice number (required)
vendor: Muller GmbH     # (required)
issued: 2026-09-01      # date YYYY-MM-DD (required)
due_skonto: 2026-09-07  # date YYYY-MM-DD (optional (only if given in the invoice))
due: 2026-09-15         # date YYYY-MM-DD (required)
amount_skonto: -110.00  # signed, 2 decimals, no thousands separators (optional (only if given in the invoice))
amount: -119.00         # signed, 2 decimals, no thousands separators (required)
currency: EUR           # ISO code, must match the booking account (required)
category: office        # optional, finance_md category
account: Checking       # optional, preselected booking target
iban: DE89370400440532013000    # optional, SEPA transfer target
account_holder: Muller GmbH     # optional, shown in the pay dialog
reference: RE-2026-0912         # optional, transfer reference (Verwendungszweck)
status: open            # managed by nexfin: open | paid
booked: ""              # set on pay: "<account>/<tx-ref>"
paid_date: ""           # set on pay: YYYY-MM-DD
---

# RE-2026-0912 — Muller GmbH

## Line items (optional, free markdown)

- 2x Widget ... 45.00
- Shipping .... 29.00
```

## Field reference


| Field            | Required | Rules                                                                                                                                                                                                                                            |
| ------------------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`             | yes      | Unique invoice number. Duplicate ids across files are flagged as "needs attention" and are not bookable.                                                                                                                                         |
| `vendor`         | yes      | Free text. Used in the finance_md transaction description (`<id> <vendor>`).                                                                                                                                                                     |
| `issued`         | yes      | Valid calendar date,`YYYY-MM-DD`.                                                                                                                                                                                                                |
| `due`            | yes      | Valid calendar date,`YYYY-MM-DD`. Drives overdue/due-soon highlighting.                                                                                                                                                                          |
| `amount`         | yes      | finance_md convention: income`+`, expense `-`, at most 2 decimals, no thousands separators (`-119.00`, `2500`). Unquoted YAML numbers are normalized to two decimals on rewrite.                                                                 |
| `currency`       | yes      | ISO code. Must equal the booking account's currency; mismatches block the pay flow.                                                                                                                                                              |
| `category`       | no       | Falls back to the admin default, then`other`.                                                                                                                                                                                                    |
| `account`        | no       | Preselected in the pay dialog.                                                                                                                                                                                                                   |
| `iban`           | no       | SEPA transfer target shown in the pay dialog. Spaces and hyphens are stripped, the value is normalized to uppercase. Checked with the ISO 7064 mod-97 checksum — an invalid IBAN shows a**warning** in the pay dialog but never blocks booking. |
| `account_holder` | no       | Recipient name shown in the pay dialog next to the IBAN.                                                                                                                                                                                         |
| `reference`      | no       | Transfer reference (Verwendungszweck) offered in the pay dialog. When absent, the UI falls back to the invoice`id` and marks it as the default.                                                                                                  |
| `status`         | no       | `open` (default) or `paid`. Managed by nexfin.                                                                                                                                                                                                   |
| `booked`         | no       | Written by nexfin on pay as`<account>/<ref>`.                                                                                                                                                                                                    |
| `paid_date`      | no       | Written by nexfin on pay.                                                                                                                                                                                                                        |

Payment fields (`iban`, `account_holder`, `reference`) are **display-only**:
nexfin never writes them, they do not affect pay/booking, and they are not
persisted in `nx_invoices`. They are read from the live file when the pay
dialog opens, so manual edits show up immediately. Unknown/extra front matter
keys are preserved when nexfin stamps `status`/`booked`/`paid_date` on pay.

## Validation behavior

Files that fail to parse (missing required fields, broken YAML, bad
amount/date) are **not bookable**. They appear in the Invoices view under
**Needs attention** together with the parse error, and the scan records an
error row. Fix the file and the next scan makes it bookable again.

## Drift detection

The scan compares file location and front matter:

- `status: paid` but file still in the inbox → attention (e.g. after a manual
  move or an interrupted pay)
- `status: open` but file in the paid folder → attention
- paid file vanished from the paid folder → attention row kept for audit
- open file vanished from the inbox → mapping row removed

## Webhook intake

External systems `POST /apps/nexfin/api/webhook/invoice` (see README) and the
file lands in the inbox; filename collisions get a `-1`, `-2`, … suffix before
the `.md` extension.
