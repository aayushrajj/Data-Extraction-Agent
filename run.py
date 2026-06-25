#!/usr/bin/env python3
"""CLI entrypoint: extract + validate + score every receipt in a folder.

Usage:
    python run.py                          # use sample/images, with cache
    python run.py --images path/to/dir     # custom folder
    python run.py --no-cache               # force fresh API calls
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # pull OPENAI_API_KEY (and any overrides) from .env

from receipt_agent import config              # noqa: E402
from receipt_agent.pipeline import run, write_outputs  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Receipt extraction + validation agent")
    parser.add_argument("--images", type=Path, default=config.IMAGES_DIR)
    parser.add_argument("--no-cache", action="store_true", help="ignore the response cache")
    args = parser.parse_args()

    print(f"Model: {config.MODEL}  |  images: {args.images}  |  cache: {not args.no_cache}")
    results = run(images_dir=args.images, use_cache=not args.no_cache)
    csv_path, jsonl_path = write_outputs(results)

    tiers = Counter(r["tier"] for r in results)
    print("\nTier summary:", dict(tiers))
    print(f"CSV : {csv_path}")
    print(f"JSON: {jsonl_path}")
    review = [r for r in results if r["tier"] in ("REVIEW", "LOW")]
    print(f"\n{len(review)} of {len(results)} rows need human review (REVIEW/LOW).")


if __name__ == "__main__":
    main()
