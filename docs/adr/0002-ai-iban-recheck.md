# ADR 0002: AI-path IBAN recheck — one corrective redo

Date: 2026-09-23
Status: accepted

## Context

AI extraction (text layer or vision) is noisy: OCR-style confusions between
similar glyphs (`0/O`, `1/I/l`) regularly corrupt the payment IBAN, which
then fails the format regex or the ISO 7064 mod-97 checksum. The
deterministic ZUGFeRD/Factur-X/XRechnung path reads the IBAN from structured
XML, where that noise does not occur; its IBAN semantics are warn-only (the
nexfin pay dialog warns but never blocks booking).

A blind re-request would be useless: `temperature` is 0, so the model would
return the same wrong IBAN again.

## Decision

1. **Validate after extraction, AI path only.** The existing checks (format
   regex + ISO 7064 mod-97, refactored into the shared `iban_problem()`
   helper) are applied to the extracted `iban`. No country-length registry,
   no BIC/SEPA checks.
2. **A missing IBAN never retries.** `iban: null` is legitimate ("never
   invent data"); only a present-but-invalid IBAN triggers the redo.
3. **One corrective redo.** The rejected output plus a feedback message (the
   IBAN and its problem, with OCR-confusion hints) are appended to the
   conversation and the full corrected JSON is requested. The redo is a
   separate stage after the schema-validation retry, each with a single
   corrective call (worst case 3 AI calls: initial + schema retry + IBAN
   redo).
4. **The full corrected invoice wins.** A schema-valid redo answer becomes
   the result, even if its IBAN is still invalid or now null. No field-level
   merging.
5. **Terminal behavior stays warn-only.** An unusable redo answer — or a
   failed redo call (API/transport error) — keeps the original invoice; an
   IBAN that still fails keeps today's warn-only semantics. The IBAN stage
   never raises.

## Consequences

- Clean extractions cost exactly one AI call, ZUGFeRD none; an invalid IBAN
  adds one call, the rare combined schema-then-IBAN failure adds two.
- The ZUGFeRD path is untouched by construction: the feature lives entirely
  in the AI orchestration (`ai/openrouter.py`), and `model.py` keeps its
  warn-only semantics.
- A redo may regress a previously good field or "fix" the IBAN by returning
  null — both accepted (decision 4).
- A wrong IBAN that happens to pass the mod-97 check stays undetectable, as
  before.
- `docs/invoice-format.md` is unchanged; there is no config/CLI knob.
