# ADR 0001: Skonto fields — reduced total, atomic pair

Date: 2026-09-21
Status: accepted

## Context

`docs/invoice-format.md` defines two optional front matter fields for cash
discount (Skonto) terms: `due_skonto` and `amount_skonto`. The nexfin ledger
app consumes these in its pay dialog; the converter (this repo) is
responsible for extracting them. ZUGFeRD/Factur-X/CII carries cash-discount
terms as `ApplicableTradePaymentDiscountTerms` inside
`SpecifiedTradePaymentTerms` (verified against the CII D16A bindings used by
ZUGFeRD 2.x); UBL/XRechnung carries them as `cbc:SettlementDiscountPercent`
/ `cbc:SettlementDiscountAmount` plus `cac:SettlementPeriod` in
`cac:PaymentTerms` (UBL 2.1 PaymentTermsType). XRechnung often states skonto
only as free text, which the deterministic path must not mine.

Two readings of `amount_skonto` were possible: the discount amount itself,
or the resulting reduced total payable.

## Decision

1. **`amount_skonto` is the reduced total payable** within the skonto window
   (`|amount| − discount`), signed exactly like `amount` (invoice →
   negative, credit note 381 → positive). The pay dialog then shows the
   actual skonto price directly; the discount math lives in the converter
   only, not in the ledger app.
2. **Atomic pair**: the writer never emits one field without the other.
   `InvoiceData` raises (`ValueError`) when exactly one of the pair is set.
   In the AI path this self-heals through the existing validation-retry
   loop.
3. **Never invent**: emit skonto only when a deadline and a discount are
   derivable from the document. A stated percentage counts as given — the
   reduced total is computed with `ROUND_HALF_UP`, 2 decimals. Period-only
   terms ("14 Tage" without an absolute date) never yield a date; in UBL an
   explicit `StartDate` + `DurationMeasure` (days) does.
4. **Warn-only sanity checks** (mirrors the IBAN precedent): `due_skonto >
   due`, sign mismatch with `amount`, `|amount_skonto| > |amount|` →
   `UserWarning`, never a booking blocker.
5. **Incomplete deterministic-path data → omit the pair + `UserWarning`**;
   `discount == 0` is omitted silently. A skonto problem never makes an
   otherwise bookable invoice unbookable.

## Consequences

- `structured_output_schema()` picks the fields up automatically (strict
  schema, every property required, `null` when absent); a partial pair from
  the model is rejected and retried.
- ZUGFeRD amounts stay absolute pre-sign; `pipeline._signed()` applies the
  sign to `amount_skonto` together with `amount`.
- The renderer treats `amount_skonto` as a raw two-decimal scalar like
  `amount` and keeps the documented key order
  (`issued, due_skonto, due, amount_skonto, amount`).
- The nexfin app side (pay dialog, scan, `nx_invoices`) is out of scope for
  this repo.
