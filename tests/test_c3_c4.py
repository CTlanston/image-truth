"""G1 unit tests for C3 (location) / C4 (caption) vision checks.

Live-API tests run on a small fixture sample (results disk-cached after the
first run); they skip cleanly when no ANTHROPIC_API_KEY is available.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_truth.checks import c3_location, c4_caption
from image_truth.model import FAIL, PASS, UNSURE, Entry
from image_truth.vision import VisionClient

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TRUTH = FIXTURES / "truth.json"

pytestmark = pytest.mark.skipif(
    not TRUTH.exists(), reason="fixtures not built — run fixtures/make_fixtures.py"
)

_client = VisionClient()
needs_vision = pytest.mark.skipif(
    not _client.available, reason="no ANTHROPIC_API_KEY / anthropic SDK"
)


def _truth():
    return json.loads(TRUTH.read_text())


def _entry(spec):
    e = Entry(**spec)
    e.local_path = str(FIXTURES / e.image)
    return e


def _sample(cat, n):
    return [c for c in _truth()["cases"] if c["category"] == cat][:n]


@needs_vision
def test_c3_flags_location_mismatches():
    wrong = []
    for c in _sample("location_mismatch", 4):
        r = c3_location.run_one(_entry(c["entries"][0]), _client)
        if r.status not in (FAIL, UNSURE):
            wrong.append((c["id"], r.status, r.reason))
    assert not wrong, f"mismatches not flagged: {wrong}"


@needs_vision
def test_c3_passes_clean_locations():
    wrong = []
    for c in _sample("clean", 4):
        r = c3_location.run_one(_entry(c["entries"][0]), _client)
        if r.status != PASS:
            wrong.append((c["id"], r.status, r.reason))
    assert not wrong, f"clean locations rejected: {wrong}"


@needs_vision
def test_c4_flags_caption_mismatches():
    wrong = []
    for c in _sample("caption_mismatch", 4):
        r = c4_caption.run_one(_entry(c["entries"][0]), _client)
        if r.status not in (FAIL, UNSURE):
            wrong.append((c["id"], r.status, r.reason))
    assert not wrong, f"caption mismatches not flagged: {wrong}"


@needs_vision
def test_c4_passes_clean_captions():
    wrong = []
    for c in _sample("clean", 3):
        r = c4_caption.run_one(_entry(c["entries"][0]), _client)
        if r.status != PASS:
            wrong.append((c["id"], r.status, r.reason))
    assert not wrong, f"clean captions rejected: {wrong}"


@needs_vision
def test_vision_cache_hit_is_deterministic():
    c = _sample("clean", 1)[0]
    e = _entry(c["entries"][0])
    r1 = c3_location.run_one(e, _client)
    r2 = c3_location.run_one(e, _client)
    assert r2.details["cached"] is True
    assert (r1.status, r1.confidence, r1.reason) == (r2.status, r2.confidence, r2.reason)


def test_no_key_degrades_to_unverified(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env to fall back to
    client = VisionClient()
    c = _sample("clean", 1)[0]
    e = _entry(c["entries"][0])
    r = c3_location.run_one(e, client)
    assert r.status == "UNVERIFIED"
    assert "ANTHROPIC_API_KEY" in r.reason


def test_empty_claims_pass_without_api():
    client = VisionClient()
    e = Entry(image="x.jpg")
    e.local_path = "/nonexistent"
    assert c3_location.run_one(e, client).status == PASS
    assert c4_caption.run_one(e, client).status == PASS
