"""Deterministic, model-independent validation.

Each check returns a `Check(status, detail)` where status is "pass", "fail", or
"na" (not applicable — the receipt didn't print what the check needs, so it is
excluded from scoring rather than counted as a failure).

The arithmetic is tax-mode aware: Malaysian receipts are frequently GST-INCLUSIVE
(tax embedded in the total), so a naive `subtotal + tax == total` would flag
correct receipts. We reconcile against both the additive and inclusive models
plus service charge / discount / rounding, and pass if any candidate fits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import config


@dataclass
class Check:
    status: str            # "pass" | "fail" | "na"
    detail: Optional[str] = None


# Active ISO 4217 alphabetic currency codes (the common set; enough to tell a
# plausible code from noise like "RM", "GST", or a misread). Not exhaustive —
# unknown-but-real codes simply flag for a human, which is the safe direction.
ISO_4217 = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
    "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
    "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
    "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF", "IDR", "ILS",
    "INR", "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR",
    "KMF", "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD",
    "LSL", "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU",
    "MUR", "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK",
    "NPR", "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG",
    "QAR", "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK",
    "SGD", "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SYP", "SZL", "THB",
    "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
    "USD", "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF",
    "XPF", "YER", "ZAR", "ZMW", "ZWL",
})


def _num(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _close_items(a: float, b: float) -> bool:
    return abs(a - b) <= max(config.ITEMS_ABS_TOL, config.ITEMS_REL_TOL * max(abs(a), abs(b)))


def _recon_tol(rec: dict) -> float:
    """Tight absolute band for the precise total check. Add a one-step rounding
    grace only when the receipt didn't print a rounding adjustment we could net
    out — never a relative band (which gets dangerously wide on large totals)."""
    tol = config.RECON_ABS_TOL
    if _num(rec.get("rounding_adjustment")) is None:
        tol += config.RECON_ROUNDING_GRACE
    return tol


def _line_amounts(rec: dict) -> list[float]:
    out = []
    for li in rec.get("line_items") or []:
        a = _num(li.get("amount"))
        if a is not None:
            out.append(a)
    return out


def check_items_sum(rec: dict) -> Check:
    """Do the line-item amounts add up to the subtotal (or the total adjusted for
    tax mode)? Forgiving by design — its job is to catch gross transcription
    errors, while total reconciliation does the precise arithmetic."""
    amounts = _line_amounts(rec)
    if not amounts:
        return Check("na", "no line items")
    s = round(sum(amounts), 2)

    sub = _num(rec.get("subtotal"))
    total = _num(rec.get("total"))
    tax = _num(rec.get("tax")) or 0.0
    svc = _num(rec.get("service_charge")) or 0.0
    disc = _num(rec.get("discount")) or 0.0
    rnd = _num(rec.get("rounding_adjustment")) or 0.0

    # The line-item base can be framed many ways: amounts may be printed tax- and
    # service-inclusive or not, and the "subtotal" line may be the ex- or inc-tax
    # figure. Recover every plausible base by backing tax/service out of the total
    # (e.g. a restaurant total 53 = items 46 + service 4 + GST 3 -> base 46).
    # Forgiving by design — this is the gross-error guard; total_reconciles does
    # the precise arithmetic.
    targets: list[float] = []
    if sub is not None:
        targets += [sub, sub - tax]
    if total is not None:
        for use_tax in (0.0, tax):
            for use_svc in (0.0, svc):
                targets.append(total - use_tax - use_svc + disc - rnd)
    if not targets:
        return Check("na", f"sum={s}, nothing to compare")

    ok = any(_close_items(s, t) for t in targets)
    return Check("pass" if ok else "fail",
                 f"sum={s} vs {[round(t, 2) for t in targets]}")


def check_total_reconciles(rec: dict) -> Check:
    """Does the total reconcile under either the additive or inclusive tax model?

    Candidates are built from subtotal and/or summed line items, combined with
    tax / service / discount / rounding the way real receipts compose them.
    Pass if the closest candidate is within tolerance of the printed total."""
    total = _num(rec.get("total"))
    if total is None:
        return Check("na", "no total")

    sub = _num(rec.get("subtotal"))
    tax = _num(rec.get("tax")) or 0.0
    svc = _num(rec.get("service_charge")) or 0.0
    disc = _num(rec.get("discount")) or 0.0
    rnd = _num(rec.get("rounding_adjustment")) or 0.0
    items = _line_amounts(rec)
    named_bases: list[tuple[str, float]] = []
    if sub is not None:
        named_bases.append(("subtotal", sub))
    if items:
        named_bases.append(("items", round(sum(items), 2)))
    if not named_bases:
        return Check("na", "only total present")

    # Build labelled candidates that cross the two genuine receipt ambiguities,
    # WITHOUT widening the band: tax may be added (exclusive) or already embedded
    # (inclusive); a printed "discount" may be netted out or merely informational
    # ("you saved 0.20"). Labelling says WHICH formula matched, so a coincidental
    # fit is easy to spot on review.
    candidates: list[tuple[str, float]] = []
    for base_name, base in named_bases:
        for use_tax, tax_lbl in ((tax, "+tax"), (0.0, "")):
            for use_disc, disc_lbl in ((disc, "-disc"), (0.0, "")):
                label = f"{base_name}{tax_lbl}+svc{disc_lbl}+rnd"
                candidates.append((label, base + use_tax + svc - use_disc + rnd))

    label, value = min(candidates, key=lambda c: abs(c[1] - total))
    residual = round(total - value, 2)
    tol = _recon_tol(rec)
    status = "pass" if abs(residual) <= tol else "fail"
    return Check(status, f"total={total} ≈ {label}={round(value, 2)} "
                         f"residual={residual:+.2f} tol=±{tol:.2f}")


def check_currency(rec: dict) -> Check:
    """Is the currency a plausible ISO 4217 code? (The brief: 'does the currency
    make sense?') Null is handled as completeness/flag, not a failure here."""
    cur = rec.get("currency")
    if cur in (None, ""):
        return Check("na", "no currency")
    code = str(cur).strip().upper()
    if code in ISO_4217:
        return Check("pass", code)
    return Check("fail", f"not a valid ISO-4217 code: {cur!r}")


def check_date_sane(rec: dict) -> Check:
    d = rec.get("date")
    if not d:
        return Check("na", "no date")  # missing date is handled by completeness
    try:
        y, m, dd = (int(p) for p in str(d).split("-"))
        parsed = date(y, m, dd)
    except Exception:  # noqa: BLE001
        return Check("fail", f"unparseable: {d}")
    if parsed.year < 2010 or parsed > date.today():
        return Check("fail", f"out of range: {d}")
    return Check("pass", d)


REQUIRED_FIELDS = ("vendor", "date", "total")


def completeness(rec: dict) -> float:
    present = sum(1 for f in REQUIRED_FIELDS if rec.get(f) not in (None, ""))
    return present / len(REQUIRED_FIELDS)


def validate(rec: dict) -> dict[str, Check]:
    return {
        "total_reconciles": check_total_reconciles(rec),
        "items_sum_ok": check_items_sum(rec),
        "date_sane": check_date_sane(rec),
        "currency_valid": check_currency(rec),
    }
