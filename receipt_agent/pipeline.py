"""Orchestration: folder of images -> validated, scored rows -> CSV + JSONL.

Extraction is I/O-bound (network), so images are processed in a bounded thread
pool. Validation and scoring are pure/cheap and run inline."""
from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from . import config
from .confidence import score_receipt
from .extract import extract
from .validate import Check, validate

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

CSV_COLUMNS = [
    "file", "tier", "confidence", "flags", "legibility",
    "vendor", "date", "currency", "subtotal", "tax", "tax_mode",
    "service_charge", "discount", "rounding_adjustment", "total",
    "n_line_items", "notes",
]


def list_images(images_dir: Path) -> list[Path]:
    return sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)


def process_one(path: Path, use_cache: bool = True) -> dict:
    """Extract + validate + score a single image into a result record."""
    try:
        rec = extract(path, use_cache=use_cache)
        error = None
    except Exception as e:  # noqa: BLE001 - one bad image shouldn't kill the run
        rec, error = {}, str(e)

    if error:
        return {
            "file": path.name, "error": error, "extraction": {},
            "checks": {}, "confidence": 0.0, "tier": config.TIER_FAILED,
            "flags": ["EXTRACTION_ERROR"], "base_score": 0.0, "legibility": None,
        }

    checks = validate(rec)
    scored = score_receipt(rec, checks)
    return {
        "file": path.name,
        "error": None,
        "extraction": rec,
        "checks": {k: asdict(v) for k, v in checks.items()},
        **scored,
    }


def run(images_dir: Path | None = None, use_cache: bool = True,
        progress_callback=None, quiet: bool = False) -> list[dict]:
    """Process every image. `progress_callback(done, total, result)` is invoked
    after each receipt (used by the Streamlit Run button); set quiet=True to
    suppress the per-row stdout lines."""
    images_dir = images_dir or config.IMAGES_DIR
    images = list_images(images_dir)
    if not images:
        raise SystemExit(f"No images found in {images_dir}")

    total = len(images)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY) as pool:
        futures = {pool.submit(process_one, p, use_cache): p for p in images}
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            results.append(res)
            if not quiet:
                print(f"  [{i}/{total}] {res['file']:<22} "
                      f"{res['tier']:<11} conf={res['confidence']:.2f} "
                      f"{','.join(res['flags']) or '-'}")
            if progress_callback is not None:
                progress_callback(i, total, res)

    # Worst-first: terminal statuses (FAILED/UNREADABLE), then ascending
    # confidence — the rows a human must deal with float to the top.
    results.sort(key=lambda r: (config.TIER_ORDER.get(r["tier"], 2), r["confidence"], r["file"]))
    return results


def write_outputs(results: list[dict], out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "results.csv"
    jsonl_path = out_dir / "results.jsonl"

    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in results:
            ext = r.get("extraction", {})
            writer.writerow({
                "file": r["file"],
                "tier": r["tier"],
                "confidence": r["confidence"],
                "flags": "|".join(r["flags"]),
                "legibility": r.get("legibility"),
                "vendor": ext.get("vendor"),
                "date": ext.get("date"),
                "currency": ext.get("currency"),
                "subtotal": ext.get("subtotal"),
                "tax": ext.get("tax"),
                "tax_mode": ext.get("tax_mode"),
                "service_charge": ext.get("service_charge"),
                "discount": ext.get("discount"),
                "rounding_adjustment": ext.get("rounding_adjustment"),
                "total": ext.get("total"),
                "n_line_items": len(ext.get("line_items") or []),
                "notes": ext.get("notes"),
            })

    with jsonl_path.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    return csv_path, jsonl_path
