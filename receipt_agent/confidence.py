"""Confidence scoring + human-readable flags.

Philosophy (locked with the user):
  - Validation is the PRIMARY signal. A weighted average of the applicable
    deterministic checks forms the base score.
  - The model's self-reported legibility is SECONDARY: it scales the base, it
    does not set it.
  - Hard caps are the calibration teeth: any single serious failure (no total,
    nothing reconciles, model says it was guessing) forces the score low no
    matter how the rest looks. The goal is that a HIGH row is almost never wrong.
"""
from __future__ import annotations

from . import config
from .validate import Check, completeness


def _pass_value(check: Check):
    """pass -> 1.0, fail -> 0.0, na -> None (excluded from the weighting)."""
    return {"pass": 1.0, "fail": 0.0}.get(check.status)


def score_receipt(rec: dict, checks: dict[str, Check]) -> dict:
    total_ok = _pass_value(checks["total_reconciles"])

    components = {
        "total_reconciles": total_ok,
        "items_sum_ok": _pass_value(checks["items_sum_ok"]),
        "date_sane": _pass_value(checks["date_sane"]),
        "currency_valid": _pass_value(checks["currency_valid"]),
        "completeness": completeness(rec),  # always applicable
    }

    # items_sum is ADVISORY when the total reconciles cleanly: a single misread
    # line item shouldn't sink a row whose headline totals add up. The flag is
    # still emitted for the reviewer; it just stops contributing to the score.
    items_advisory = total_ok == 1.0
    if items_advisory:
        components["items_sum_ok"] = None

    # Weighted average over the applicable components, weights renormalized.
    num = den = 0.0
    for name, weight in config.WEIGHTS.items():
        val = components.get(name)
        if val is None:
            continue
        num += weight * val
        den += weight
    base = num / den if den else 0.0

    legibility = rec.get("legibility", "medium")
    score = base * config.LEGIBILITY_FACTOR.get(legibility, 0.85)

    # --- Hard caps -------------------------------------------------------
    if rec.get("total") is None:
        score = min(score, config.CAP_TOTAL_NULL)
    if total_ok == 0.0:  # total present but failed to reconcile
        score = min(score, config.CAP_MATH_FAIL)
    if legibility == "low":
        score = min(score, config.CAP_LOW_LEGIBILITY)

    score = round(max(0.0, min(1.0, score)), 3)

    # Terminal status overrides the score-derived tier: separate "couldn't read
    # it" from "double-check it".
    if rec.get("total") is None and legibility == "low":
        tier = config.TIER_UNREADABLE
    else:
        tier = _tier(score)

    return {
        "confidence": score,
        "tier": tier,
        "flags": _flags(rec, checks, items_advisory),
        "base_score": round(base, 3),
        "legibility": legibility,
    }


def _tier(score: float) -> str:
    for name, threshold in config.TIERS:
        if score >= threshold:
            return name
    return "REVIEW"


def _flags(rec: dict, checks: dict[str, Check], items_advisory: bool = False) -> list[str]:
    flags: list[str] = []
    if rec.get("total") is None:
        flags.append("TOTAL_NULL")
    if rec.get("vendor") is None:
        flags.append("VENDOR_NULL")
    if rec.get("date") is None:
        flags.append("DATE_UNREADABLE")
    if checks["total_reconciles"].status == "fail":
        flags.append("MATH_MISMATCH")
    if checks["items_sum_ok"].status == "fail":
        # Still surfaced for the reviewer even when demoted to advisory, but
        # tagged so it's clear it isn't dragging the score down.
        flags.append("ITEMS_SUM_MISMATCH(advisory)" if items_advisory else "ITEMS_SUM_MISMATCH")
    if not (rec.get("line_items") or []):
        flags.append("NO_LINE_ITEMS")
    if rec.get("legibility") == "low":
        flags.append("LOW_LEGIBILITY")
    if rec.get("currency") is None:
        flags.append("CURRENCY_NULL")
    if checks["currency_valid"].status == "fail":
        flags.append("CURRENCY_INVALID")
    if checks["date_sane"].status == "fail":
        flags.append("DATE_INSANE")
    return flags
