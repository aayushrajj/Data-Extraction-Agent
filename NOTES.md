# Notes

Short companion to the [README](README.md) — the *why* and the *what-next*, not
the *how*.

## Approach in one paragraph
A vision model (OpenAI Responses API + strict structured outputs) transcribes each
receipt to a typed schema, returning `null` rather than guessing. Deterministic,
model-independent code then validates that output — does the total reconcile under
either tax framing, do line items sum, is the date/currency sane — and a confidence
score combines those checks (primary) with the model's legibility self-report
(secondary), with hard caps so a single serious failure can't produce a false HIGH.
Output is a confidence-sorted CSV for human triage plus a Streamlit review UI. The
guiding principle is **calibration over coverage**: it's fine to flag a receipt for
review, never fine to confidently emit a wrong value.

## What we'd do differently for production
- **Benchmark models on cost vs accuracy.** We moved `nano → gpt-5.4` on observed
  misreads (flagged rows 15 → 2), but that was eyeballed, not measured. Production
  wants a labelled set and a harness scoring field-level accuracy against $/receipt
  and latency across candidate models — then pick per-field or tiered (cheap model
  first, escalate only low-confidence rows).
- **Ground-truth calibration.** SROIE ships entity labels; wire them in to measure
  whether HIGH rows are actually correct (precision of the confidence score), and
  tune thresholds/weights from data instead of judgement.
- **Robustness:** idempotent batch runs over a queue, structured logging + metrics,
  per-field confidence from the model, image pre-processing (deskew/denoise) for the
  worst scans, and a human-in-the-loop feedback path that feeds corrections back.
- **Schema breadth:** multi-currency, multi-tax-line, and non-Malaysian receipt
  formats; today's tolerances assume cash rounding to 0.05.

## Tradeoffs made for the ~2-hour budget
- **Vision end-to-end, not OCR+LLM** — fewer moving parts, no OCR to tune.
- **Single model, no escalation tier** — hard receipts are flagged, not re-run.
- **Self-validation, not ground-truth** — no labels wired in yet, so "calibration"
  is internal-consistency, not measured precision.
- **Heuristic weights/caps** — chosen by judgement and sanity-checked on the 45,
  not learned.
- **Disk response cache** — keeps iteration free/instant; not a production cache.
