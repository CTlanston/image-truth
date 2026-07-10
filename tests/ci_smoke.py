"""Hermetic CI smoke test — generates its own images, needs no fixtures,
no network, and no API key. Exercises the deterministic core (C1 duplicate,
C2 watermark) and the CLI exit-code contract (G1-deterministic + G2).

Run: python3 -m pytest tests/ci_smoke.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size):
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_truth.checks import c1_duplicate, c2_watermark
from image_truth.model import FAIL, PASS, Entry


def _scene(path, seed, size=(1200, 900)):
    """A deterministic non-trivial image (blocks of colour + shapes)."""
    rng = __import__("random").Random(seed)
    img = Image.new("RGB", size, (rng.randint(0, 255),) * 3)
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x0, y0 = rng.randint(0, size[0]), rng.randint(0, size[1])
        x1, y1 = x0 + rng.randint(20, 300), y0 + rng.randint(20, 300)
        c = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        (d.rectangle if rng.random() > 0.5 else d.ellipse)([x0, y0, x1, y1], fill=c)
    img.save(path, "JPEG", quality=90)
    return img


def _entry(path):
    e = Entry(image=str(path))
    e.local_path = str(path)
    return e


def test_c1_detects_exact_and_resized_duplicate(tmp_path):
    a = tmp_path / "a.jpg"
    _scene(a, seed=1)
    exact = tmp_path / "a_copy.jpg"
    Image.open(a).save(exact, "JPEG", quality=90)
    resized = tmp_path / "a_small.jpg"
    Image.open(a).resize((600, 450)).save(resized, "JPEG", quality=60)
    distinct = tmp_path / "b.jpg"
    _scene(distinct, seed=999)

    results = c1_duplicate.run([_entry(a), _entry(exact), _entry(resized), _entry(distinct)])
    assert results[0].status == FAIL  # a groups with its copies
    assert results[1].status == FAIL
    assert results[2].status == FAIL  # perceptual tolerance: resized still caught
    assert results[3].status == PASS  # distinct scene not flagged


@pytest.mark.skipif(not c2_watermark.HAVE_OCR, reason="tesseract not installed")
def test_c2_detects_watermark_but_not_clean(tmp_path):
    clean = tmp_path / "clean.jpg"
    _scene(clean, seed=7)

    wm = tmp_path / "wm.jpg"
    img = _scene(wm, seed=8)
    d = ImageDraw.Draw(img)
    font = _font(48)
    text = "© STOCKPHOTO.COM"
    tw = d.textlength(text, font=font)
    d.text((img.size[0] - tw - 30, img.size[1] - 90), text, font=font,
           fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    img.save(wm, "JPEG", quality=90)

    assert c2_watermark.run_one(_entry(wm)).status == FAIL
    assert c2_watermark.run_one(_entry(clean)).status == PASS


def _cli(*args, cwd):
    root = Path(__file__).resolve().parent.parent
    import os
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    return subprocess.run(
        [sys.executable, "-m", "image_truth", *args],
        capture_output=True, text=True, env=env, cwd=cwd, timeout=180,
    )


def test_cli_exit_codes(tmp_path):
    root = Path(__file__).resolve().parent.parent
    a = tmp_path / "hero.jpg"
    _scene(a, seed=3)
    dup = tmp_path / "gallery.jpg"
    Image.open(a).save(dup, "JPEG", quality=88)

    # manifest with a duplicate pair -> REJECT -> exit 1 under --ci
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([
        {"image": "hero.jpg", "page": "index"},
        {"image": "gallery.jpg", "page": "gallery"},
    ]))
    r = _cli("check", str(bad), "--ci", "--out", str(tmp_path / "o1"), cwd=root)
    assert r.returncode == 1, r.stderr

    # single clean image -> exit 0
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"image": "hero.jpg", "page": "index"}]))
    r = _cli("check", str(good), "--ci", "--out", str(tmp_path / "o2"), cwd=root)
    assert r.returncode == 0, r.stderr

    # a REJECT without --ci still exits 0 (report-only)
    r = _cli("check", str(bad), "--out", str(tmp_path / "o3"), cwd=root)
    assert r.returncode == 0, r.stderr

    # bad manifest -> exit 2
    r = _cli("check", str(tmp_path / "missing.json"), cwd=root)
    assert r.returncode == 2


def test_vision_degrades_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    from image_truth.checks import c3_location
    from image_truth.vision import VisionClient
    a = tmp_path / "x.jpg"
    _scene(a, seed=5)
    e = Entry(image="x.jpg", claimed_location="Paris")
    e.local_path = str(a)
    r = c3_location.run_one(e, VisionClient())
    assert r.status == "UNVERIFIED"
