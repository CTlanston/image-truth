"""Vision client with pluggable providers and a disk cache, shared by C3/C4.

Providers (pick with --provider / IMAGE_TRUTH_PROVIDER, or auto-detected from
whichever API key is configured):

  gemini     Google gemini-2.5-flash-lite   GEMINI_API_KEY      (overseas default)
  dashscope  Alibaba qwen3-vl-flash         DASHSCOPE_API_KEY   (China)
  ark        ByteDance doubao seed vision   ARK_API_KEY         (China)
  anthropic  claude-sonnet-5 / haiku-4-5    ANTHROPIC_API_KEY   (quality upgrade)

gemini/dashscope/ark speak the OpenAI-compatible chat/completions protocol via
stdlib urllib — no extra dependency. anthropic uses the anthropic SDK with
enforced JSON schema. Responses are cached in .image-truth-cache/ keyed by
(image content hash, model, check, prompt) so re-runs without --no-cache are
bit-identical. Degrades gracefully: no key -> available == False and callers
emit UNVERIFIED verdicts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_DIR = ".image-truth-cache"
MAX_SEND_EDGE = 1200  # downscale before upload: cuts image tokens ~2-4x
CACHE_VERSION = "1"  # bump when re-encode params or the verdict schema change

# provider -> (base_url, key_env, default_model, style)
PROVIDERS = {
    "anthropic": (None, "ANTHROPIC_API_KEY", "claude-sonnet-5", "anthropic"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY", "gemini-2.5-flash-lite", "openai",
    ),
    "dashscope": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY", "qwen3-vl-flash", "openai",
    ),
    "ark": (
        "https://ark.cn-beijing.volces.com/api/v3",
        "ARK_API_KEY", "doubao-seed-1-6-vision-250815", "openai",
    ),
}
# auto-detect order when no provider is named: first configured key wins
DETECT_ORDER = ("gemini", "dashscope", "ark", "anthropic")

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

JSON_INSTRUCTION = (
    '\n\nReply with ONLY a JSON object, no prose around it: '
    '{"answer": "yes"|"no"|"unsure", "confidence": <0..1>, "reason": "<one sentence>"}'
)

RETRY_DELAYS = (2, 6)  # seconds, for 429/5xx/network errors


def load_env(start: Path = None) -> None:
    """Populate os.environ from the nearest .env (repo-local secret store)."""
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


def resolve_provider(name: str = None) -> str:
    """Explicit name (arg or IMAGE_TRUTH_PROVIDER) wins; else first configured key."""
    name = name or os.environ.get("IMAGE_TRUTH_PROVIDER")
    if name:
        name = name.lower()
        if name not in PROVIDERS:
            raise ValueError(
                f"unknown provider '{name}' (choose from: {', '.join(PROVIDERS)})"
            )
        return name
    for p in DETECT_ORDER:
        if os.environ.get(PROVIDERS[p][1]):
            return p
    return "anthropic"  # nothing configured — callers see available == False


class VisionUnavailable(RuntimeError):
    pass


class VisionClient:
    def __init__(
        self,
        model: str = None,
        cache_dir: str = None,
        use_cache: bool = True,
        provider: str = None,
    ):
        load_env()
        self.provider = resolve_provider(provider)
        base_url, key_env, default_model, style = PROVIDERS[self.provider]
        # e.g. DashScope International: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
        self.base_url = os.environ.get("IMAGE_TRUTH_BASE_URL") or base_url
        self.key_env = key_env
        self.style = style
        self.model = model or os.environ.get("IMAGE_TRUTH_VISION_MODEL") or default_model
        self.cache = Path(cache_dir or CACHE_DIR)
        self.use_cache = use_cache
        self._client = None
        self._stats_lock = threading.Lock()  # counters are updated from worker threads
        self.calls_made = 0        # live (non-cached) API calls this process
        self.live_seconds = 0.0    # wall time spent in live calls
        self.tokens_in = 0         # actual usage reported by the API (live calls)
        self.tokens_out = 0

    @property
    def available(self) -> bool:
        if not os.environ.get(self.key_env):
            return False
        if self.style == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return False
        return True

    # ------------------------------------------------------------- payload

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

    # ------------------------------------------------------------- backends

    def _ask_anthropic(self, b64: str, prompt: str) -> dict:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=400,
            thinking={"type": "disabled"},  # cheap deterministic-ish checks
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            with self._stats_lock:
                self.tokens_in += getattr(usage, "input_tokens", 0) or 0
                self.tokens_out += getattr(usage, "output_tokens", 0) or 0
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    def _openai_request(self, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ[self.key_env]}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())

    def _ask_openai_compatible(self, b64: str, prompt: str) -> dict:
        body = {
            "model": self.model,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt + JSON_INSTRUCTION},
                ],
            }],
        }
        last_exc = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                data = self._openai_request(body)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                # some providers reject response_format on vision models — drop it once
                if exc.code == 400 and "response_format" in detail and "response_format" in body:
                    body.pop("response_format")
                    continue
                if exc.code in (429, 500, 502, 503, 529) and attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    last_exc = exc
                    continue
                raise RuntimeError(f"{self.provider} API error {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    last_exc = exc
                    continue
                raise RuntimeError(f"{self.provider} network error: {exc}") from exc
            usage = data.get("usage") or {}
            with self._stats_lock:
                self.tokens_in += usage.get("prompt_tokens", 0) or 0
                self.tokens_out += usage.get("completion_tokens", 0) or 0
            text = (data["choices"][0]["message"].get("content") or "").strip()
            parsed = _extract_verdict_json(text)
            if parsed is not None:
                return parsed
            # unparseable reply: retry once with a sterner instruction
            if attempt < len(RETRY_DELAYS):
                body["messages"][0]["content"][1]["text"] = (
                    prompt + JSON_INSTRUCTION + "\nYour previous reply was not valid JSON. JSON only."
                )
                continue
            raise RuntimeError(f"{self.provider} returned unparseable verdict: {text[:200]}")
        raise RuntimeError(f"{self.provider} API failed after retries: {last_exc}")

    # ---------------------------------------------------------------- ask

    def ask(self, image_path: str, check: str, prompt: str) -> dict:
        """Structured yes/no/unsure verdict for one image + question.

        Returns {"answer", "confidence", "reason", "cached": bool}.
        Raises VisionUnavailable if no key/SDK; RuntimeError on API failure.
        """
        if not self.available:
            raise VisionUnavailable(
                f"vision checks need {self.key_env} (env or .env)"
                + (" and `pip install anthropic`" if self.style == "anthropic" else "")
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

        t0 = time.monotonic()
        if self.style == "anthropic":
            out = self._ask_anthropic(b64, prompt)
        else:
            out = self._ask_openai_compatible(b64, prompt)
        with self._stats_lock:
            self.live_seconds += time.monotonic() - t0
            self.calls_made += 1

        answer = str(out.get("answer", "")).lower().strip()
        conf = out.get("confidence")
        try:
            conf = 0.5 if conf is None else float(conf)
        except (TypeError, ValueError):
            conf = 0.5
        out = {
            "answer": answer if answer in ("yes", "no", "unsure") else "unsure",
            "confidence": max(0.0, min(1.0, conf)),
            "reason": str(out.get("reason", ""))[:500],
        }
        self.cache.mkdir(exist_ok=True)
        cpath.write_text(json.dumps(out))
        out["cached"] = False
        return out


def _extract_verdict_json(text: str):
    """Parse a verdict object out of a possibly-decorated model reply."""
    for candidate in (text, *re.findall(r"\{[^{}]*\}", text, re.DOTALL)):
        candidate = candidate.strip().strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict) and "answer" in obj:
            return obj
    return None
