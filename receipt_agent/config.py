"""Central configuration. Everything tunable lives here so the rest of the
pipeline stays declarative. Override any value via the matching env var."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- Paths -----------------------------------------------------------------
IMAGES_DIR = Path(os.getenv("RECEIPTS_IMAGES_DIR", ROOT / "sample" / "images"))
CACHE_DIR = Path(os.getenv("RECEIPTS_CACHE_DIR", ROOT / "cache"))
OUT_DIR = Path(os.getenv("RECEIPTS_OUT_DIR", ROOT / "out"))

# --- Model -----------------------------------------------------------------
# Single config-swappable constant. Confirmed against the live API on the first
# call (see extract.py). Swap to any other vision-capable model here.
MODEL = os.getenv("RECEIPTS_MODEL", "gpt-5.4")

# Bump when the prompt or schema changes — invalidates the response cache so we
# never serve stale extractions during iteration.
PROMPT_VERSION = "v2"

# --- Concurrency / retry ---------------------------------------------------
MAX_CONCURRENCY = int(os.getenv("RECEIPTS_CONCURRENCY", "6"))
MAX_RETRIES = int(os.getenv("RECEIPTS_MAX_RETRIES", "4"))

# --- Validation tolerances -------------------------------------------------
# Two different jobs, two different bands:
#
# total_reconciles is the PRECISE arithmetic check, so it gets a tight absolute
# band (no relative term — a 2% band on a large total is wide enough that a
# wrong total could pass by coincidence and become a false HIGH). Malaysian cash
# totals round to the nearest 0.05; when the receipt didn't print a rounding
# adjustment we can't subtract it, so we add a one-step rounding grace.
RECON_ABS_TOL = float(os.getenv("RECEIPTS_RECON_ABS_TOL", "0.05"))
RECON_ROUNDING_GRACE = float(os.getenv("RECEIPTS_RECON_ROUNDING_GRACE", "0.05"))

# items_sum is the FORGIVING gross-error guard (and is demoted to advisory when
# the total reconciles), so it keeps a looser absolute-or-relative band that
# absorbs per-line rounding across many items.
ITEMS_ABS_TOL = float(os.getenv("RECEIPTS_ITEMS_ABS_TOL", "0.05"))
ITEMS_REL_TOL = float(os.getenv("RECEIPTS_ITEMS_REL_TOL", "0.02"))

# --- Confidence model ------------------------------------------------------
# Validation is the primary signal; weights are renormalized over whichever
# checks are actually applicable to a given receipt. items_sum_ok is further
# demoted to advisory (weight 0, flag retained) whenever total_reconciles passes
# — a single misread line item shouldn't sink a row whose totals add up.
WEIGHTS = {
    "total_reconciles": 0.45,
    "items_sum_ok": 0.20,
    "completeness": 0.20,
    "date_sane": 0.10,
    "currency_valid": 0.05,
}

# The model's self-reported legibility is the secondary signal: it scales the
# validation-derived base score, it does not drive it.
LEGIBILITY_FACTOR = {"high": 1.0, "medium": 0.85, "low": 0.60}

# Hard caps — the calibration teeth. A row that trips one of these cannot score
# above the cap no matter how the rest looks.
CAP_TOTAL_NULL = 0.30        # no total at all -> always needs review
CAP_MATH_FAIL = 0.45         # total present but nothing reconciles to it
CAP_LOW_LEGIBILITY = 0.50    # model itself says it was largely guessing

# Score -> tier. Evaluated top-down; first threshold the score meets wins.
TIERS = [("HIGH", 0.80), ("MEDIUM", 0.60), ("LOW", 0.40), ("REVIEW", 0.0)]

# Terminal, score-independent statuses that override the tier above. They split
# "a human should double-check this" (REVIEW) from "the agent honestly couldn't
# read it" / "extraction broke" — so the CSV triage is unambiguous.
#   FAILED     — extraction raised (API/parse error); no data to score.
#   UNREADABLE — no total AND the model self-reports low legibility.
TIER_FAILED = "FAILED"
TIER_UNREADABLE = "UNREADABLE"
# Ordering for sorting/colour (higher = better). Terminal statuses sort first.
TIER_ORDER = {
    "HIGH": 5, "MEDIUM": 4, "LOW": 3, "REVIEW": 2,
    TIER_UNREADABLE: 1, TIER_FAILED: 0,
}
