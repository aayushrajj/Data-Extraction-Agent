#!/usr/bin/env python3
"""Convert a Claude Code session log (.jsonl) into a readable markdown transcript,
stripping base64 image blobs and truncating very long tool output.

Usage:
    python scripts/export_transcript.py                 # newest session for this project
    python scripts/export_transcript.py --session FILE  # explicit .jsonl
    python scripts/export_transcript.py --out docs/transcript.md

The Claude Code session store lives at:
    ~/.claude/projects/<slugified-cwd>/<session-uuid>.jsonl
We slugify the current working directory the same way Claude Code does
(non-alphanumerics -> "-") and pick the most recently modified log.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

MAX_TEXT = 6000          # truncate any single rendered block beyond this
MAX_TOOL_RESULT = 2000   # tool results are usually noisier; trim harder
BASE64_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


def find_session() -> Path:
    cwd = Path.cwd()
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    proj_dir = Path.home() / ".claude" / "projects" / slug
    if not proj_dir.is_dir():
        raise SystemExit(f"No session dir for this project at {proj_dir}")
    logs = sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        raise SystemExit(f"No .jsonl logs in {proj_dir}")
    return logs[0]


def scrub(text: str, limit: int = MAX_TEXT) -> str:
    """Drop long base64 runs (image blobs) and cap length."""
    text = BASE64_RE.sub("[base64 omitted]", text)
    if len(text) > limit:
        text = text[:limit] + f"\n…[truncated {len(text) - limit} chars]"
    return text.rstrip()


def render_content(content) -> list[str]:
    """Turn a message `content` (str or list of blocks) into markdown lines."""
    out: list[str] = []
    if isinstance(content, str):
        return [scrub(content)] if content.strip() else []
    if not isinstance(content, list):
        return []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            txt = scrub(block.get("text", ""))
            if txt:
                out.append(txt)
        elif btype == "thinking":
            continue  # internal reasoning, not part of the shared transcript
        elif btype == "image":
            out.append("_[image omitted]_")
        elif btype == "tool_use":
            name = block.get("name", "tool")
            args = json.dumps(block.get("input", {}), ensure_ascii=False)
            out.append(f"**→ tool call: `{name}`**\n\n```json\n{scrub(args, 1500)}\n```")
        elif btype == "tool_result":
            inner = block.get("content", "")
            if isinstance(inner, list):
                parts = []
                for c in inner:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif isinstance(c, dict) and c.get("type") == "image":
                        parts.append("[image omitted]")
                inner = "\n".join(parts)
            out.append(f"**← tool result**\n\n```\n{scrub(str(inner), MAX_TOOL_RESULT)}\n```")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("docs/transcript.md"))
    args = ap.parse_args()

    session = args.session or find_session()
    lines = session.read_text().splitlines()

    md: list[str] = [
        "# Session transcript",
        "",
        f"_Source: `{session.name}` · exported {datetime.now():%Y-%m-%d %H:%M} · "
        "base64 image blobs stripped, internal reasoning omitted._",
        "",
    ]

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = evt.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or evt.get("type")
        rendered = render_content(msg.get("content"))
        if not rendered:
            continue
        label = {"user": "## 🧑 User", "assistant": "## 🤖 Assistant"}.get(role, f"## {role}")
        md.append(label)
        md.append("")
        md.extend(rendered)
        md.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(md))
    print(f"Wrote {args.out} ({len(lines)} events from {session.name})")


if __name__ == "__main__":
    main()
