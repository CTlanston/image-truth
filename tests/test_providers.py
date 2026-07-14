"""Offline tests for the multi-provider vision layer (no network)."""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_truth import vision
from image_truth.vision import (
    PROVIDERS, VisionClient, _extract_verdict_json, resolve_provider,
)


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    for _, key_env, _, _ in PROVIDERS.values():
        monkeypatch.delenv(key_env, raising=False)
    monkeypatch.delenv("IMAGE_TRUTH_PROVIDER", raising=False)
    monkeypatch.delenv("IMAGE_TRUTH_VISION_MODEL", raising=False)
    monkeypatch.delenv("IMAGE_TRUTH_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no repo .env
    return monkeypatch


# ------------------------------------------------------ provider resolution

def test_explicit_provider_wins(clean_env):
    clean_env.setenv("GEMINI_API_KEY", "x")
    assert resolve_provider("dashscope") == "dashscope"


def test_env_provider(clean_env):
    clean_env.setenv("IMAGE_TRUTH_PROVIDER", "ark")
    assert resolve_provider() == "ark"


def test_autodetect_order(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "a")
    assert resolve_provider() == "anthropic"
    clean_env.setenv("DASHSCOPE_API_KEY", "d")
    assert resolve_provider() == "dashscope"
    clean_env.setenv("GEMINI_API_KEY", "g")
    assert resolve_provider() == "gemini"


def test_unknown_provider_raises(clean_env):
    with pytest.raises(ValueError, match="unknown provider"):
        resolve_provider("hal9000")


def test_default_models_per_provider(clean_env):
    clean_env.setenv("DASHSCOPE_API_KEY", "d")
    assert VisionClient(provider="dashscope").model == "qwen3-vl-flash"
    clean_env.setenv("GEMINI_API_KEY", "g")
    assert VisionClient(provider="gemini").model == "gemini-2.5-flash-lite"
    assert VisionClient(provider="gemini", model="claude-haiku-4-5").model == "claude-haiku-4-5"


def test_openai_style_available_without_sdk(clean_env):
    clean_env.setenv("GEMINI_API_KEY", "g")
    assert VisionClient(provider="gemini").available is True
    assert VisionClient(provider="dashscope").available is False  # no key


def test_base_url_override(clean_env):
    clean_env.setenv("DASHSCOPE_API_KEY", "d")
    clean_env.setenv("IMAGE_TRUTH_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    assert "intl" in VisionClient(provider="dashscope").base_url


# ------------------------------------------------------ JSON extraction

def test_extract_plain_json():
    assert _extract_verdict_json('{"answer": "yes", "confidence": 0.9, "reason": "ok"}')["answer"] == "yes"


def test_extract_fenced_json():
    text = 'Sure! ```json\n{"answer": "no", "confidence": 0.8, "reason": "wrong place"}\n```'
    assert _extract_verdict_json(text)["answer"] == "no"


def test_extract_prose_wrapped():
    text = 'The verdict is {"answer": "unsure", "confidence": 0.4, "reason": "too generic"} hope that helps'
    assert _extract_verdict_json(text)["answer"] == "unsure"


def test_extract_garbage_is_none():
    assert _extract_verdict_json("I think it looks fine!") is None


# ------------------------------------------------------ openai-compatible path

def _img(tmp_path):
    p = tmp_path / "x.jpg"
    Image.new("RGB", (600, 400), (10, 90, 30)).save(p)
    return str(p)


def _resp(content, pt=1000, ct=50):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
    }


def test_openai_path_parses_and_caches(clean_env, tmp_path):
    clean_env.setenv("DASHSCOPE_API_KEY", "k")
    client = VisionClient(provider="dashscope", cache_dir=str(tmp_path / "cache"))
    calls = []

    def fake(body):
        calls.append(body)
        return _resp('{"answer": "no", "confidence": 0.91, "reason": "different bridge"}')

    client._openai_request = fake
    out = client.ask(_img(tmp_path), "c3", "is this X?")
    assert out == {"answer": "no", "confidence": 0.91, "reason": "different bridge", "cached": False}
    assert client.calls_made == 1 and client.tokens_in == 1000 and client.tokens_out == 50
    assert calls[0]["model"] == "qwen3-vl-flash"
    assert calls[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # second ask: served from cache, no new transport call
    out2 = client.ask(_img(tmp_path), "c3", "is this X?")
    assert out2["cached"] is True and len(calls) == 1


def test_openai_path_bad_answer_normalized(clean_env, tmp_path):
    clean_env.setenv("GEMINI_API_KEY", "k")
    client = VisionClient(provider="gemini", cache_dir=str(tmp_path / "cache"))
    client._openai_request = lambda body: _resp('{"answer": "maybe?", "confidence": 3, "reason": "eh"}')
    out = client.ask(_img(tmp_path), "c4", "caption ok?")
    assert out["answer"] == "unsure" and out["confidence"] == 1.0


def test_openai_path_retries_unparseable_then_succeeds(clean_env, tmp_path):
    clean_env.setenv("ARK_API_KEY", "k")
    client = VisionClient(provider="ark", cache_dir=str(tmp_path / "cache"))
    replies = [_resp("It looks wrong to me."), _resp('{"answer": "no", "confidence": 0.7, "reason": "r"}')]
    client._openai_request = lambda body: replies.pop(0)
    out = client.ask(_img(tmp_path), "c3", "is this X?")
    assert out["answer"] == "no" and not replies


def test_openai_path_drops_rejected_response_format(clean_env, tmp_path):
    clean_env.setenv("ARK_API_KEY", "k")
    client = VisionClient(provider="ark", cache_dir=str(tmp_path / "cache"))
    seen = []

    def fake(body):
        seen.append("response_format" in body)
        if "response_format" in body:
            raise urllib.error.HTTPError(
                "u", 400, "bad", {}, io.BytesIO(b'{"error": "response_format not supported"}')
            )
        return _resp('{"answer": "yes", "confidence": 0.9, "reason": "ok"}')

    client._openai_request = fake
    out = client.ask(_img(tmp_path), "c3", "is this X?")
    assert out["answer"] == "yes" and seen == [True, False]


def test_anthropic_param_compat_by_model(clean_env, tmp_path):
    """Haiku 4.5 / claude-3 / sonnet-4-5 reject `effort` and `thinking` (400) —
    they must be sent only to models that support them (regression: the first
    Haiku benchmark scored 35.7% because every call 400'd)."""
    clean_env.setenv("ANTHROPIC_API_KEY", "k")

    class FakeMessages:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            block = type("B", (), {"type": "text",
                                   "text": '{"answer": "yes", "confidence": 0.9, "reason": "ok"}'})()
            usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()
            return type("R", (), {"content": [block], "usage": usage})()

    for model, expect_effort in [
        ("claude-haiku-4-5", False),
        ("claude-3-5-sonnet-latest", False),
        ("claude-sonnet-4-5", False),
        ("claude-sonnet-5", True),
    ]:
        client = VisionClient(provider="anthropic", model=model,
                              cache_dir=str(tmp_path / f"cache-{model}"))
        fake = FakeMessages()
        client._client = type("C", (), {"messages": fake})()
        out = client.ask(_img(tmp_path), "c3", "is this X?")
        assert out["answer"] == "yes"
        has_effort = "effort" in fake.kwargs["output_config"]
        has_thinking = "thinking" in fake.kwargs
        assert has_effort == has_thinking == expect_effort, model


def test_hard_api_error_raises_runtime(clean_env, tmp_path):
    clean_env.setenv("GEMINI_API_KEY", "k")
    client = VisionClient(provider="gemini", cache_dir=str(tmp_path / "cache"))

    def fake(body):
        raise urllib.error.HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b"bad key"))

    client._openai_request = fake
    with pytest.raises(RuntimeError, match="gemini API error 401"):
        client.ask(_img(tmp_path), "c3", "is this X?")
