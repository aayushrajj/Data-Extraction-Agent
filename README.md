# Receipt Extraction & Validation Agent

Extracts structured data from a folder of messy retail-receipt images and
**validates its own output**, producing a triage-ready CSV sorted by a calibrated
confidence score. Built for the Presolv360 data-extraction assignment.

The design goal is calibration: **the agent should know when it's likely wrong
and say so.** High-confidence rows are almost never wrong; near-illegible
receipts are flagged for review rather than hallucinated.


## How it works

```
sample/images/*.jpg
        │
        ▼
  extract.py        Vision model (OpenAI Responses API + structured outputs).
                    Transcribes printed values to a typed schema; returns null
                    for anything unreadable. Cached on disk by image hash.
        │
        ▼
  validate.py       Deterministic, model-independent checks:
                    • line items sum to subtotal / total (tax-mode aware)
                    • total reconciles (additive OR GST-inclusive, + service
                      charge / discount / rounding)
                    • date is sane; required fields present
        │
        ▼
  confidence.py     One score per receipt. Validation is primary; the model's
                    legibility self-report is secondary; serious failures are
                    hard-capped low. Emits a tier + human-readable flags.
        │
        ▼
  out/results.csv   One row per receipt, sorted worst-first for triage.
  out/results.jsonl Full detail (line items, per-check results).
        │
        ▼
  app/streamlit_app.py   Review UI (Table + Inspect pages, in-app Run button).
```

### Why these choices
- **Vision end-to-end, not OCR+LLM.** Simpler, no OCR dependency to tune, and the
  receipts are within a modern VLM's range.
- **The model transcribes, it does not compute.** Every validation check assumes
  numbers are copied off the receipt, not derived — otherwise the checks would be
  circular. The prompt enforces this hard.
- **Tax-mode-aware arithmetic.** The sample (SROIE, Malaysian receipts) is largely
  **GST-inclusive** — tax embedded in the total. A naive `subtotal + tax == total`
  would flag *correct* receipts and destroy calibration. We reconcile against both
  the additive and inclusive models and pass if either fits within tolerance.
- **Caching.** Responses are cached by `(model, prompt_version, image bytes)`, so
  iterating is free and instant; only changed images or a bumped `PROMPT_VERSION`
  trigger fresh calls.

---

## The confidence score

Locked design: **validation primary, model self-report secondary, failures capped.**

```
base  = weighted avg of the APPLICABLE checks (weights renormalized):
          total_reconciles 0.45 · items_sum_ok 0.20 · completeness 0.20
          · date_sane 0.10 · currency_valid 0.05
score = base × legibility_factor          (high 1.0 · medium 0.85 · low 0.60)

hard caps (the calibration teeth):
  total is null                     → score ≤ 0.30
  total present but no fit          → score ≤ 0.45
  model self-reports low legibility → score ≤ 0.50

tier: HIGH ≥0.80 · MEDIUM ≥0.60 · LOW ≥0.40 · REVIEW <0.40
terminal (override score):  UNREADABLE = no total AND low legibility
                            FAILED     = extraction raised
```

A check that doesn't apply to a receipt (e.g. no line items, no date printed) is
**dropped and the weights renormalized** — it is not scored as a failure.

Two refinements keep the score well-calibrated:
- **`total_reconciles` uses a tight absolute band** (±0.05, +0.05 grace when no
  rounding line was printed) — *not* a relative band, so a wrong total can't coast
  in as HIGH on a large receipt. It records which formula matched and the residual,
  and tries both tax framings (inclusive/exclusive) and discount-applied vs
  -informational so correct receipts don't false-flag.
- **`items_sum_ok` is advisory when the total reconciles.** A single misread line
  item shouldn't sink a row whose headline totals add up — the row stays HIGH and
  carries `ITEMS_SUM_MISMATCH(advisory)` for the reviewer.

Each row also carries explicit flags: `MATH_MISMATCH`, `ITEMS_SUM_MISMATCH`
(or `…(advisory)`), `TOTAL_NULL`, `LOW_LEGIBILITY`, `DATE_UNREADABLE`,
`NO_LINE_ITEMS`, `VENDOR_NULL`, `CURRENCY_NULL`, `CURRENCY_INVALID`, `DATE_INSANE`,
`EXTRACTION_ERROR`.

A human opens `results.csv` (already sorted worst-first) and only reviews the
non-HIGH rows. All tunables live in [`receipt_agent/config.py`](receipt_agent/config.py).

> **Calibration note.** True calibration needs ground truth. SROIE ships entity
> labels upstream; drop them in and the high-confidence rows can be measured
> against truth. Without labels we self-validate via the deterministic checks.

---

## Setup

Requires Python 3.10+ and an OpenAI API key.

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then put your key in .env
# OPENAI_API_KEY=sk-...
```

**Windows (PowerShell)**
```powershell
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env         # then edit .env and set OPENAI_API_KEY
```
If activation is blocked, allow it for the session first:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.
(Windows **cmd.exe**: activate with `.\.venv\Scripts\activate.bat`.)

### Receipt images

The sample receipts live in `sample/images/` (45 `.jpg` files). **`sample/` is
gitignored**, so a fresh clone won't contain them — drop the images into
`sample/images/` (or point `--images` / `RECEIPTS_IMAGES_DIR` at your own folder)
before running, or `python run.py` will exit with "No images found".

### Run

```bash
python run.py                 # process sample/images with caching
python run.py --no-cache      # force fresh API calls
python run.py --images path/to/other/folder
```

Outputs land in `out/results.csv` and `out/results.jsonl`. The run prints a
per-receipt line and a tier summary, e.g.:

```
[1/45] X51005568855.jpg       REVIEW  conf=0.35  MATH_MISMATCH,ITEMS_SUM_MISMATCH
...
Tier summary: {'HIGH': 43, 'REVIEW': 2}

2 of 45 rows need human review.
```

(On `gpt-5.4`. Exact counts vary run to run — vision extraction is not fully
deterministic, so near-threshold receipts can shift a tier. The flags and the
worst-first ordering are what drive triage.)

### Output artifacts (`out/`)

The `out/` folder holds **generated** results, kept separate from source and
gitignored so the repo stays clean — every run regenerates it.

- **`results.csv`** — the human triage view. One flat row per receipt (summary
  fields only), sorted worst-first. Open in Excel/Sheets, read the REVIEW/LOW
  rows, done. This is the primary deliverable.
- **`results.jsonl`** — the full machine-readable record per receipt: the nested
  `line_items` array, every per-check result (`pass`/`fail`/`na` + detail), all
  flags, and the raw extraction. A flat CSV *can't* represent the nested line
  items or per-check detail, so this is what the Streamlit inspect view loads and
  what any downstream step would consume. If you only ever want the CSV, the
  JSONL is safe to ignore (or removable in `pipeline.write_outputs`).
- **`verdicts.json`** — created by the review UI when you record human verdicts.

### Model

Default is `gpt-5.4` (set in `config.py`, override with `RECEIPTS_MODEL`). It's
isolated to one constant so swapping to any other vision-capable model is a
one-line change. Confirm your chosen model exists in your account before a full
run — the first call surfaces an auth/model error immediately.

We started on the cheaper `gpt-5.4-nano` and moved to the frontier `gpt-5.4`
after the first run showed several review-flagged rows were the model *misreading*
the receipt (wrong line items, a tax read as 6.69 instead of 0.41, an all-null
total). The switch took flagged rows from 15 → 2. A proper cost-vs-accuracy
benchmark across models is parked in [NOTES.md](NOTES.md).

### Review UI (optional)

```bash
streamlit run app/streamlit_app.py
```

A multipage app (so a row can genuinely jump to its detail — `st.tabs` can't be
switched programmatically). Header shows the title, the `extract → validate →
score → review` caption, and summary metrics on every page.

- **Table view** — every row, neutral tier/flag filters, confidence bars,
  horizontal scroll, worst-first. Select a row → **Inspect selected →** jumps to it.
- **Inspect view** — compact receipt image beside extracted fields (as a table),
  the per-check results with residuals, and a human verdict saved to
  `out/verdicts.json`.
- **Run pipeline** (header expander) — re-run from the UI with a progress bar.
  Uses the cache by default (fast, free); the API path is behind a *Force
  re-extract* checkbox and is disabled when `OPENAI_API_KEY` is absent.

---

## Tests

Offline self-test of the validation + confidence logic (no API calls), using
mock extractions modeled on real sample receipts (inclusive, exclusive,
service-charge, broken-math, illegible):

```bash
python -m tests.test_validation
```

---

## Project layout

```
receipt_agent/
  config.py       all tunables (model, tolerances, weights, caps, tiers)
  prompts.py      the agent's extraction prompt (mirrored in docs/prompts.md)
  schema.py       strict JSON schema for structured outputs
  extract.py      Responses API call + disk cache
  validate.py     deterministic, tax-mode-aware checks
  confidence.py   scoring, caps, tiers, flags
  pipeline.py     orchestration (threaded extract → validate → score → write)
run.py            CLI entrypoint
app/streamlit_app.py            multipage review UI (Table + Inspect)
.streamlit/config.toml          neutral UI theme
scripts/export_transcript.py    session log (.jsonl) → readable markdown
tests/test_validation.py        offline validation/confidence + adversarial tests
docs/
  prompts.md      curated prompt log + extraction prompt evolution
  transcript.md   full session export (base64 stripped)
NOTES.md          approach, production differences, time-budget tradeoffs
sample/images/    45 receipts
cache/  out/      generated (gitignored)
```

---

## Limitations & next steps

See [NOTES.md](NOTES.md) for the approach summary, production differences, and
time-budget tradeoffs. In brief:
- No ground-truth scoring yet (see calibration note above).
- Single model, no escalation — hard receipts are flagged, not re-extracted.
  Model cost-vs-accuracy benchmarking is the top parked task.
- Currency validation checks ISO-4217 plausibility; the sample is single-currency
  (MYR), so it's a low-information check here.
- Reconciliation tolerances are tuned for cash receipts rounded to 0.05; adjust
  `RECON_ABS_TOL` / `ITEMS_*_TOL` in `config.py` for other locales.
