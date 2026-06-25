# Prompts log

A curated, readable record of (a) the key instructions that shaped this project
and (b) the agent's own extraction prompt and how it evolves. Maintained as we
build, not reconstructed after.

For the full, unabridged session see [`transcript.md`](transcript.md)
(regenerate with `python scripts/export_transcript.py`).

---

## Part A — Key human prompts

### 1. Kickoff (2026-06-25)
> Build a small agent that extracts structured data from ~45 messy retail
> receipt images and validates its own output. Use OpenAI (Responses API +
> structured outputs). Vision model end-to-end (not OCR+LLM). Typed schema:
> vendor, date, line items, subtotal, tax, total, currency. Return null instead
> of guessing. Deterministic validation independent of the model (items→subtotal,
> subtotal+tax+rounding→total, sane currency/date). One confidence score per
> receipt — validation primary, model self-report secondary, failures capped low.
> CSV sorted by confidence with flags; Streamlit review UI later. ~2-hour budget.
> **Critique the plan, recommend models, define "confidence," call out what's
> missing — look at the actual images first.**

Also requested: maintain `prompts.md`, a `transcript.md` exported from the
session log (base64 stripped), and a detailed README, in a clean folder layout.

### 2. Decisions locked (after critique)
- **Model:** keep `gpt-5.4-nano` as the default, but isolated in `config.py` so
  it's a one-line swap; confirm it runs on the first live call.
- **Escalation:** none — single model for all 45, keep it lean. Hard receipts
  are flagged for human review rather than re-run on a bigger model.

### 3. Review round 2 (2026-06-25)
> Sanity-check each review point, push back where wrong, then implement what we
> agree on; show before/after on the real 45-receipt run for every logic change.

Agreed and shipped:
- **Model `gpt-5.4-nano` → `gpt-5.4`.** Several flagged rows were the model
  *misreading* receipts, not scoring bugs. Frontier model: flagged rows 15 → 3.
  Benchmark-models task parked in [`../NOTES.md`](../NOTES.md).
- **Currency ISO-4217 check** added (the brief's "does the currency make sense?"),
  small weight since the sample is single-currency.
- **Tightened reconciliation tolerance** from a 2% relative band (±5+ on a 269
  total — a wrong total could coincidentally pass) to a tight absolute ±0.05
  (+0.05 grace when no rounding line printed); records which formula matched and
  the residual; added an adversarial corrupt-the-total test. Also crosses
  tax-inclusive/exclusive and discount-applied/informational so correct receipts
  don't false-flag.
- **`items_sum` demoted to advisory** when the total reconciles — a single
  misread line item no longer sinks an otherwise-correct row.
- **Terminal tiers** `UNREADABLE` (no total + low legibility) and `FAILED`
  (extraction raised) split "couldn't read it" from "double-check it".

Net on the real run: HIGH 30 → 43, flagged 15 → 2 (both remaining are genuine
math mismatches). The agent's extraction prompt itself was unchanged this round
(still v2) — these were validation/scoring and model changes.

### Critique outcomes that changed the build
- **The sample is the SROIE dataset** (Malaysian receipts) and lives flat in
  `sample/images/`, not `sample/images/S4`.
- **`subtotal + tax == total` is wrong for most of these** — many are
  GST-*inclusive* (tax embedded in the total). Validation must reconcile against
  both the additive and inclusive models, plus service charge / discount /
  rounding. This is the single biggest correctness fix.
- Added explicit `tax_mode`, `service_charge`, `discount`, `rounding_adjustment`
  fields so the deterministic checks have what they need.
- Subtotal is often absent → checks fall back to total-adjusted-for-tax.
- Currency is ~always MYR and printed as a symbol → normalize, don't weight it.
- Added response **caching** (free/instant re-runs) and bounded concurrency.

---

## Part B — The agent's extraction prompt

The live prompt is the source of truth in
[`receipt_agent/prompts.py`](../receipt_agent/prompts.py); `PROMPT_VERSION` in
`config.py` gates the cache when it changes.

### v1 (current)
System prompt establishes the model as a **transcriber, not a calculator** —
the premise the whole validation layer depends on:

- **Transcribe, never compute.** Copy printed numbers exactly; if the subtotal
  isn't printed, return `null` — don't derive it from line items.
- **Unreadable → `null`.** Many nulls on a bad scan is correct behaviour.
- **Ignore handwriting, stamps, annotations** (the sample has scrawled names and
  amounts written over receipts).
- Normalize numbers (strip `RM`, thousands separators, trailing tax codes),
  currency → ISO 4217 (`RM`→`MYR`), date → ISO 8601.
- Classify `tax_mode` as `inclusive` / `exclusive` / `unknown`.
- Capture `service_charge`, `discount`, `rounding_adjustment` when printed.
- Self-report `legibility` as `high` / `medium` / `low` (the secondary
  confidence signal).
- `notes`: one line on anything odd.

### v2 (2026-06-25) — exclude summary lines from line items
**Reason:** the first live run on all 45 receipts revealed a recurring extraction
error: the model transcribed *summary/meta* lines as line items. E.g. CONTENTO
(`X51005442388`) had its "ITEMS 50" count line and the 21.60 total both pulled in
as line items, double-counting the sum to 43.20 → math reconciliation failed →
the receipt was (correctly) dumped to REVIEW even though its real items
(11.80+4.90+1.00+3.90) sum cleanly to the 21.60 total.

Validation behaved correctly (it caught the bad extraction), so the fix belongs
in the prompt. Added **rule 11**: line_items are purchased products/services
ONLY — never subtotal/total/tax/service/rounding/change/cash/item-count/"thank
you" lines — and each printed product line is exactly one item (no splitting or
duplicating). `PROMPT_VERSION` bumped `v1`→`v2`, which invalidates the cache and
forces a clean re-extraction.

_Further evolution appended here as the prompt changes (with the reason)._
