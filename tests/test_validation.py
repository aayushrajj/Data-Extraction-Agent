"""Offline self-test of the deterministic validation + confidence logic, using
mock extractions modeled on the real sample receipts. No API calls.

Run:  python -m tests.test_validation
"""
from __future__ import annotations

from receipt_agent.confidence import score_receipt
from receipt_agent.validate import check_currency, validate

# Modeled on SIN LIANHAP (X51005230621): GST-INCLUSIVE. subtotal already includes
# tax; naive subtotal+tax would (wrongly) flag it. Should PASS.
INCLUSIVE = {
    "vendor": "SIN LIANHAP SDN BHD", "date": "2018-02-05", "currency": "MYR",
    "line_items": [{"description": "PPD 4MM DENLIME", "qty": 1, "unit_price": 2.00, "amount": 1.887},
                   {"description": "6023# GARDEN", "qty": 1, "unit_price": 5.30, "amount": 5.00}],
    "subtotal": 7.30, "tax": 0.41, "service_charge": None, "discount": None,
    "rounding_adjustment": 0.00, "total": 7.30, "tax_mode": "inclusive",
    "legibility": "medium", "notes": "crumpled thermal paper",
}

# Modeled on EVERGREEN LIGHT (X51005568885): EXCLUSIVE/additive with rounding.
# subtotal 52.84 + GST 3.17 = 56.01, rounding -0.01 -> 56.00. Should PASS.
EXCLUSIVE = {
    "vendor": "EVERGREEN LIGHT SDN BHD", "date": "2017-07-10", "currency": "MYR",
    "line_items": [{"description": "PROEMK 10W LED STICK", "qty": 4, "unit_price": 13.21, "amount": 52.84}],
    "subtotal": 52.84, "tax": 3.17, "service_charge": None, "discount": None,
    "rounding_adjustment": -0.01, "total": 56.00, "tax_mode": "exclusive",
    "legibility": "low", "notes": "faded dot-matrix",
}

# Modeled on UROKO (X51005337877): additive WITH service charge.
# 50.00 + svc 4.00 (rounded display) + GST 3.00 ~= 53.00 grand total. Should PASS.
SERVICE = {
    "vendor": "UROKO JAPANESE CUISINE", "date": "2018-03-20", "currency": "MYR",
    "line_items": [{"description": "KO NABE INANIWA UDON", "qty": 1, "unit_price": 24.0, "amount": 24.0},
                   {"description": "GREEN TEA", "qty": 1, "unit_price": 1.0, "amount": 1.0},
                   {"description": "GYOZA 5pcs", "qty": 1, "unit_price": 15.0, "amount": 15.0},
                   {"description": "GARLIC CHAHAN", "qty": 1, "unit_price": 6.0, "amount": 6.0}],
    "subtotal": 46.00, "tax": 3.00, "service_charge": 4.00, "discount": None,
    "rounding_adjustment": None, "total": 53.00, "tax_mode": "exclusive",
    "legibility": "high", "notes": None,
}

# UROKO as actually extracted: subtotal NOT captured (null), only the grand
# total + service + tax. Line items sum to 46 (the pre-tax base). items_sum must
# back service+tax out of the total to find the 46 target, else it false-flags.
SERVICE_NO_SUBTOTAL = {
    "vendor": "UROKO JAPANESE CUISINE", "date": "2018-03-20", "currency": "MYR",
    "line_items": [{"description": "KO NABE", "qty": 1, "unit_price": 24.0, "amount": 24.0},
                   {"description": "GREEN TEA", "qty": 1, "unit_price": 1.0, "amount": 1.0},
                   {"description": "GYOZA 5pcs", "qty": 1, "unit_price": 15.0, "amount": 15.0},
                   {"description": "GARLIC CHAHAN", "qty": 1, "unit_price": 6.0, "amount": 6.0}],
    "subtotal": None, "tax": 3.00, "service_charge": 4.00, "discount": 0.0,
    "rounding_adjustment": None, "total": 53.00, "tax_mode": "exclusive",
    "legibility": "medium", "notes": None,
}

# A genuinely broken extraction: total doesn't reconcile at all. Should FAIL math.
BROKEN = {
    "vendor": "SOME SHOP", "date": "2018-01-01", "currency": "MYR",
    "line_items": [{"description": "ITEM A", "qty": 1, "unit_price": 10.0, "amount": 10.0}],
    "subtotal": 10.00, "tax": 0.60, "service_charge": None, "discount": None,
    "rounding_adjustment": None, "total": 99.99, "tax_mode": "exclusive",
    "legibility": "high", "notes": None,
}

# Near-illegible: total unreadable. Should be capped low + flagged.
ILLEGIBLE = {
    "vendor": None, "date": None, "currency": "MYR",
    "line_items": [], "subtotal": None, "tax": None, "service_charge": None,
    "discount": None, "rounding_adjustment": None, "total": None,
    "tax_mode": "unknown", "legibility": "low", "notes": "barely legible",
}


def show(name, rec, expect_reconcile, conf_check):
    checks = validate(rec)
    scored = score_receipt(rec, checks)
    rec_status = checks["total_reconciles"].status
    ok = rec_status == expect_reconcile and conf_check(scored["confidence"])
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<26} "
          f"reconcile={rec_status:<5} conf={scored['confidence']:.2f} "
          f"tier={scored['tier']:<7} flags={scored['flags']}")
    assert ok, f"{name}: reconcile={rec_status} (want {expect_reconcile}), conf={scored['confidence']}"


# Clean reconciling totals, but ONE line item misread (sum 40 not 46). With
# items_sum demoted to advisory, this must stay HIGH and carry the advisory flag.
ITEM_SLIP = {
    "vendor": "SHOP", "date": "2018-03-20", "currency": "MYR",
    "line_items": [{"description": "A", "qty": 1, "unit_price": 40.0, "amount": 40.0}],
    "subtotal": 46.00, "tax": 3.00, "service_charge": 4.00, "discount": 0.0,
    "rounding_adjustment": 0.0, "total": 53.00, "tax_mode": "exclusive",
    "legibility": "high", "notes": None,
}


def adversarial_corrupt_total():
    """Take a known-good receipt, corrupt the total, assert validation now FAILS.
    Guards against a wrong total slipping through as a coincidental reconcile."""
    good = dict(SERVICE)
    assert validate(good)["total_reconciles"].status == "pass", "baseline should reconcile"
    for bad_total in (52.50, 53.50, 63.00, 5.30):  # off by rounding-band+, big, transposed
        corrupt = dict(good, total=bad_total)
        chk = validate(corrupt)["total_reconciles"]
        ok = chk.status == "fail"
        print(f"[{'PASS' if ok else 'FAIL'}] adversarial total={bad_total:<6} "
              f"-> reconcile={chk.status}  ({chk.detail})")
        assert ok, f"corrupt total {bad_total} should FAIL but got {chk.status}: {chk.detail}"


def currency_checks():
    cases = [({"currency": "MYR"}, "pass"), ({"currency": "rm"}, "fail"),
             ({"currency": "GST"}, "fail"), ({"currency": None}, "na")]
    for rec, want in cases:
        got = check_currency(rec).status
        ok = got == want
        print(f"[{'PASS' if ok else 'FAIL'}] currency {str(rec['currency']):<5} -> {got} (want {want})")
        assert ok, f"currency {rec['currency']}: got {got}, want {want}"


def main():
    show("inclusive (no over-flag)", INCLUSIVE, "pass", lambda c: c >= 0.6)
    show("exclusive + rounding", EXCLUSIVE, "pass", lambda c: c >= 0.4)  # capped by low legibility
    show("additive + service chg", SERVICE, "pass", lambda c: c >= 0.8)
    show("service chg, null subtotal", SERVICE_NO_SUBTOTAL, "pass", lambda c: c >= 0.6)
    show("item slip stays HIGH", ITEM_SLIP, "pass", lambda c: c >= 0.8)
    show("broken math", BROKEN, "fail", lambda c: c <= 0.45)
    show("illegible -> UNREADABLE", ILLEGIBLE, "na", lambda c: c <= 0.30)
    # tier-specific checks
    assert score_receipt(ILLEGIBLE, validate(ILLEGIBLE))["tier"] == "UNREADABLE"
    slip = score_receipt(ITEM_SLIP, validate(ITEM_SLIP))
    assert any("advisory" in f for f in slip["flags"]), slip["flags"]
    print()
    adversarial_corrupt_total()
    print()
    currency_checks()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
