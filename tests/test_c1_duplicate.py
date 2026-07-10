"""G1 unit tests for C1 duplicate detection, against the labeled fixtures."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_truth.checks import c1_duplicate
from image_truth.hashing import hamming, phash, dhash
from image_truth.model import FAIL, PASS, Entry

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


def test_hash_selfconsistency():
    from PIL import Image

    img = Image.open(next((FIXTURES / "base").glob("*.jpg")))
    assert hamming(phash(img), phash(img)) == 0
    assert hamming(dhash(img), dhash(img)) == 0


def test_every_derived_duplicate_detected():
    """Contract: C1 on exact/derived duplicates = 100%."""
    truth = _truth()
    missed = []
    for c in truth["cases"]:
        if c["category"] != "duplicate":
            continue
        entries = [_entry(s) for s in c["entries"]]
        results = c1_duplicate.run(entries)
        if not all(r.status == FAIL for r in results):
            missed.append((c["id"], c["expected"]["note"]))
    assert not missed, f"undetected duplicate pairs: {missed}"


def test_no_false_positives_within_bundles():
    """Distinct-base images inside one bundle must not be grouped."""
    truth = _truth()
    for b in range(truth["bundles"]):
        entries, sources = [], []
        for c in truth["cases"]:
            if c["bundle"] == b and c["category"] != "duplicate":
                entries.append(_entry(c["entries"][0]))
                sources.append(c["base"])
        results = c1_duplicate.run(entries)
        false_pos = [
            (sources[i], r.reason)
            for i, r in enumerate(results)
            if r.status == FAIL
        ]
        assert not false_pos, f"bundle {b} false duplicates: {false_pos}"


def test_watermarked_variant_of_same_base_is_grouped():
    """A watermarked copy of the same photo IS a duplicate — sanity check."""
    truth = _truth()
    dup = next(c for c in truth["cases"] if c["category"] == "duplicate")
    wm = next(
        (c for c in truth["cases"] if c["category"] == "watermark" and c["base"] == dup["base"]),
        None,
    )
    if wm is None:
        pytest.skip("no watermark case shares a base with a dup case")
    entries = [_entry(dup["entries"][0]), _entry(wm["entries"][0])]
    results = c1_duplicate.run(entries)
    assert all(r.status == FAIL for r in results)


def test_unreadable_file_is_unverified_not_crash(tmp_path):
    bad = tmp_path / "corrupt.jpg"
    bad.write_bytes(b"not an image")
    truth = _truth()
    ok = _entry(truth["cases"][0]["entries"][0])
    broken = Entry(image="corrupt.jpg")
    broken.local_path = str(bad)
    results = c1_duplicate.run([ok, broken])
    assert results[0].status == PASS
    assert results[1].status == "UNVERIFIED"
