"""G1 unit tests for C2 (watermark) and C5 (aesthetic advisory)."""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_truth.checks import c2_watermark, c5_aesthetic
from image_truth.model import FAIL, PASS, WARN, Entry

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TRUTH = FIXTURES / "truth.json"

pytestmark = pytest.mark.skipif(
    not TRUTH.exists(), reason="fixtures not built — run fixtures/make_fixtures.py"
)


def _truth():
    return json.loads(TRUTH.read_text())


def _entry(spec):
    e = Entry(**spec)
    e.local_path = str(FIXTURES / e.image)
    return e


# ---------------------------------------------------------------------- C2

@pytest.mark.skipif(not c2_watermark.HAVE_OCR, reason="tesseract not installed")
def test_watermark_cases_detected():
    truth = _truth()
    missed = []
    for c in truth["cases"]:
        if c["category"] != "watermark":
            continue
        r = c2_watermark.run_one(_entry(c["entries"][0]))
        if r.status != FAIL:
            missed.append((c["id"], c["expected"]["note"], r.status, r.reason))
    assert not missed, f"undetected watermarks: {missed}"


@pytest.mark.skipif(not c2_watermark.HAVE_OCR, reason="tesseract not installed")
def test_clean_controls_not_flagged_as_watermarked():
    """Scene text (billboards, signs) must not be called a watermark."""
    truth = _truth()
    false_pos = []
    for c in truth["cases"]:
        if c["category"] != "clean":
            continue
        r = c2_watermark.run_one(_entry(c["entries"][0]))
        if r.status == FAIL:
            false_pos.append((c["id"], c["base"], r.reason))
    assert not false_pos, f"clean images flagged as watermarked: {false_pos}"


def test_no_ocr_degrades_to_unverified(monkeypatch):
    monkeypatch.setattr(c2_watermark, "HAVE_OCR", False)
    truth = _truth()
    r = c2_watermark.run_one(_entry(truth["cases"][0]["entries"][0]))
    assert r.status == "UNVERIFIED"


# ---------------------------------------------------------------------- C5

def test_low_resolution_warns(tmp_path):
    p = tmp_path / "tiny.jpg"
    Image.new("RGB", (400, 300), (120, 140, 90)).save(p)
    e = Entry(image="tiny.jpg")
    e.local_path = str(p)
    r = c5_aesthetic.run_one(e)
    assert r.status == WARN
    assert "low resolution" in r.reason


def test_extreme_aspect_warns(tmp_path):
    p = tmp_path / "banner.jpg"
    Image.new("RGB", (4000, 900), (10, 20, 30)).save(p)
    e = Entry(image="banner.jpg")
    e.local_path = str(p)
    r = c5_aesthetic.run_one(e)
    assert r.status == WARN
    assert "aspect ratio" in r.reason


def test_sharp_fixture_bases_mostly_pass():
    """Most curated bases should carry no aesthetic advisory."""
    truth = _truth()
    bases = sorted({c["base"] for c in truth["cases"]})
    statuses = []
    for b in bases:
        e = Entry(image=f"base/{b}.jpg")
        e.local_path = str(FIXTURES / "base" / f"{b}.jpg")
        statuses.append(c5_aesthetic.run_one(e).status)
    pass_rate = statuses.count(PASS) / len(statuses)
    assert pass_rate >= 0.7, f"only {pass_rate:.0%} of curated bases pass C5"


def test_c5_never_fails():
    """C5 is advisory: statuses are only PASS/WARN/UNVERIFIED, never FAIL."""
    truth = _truth()
    for c in truth["cases"][:10]:
        r = c5_aesthetic.run_one(_entry(c["entries"][0]))
        assert r.status in (PASS, WARN, "UNVERIFIED")
