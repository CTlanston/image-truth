"""Thin Claude vision client with a disk cache, shared by C3 and C4.

- Model: claude-sonnet-5 by default (override with IMAGE_TRUTH_VISION_MODEL).
- Responses cached in .image-truth-cache/ keyed by (image content hash, model,
  check, prompt) so re-runs without --no-cache are bit-identical.
- Degrades gracefully: no API key / no anthropic SDK -> available == False and
  callers emit UNVERIFIED verdicts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-5"
CACHE_DIR = ".image-truth-cache"
MAX_SEND_EDGE = 1200  # downscale before upload: cuts image tokens ~2-4x
CACHE_VERSION = "1"  # bump when re-encode params or the verdict schema change

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["answer", "confidence", "reason"],
    "additionalProperties": False,
}


def load_env(start: Path = None) -> None:
    """Populate os.environ from the nearest .env (repo-local secret store)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    d = (start or Path.cwd()).resolve()
    for candidate in (d, *d.parents):
        env = candidate / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            return
        if (candidate / ".git").exists():
            return  # repo boundary — don't pick up unrelated .env above


class VisionClient:
    def __init__(self, model: str = None, cache_dir: str = None, use_cache: bool = True):
        load_env()
        self.model = model or os.environ.get("IMAGE_TRUTH_VISION_MODEL", DEFAULT_MODEL)
        self.cache = Path(cache_dir or CACHE_DIR)
        self.use_cache = use_cache
        self._client = None
        self.calls_made = 0  # live (non-cached) API calls this process

    @property
    def available(self) -> bool:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False

    def _anthropic(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    @staticmethod
    def _image_payload(path: str) -> tuple:
        """(base64_jpeg, content_sha256). Re-encodes to bounded-size JPEG."""
        from PIL import Image

        raw = Path(path).read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if max(img.size) > MAX_SEND_EDGE:
            img.thumbnail((MAX_SEND_EDGE, MAX_SEND_EDGE))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode(), content_hash

    def _cache_path(self, content_hash: str, check: str, prompt: str) -> Path:
        key = hashlib.sha256(
            f"{CACHE_VERSION}|{content_hash}|{self.model}|{check}|{prompt}".encode()
        ).hexdigest()[:32]
        return self.cache / f"{check}-{key}.json"

    def ask(self, image_path: str, check: str, prompt: str) -> dict:
        """Structured yes/no/unsure verdict for one image + question.

        Returns {"answer", "confidence", "reason", "cached": bool}.
        Raises VisionUnavailable if no key/SDK; RuntimeError on API failure.
        """
        if not self.available:
            raise VisionUnavailable(
                "vision checks need ANTHROPIC_API_KEY (env or .env) and `pip install anthropic`"
            )
        b64, content_hash = self._image_payload(image_path)
        cpath = self._cache_path(content_hash, check, prompt)
        if self.use_cache and cpath.exists():
            try:
                out = json.loads(cpath.read_text())
                out["cached"] = True
                return out
            except ValueError:
                pass  # corrupt cache entry (crashed run) — refetch

        client = self._anthropic()
        response = client.messages.create(
            model=self.model,
            max_tokens=400,
            thinking={"type": "disabled"},  # cheap deterministic-ish checks
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
            },
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        self.calls_made += 1
        text = next(b.text for b in response.content if b.type == "text")
        out = json.loads(text)
        out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
        self.cache.mkdir(exist_ok=True)
        cpath.write_text(json.dumps(out))
        out["cached"] = False
        return out


class VisionUnavailable(RuntimeError):
    pass
