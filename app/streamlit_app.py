"""Streamlit review UI for the extracted receipts.

    streamlit run app/streamlit_app.py

Multipage app (st.navigation + st.switch_page) so a row in the Table page can
genuinely jump to that receipt in the Inspect page — st.tabs can't be switched
programmatically, so tabs would never give a real click-to-jump. Shared state
(the selected receipt) lives in st.session_state.

Pages:
  - Table view   : every row, neutral tier/flag filters, horizontal scroll,
                   selectable rows -> "Inspect selected" jumps to Inspect.
  - Inspect view : compact image + extracted fields as a table, per-check
                   results, and a human verdict.
The header (title, caption, summary metrics) and a Run control render on every
page; the footer too.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from receipt_agent import config  # noqa: E402
from receipt_agent.pipeline import run as run_pipeline, write_outputs  # noqa: E402

JSONL = config.OUT_DIR / "results.jsonl"
IMAGES_DIR = config.IMAGES_DIR
VERDICTS = config.OUT_DIR / "verdicts.json"

TIER_ICON = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠",
             "REVIEW": "🔴", "UNREADABLE": "⚫", "FAILED": "✖️"}

st.set_page_config(page_title="Data Extraction Agent", page_icon="🧾", layout="wide")


# --- data ------------------------------------------------------------------
@st.cache_data
def load_results() -> list[dict]:
    if not JSONL.exists():
        return []
    return [json.loads(line) for line in JSONL.read_text().splitlines() if line.strip()]


def load_verdicts() -> dict:
    return json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}


def save_verdict(file: str, verdict: str, note: str) -> None:
    data = load_verdicts()
    data[file] = {"verdict": verdict, "note": note}
    VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    VERDICTS.write_text(json.dumps(data, indent=2))


# --- shared chrome ---------------------------------------------------------
def run_control() -> None:
    """Re-run the pipeline from the UI. Cached by default (fast/free); the API
    path is gated behind an explicit checkbox and a present API key."""
    with st.expander("⚙️  Run pipeline", expanded=not load_results()):
        has_key = bool(os.getenv("OPENAI_API_KEY"))
        force = st.checkbox("Force re-extract (calls the API, costs money)", value=False)
        if not has_key:
            st.warning("`OPENAI_API_KEY` not found in environment / .env — running is disabled.")
        run_clicked = st.button("▶ Run", type="primary", disabled=not has_key)
        st.caption("Default reuses the on-disk cache (no API calls). Force re-extract "
                   "ignores the cache and re-calls the model for every image.")
        if run_clicked:
            bar = st.progress(0.0, text="Starting…")

            def on_progress(done, total, res):
                bar.progress(done / total,
                             text=f"{done}/{total} · {res['file']} → {res['tier']}")

            results = run_pipeline(use_cache=not force, progress_callback=on_progress, quiet=True)
            write_outputs(results)
            bar.empty()
            load_results.clear()
            st.success(f"Done — processed {len(results)} receipts.")
            st.rerun()


def header() -> None:
    st.title("🧾 Data Extraction Agent with Validation")
    st.caption("extract → validate → score → review")
    results = load_results()
    if results:
        from collections import Counter
        c = Counter(r["tier"] for r in results)
        cols = st.columns(6)
        cols[0].metric("Total", len(results))
        for col, tier in zip(cols[1:], ["HIGH", "MEDIUM", "LOW", "REVIEW", "UNREADABLE"]):
            col.metric(f"{TIER_ICON.get(tier,'')} {tier.title()}", c.get(tier, 0))
    run_control()
    st.divider()


def footer() -> None:
    st.divider()
    st.caption("Presolv360 — AI-Native SWE Assessment · Ayush Raj")


# --- pages -----------------------------------------------------------------
def table_page() -> None:
    results = load_results()
    if not results:
        st.info("No results yet. Use **Run pipeline** above to generate them.")
        return

    rows = []
    for r in results:
        ext = r.get("extraction", {})
        rows.append({
            "file": r["file"], "tier": r["tier"], "confidence": r["confidence"],
            "vendor": ext.get("vendor"), "date": ext.get("date"),
            "total": ext.get("total"), "currency": ext.get("currency"),
            "flags": ", ".join(r.get("flags", [])),
        })
    df = pd.DataFrame(rows).sort_values(
        ["tier", "confidence"], key=lambda s: s.map(config.TIER_ORDER) if s.name == "tier" else s)

    c1, c2 = st.columns(2)
    tiers = c1.multiselect("Tier", sorted(df["tier"].unique(), key=lambda t: -config.TIER_ORDER.get(t, 0)),
                           default=list(df["tier"].unique()))
    all_flags = sorted({f for r in results for f in r.get("flags", [])})
    flag_filter = c2.multiselect("Has any of these flags", all_flags)

    view = df[df["tier"].isin(tiers)]
    if flag_filter:
        view = view[view["flags"].apply(lambda s: any(f in s for f in flag_filter))]

    st.caption(f"{len(view)} of {len(df)} receipts · worst-first · select a row, then "
               "**Inspect selected →**")
    event = st.dataframe(
        view, key="receipts_table",  # stable key: selection survives reruns
        hide_index=True, on_select="rerun", selection_mode="single-row",
        width="stretch",  # full width; wide content scrolls horizontally
        column_config={
            "confidence": st.column_config.ProgressColumn(
                "confidence", min_value=0.0, max_value=1.0, format="%.2f"),
            "flags": st.column_config.TextColumn("flags", width="large"),
        },
    )

    selected = event.selection.rows if event and event.selection else []
    if selected:
        chosen = view.iloc[selected[0]]["file"]
        st.session_state["inspect_file"] = chosen
        # Call switch_page in the main flow (NOT an on_click callback, where it
        # silently fails to navigate) so the click actually jumps to Inspect.
        if st.button(f"Inspect selected →  {chosen}", type="primary"):
            st.switch_page(INSPECT_PAGE)


def inspect_page() -> None:
    results = load_results()
    if not results:
        st.info("No results yet. Use **Run pipeline** above to generate them.")
        return

    by_file = {r["file"]: r for r in results}
    ordered = sorted(results, key=lambda r: (config.TIER_ORDER.get(r["tier"], 2), r["confidence"]))
    files = [r["file"] for r in ordered]
    default = st.session_state.get("inspect_file", files[0])
    idx = files.index(default) if default in files else 0
    chosen = st.selectbox(
        "Receipt", files, index=idx,
        format_func=lambda f: f"{TIER_ICON.get(by_file[f]['tier'],'')} {f}  "
                              f"({by_file[f]['confidence']:.2f} · {by_file[f]['tier']})")
    st.session_state["inspect_file"] = chosen
    r = by_file[chosen]
    ext = r.get("extraction", {})

    img_col, data_col = st.columns([1, 1.7])  # narrow image column
    with img_col:
        img_path = IMAGES_DIR / chosen
        if img_path.exists():
            st.image(str(img_path), width="stretch")
        else:
            st.info(f"Image not found: {img_path}")

    with data_col:
        m1, m2 = st.columns(2)
        m1.metric("Confidence", f"{r['confidence']:.2f}", f"{TIER_ICON.get(r['tier'],'')} {r['tier']}")
        m2.metric("Legibility (model)", str(r.get("legibility")))
        if r.get("flags"):
            st.write("**Flags:** " + " ".join(f"`{f}`" for f in r["flags"]))

        st.markdown("**Extracted fields**")
        field_rows = [{"field": k, "value": ext.get(k)} for k in (
            "vendor", "date", "currency", "subtotal", "tax", "tax_mode",
            "service_charge", "discount", "rounding_adjustment", "total", "notes")]
        st.dataframe(pd.DataFrame(field_rows), hide_index=True, width="stretch")

        if ext.get("line_items"):
            st.markdown("**Line items**")
            st.dataframe(pd.DataFrame(ext["line_items"]), hide_index=True, width="stretch")

        st.markdown("**Validation checks**")
        for name, chk in (r.get("checks") or {}).items():
            mark = {"pass": "✅", "fail": "❌", "na": "➖"}.get(chk["status"], "?")
            st.write(f"{mark} **{name}** — {chk.get('detail') or chk['status']}")

        st.markdown("**Human verdict**")
        existing = load_verdicts().get(chosen, {})
        opts = ["unreviewed", "correct", "needs-fix", "illegible"]
        verdict = st.radio("Is the extraction correct?", opts,
                           index=opts.index(existing.get("verdict", "unreviewed")), horizontal=True)
        note = st.text_input("Note", value=existing.get("note", ""))
        if st.button("Save verdict"):
            save_verdict(chosen, verdict, note)
            st.success("Saved to out/verdicts.json")


# --- navigation ------------------------------------------------------------
TABLE_PAGE = st.Page(table_page, title="Table view", icon="📊", url_path="table", default=True)
INSPECT_PAGE = st.Page(inspect_page, title="Inspect view", icon="🔎", url_path="inspect")

pg = st.navigation([TABLE_PAGE, INSPECT_PAGE])
header()
pg.run()
footer()
