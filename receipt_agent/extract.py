"""Vision extraction via the OpenAI Responses API with structured outputs.

Responses are cached on disk keyed by (model, prompt_version, image bytes), so
re-running the pipeline during development is free and instant, and only changed
images / a bumped PROMPT_VERSION trigger fresh calls."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

from openai import OpenAI

from . import config
from .prompts import SYSTEM_PROMPT, USER_PROMPT
from .schema import RECEIPT_SCHEMA, SCHEMA_NAME

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()  # reads OPENAI_API_KEY from env
    return _client


def _cache_key(image_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(config.MODEL.encode())
    h.update(config.PROMPT_VERSION.encode())
    h.update(image_bytes)
    return h.hexdigest()


def _data_url(image_bytes: bytes, suffix: str) -> str:
    mime = "image/png" if suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:{mime};base64,{b64}"


def _call_api(image_bytes: bytes, suffix: str) -> dict:
    client = _get_client()
    last_err: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = client.responses.create(
                model=config.MODEL,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": USER_PROMPT},
                            {"type": "input_image", "image_url": _data_url(image_bytes, suffix)},
                        ],
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": SCHEMA_NAME,
                        "strict": True,
                        "schema": RECEIPT_SCHEMA,
                    }
                },
            )
            return json.loads(resp.output_text)
        except Exception as e:  # noqa: BLE001 - retry on any transient failure
            last_err = e
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"extraction failed after {config.MAX_RETRIES} retries: {last_err}")


def extract(image_path: Path, use_cache: bool = True) -> dict:
    """Return the parsed extraction dict for one image (cached)."""
    image_bytes = image_path.read_bytes()
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = config.CACHE_DIR / f"{_cache_key(image_bytes)}.json"

    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    data = _call_api(image_bytes, image_path.suffix)
    cache_file.write_text(json.dumps(data, indent=2))
    return data
