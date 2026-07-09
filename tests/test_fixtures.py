"""Validate the generated fixture set (run after fixtures/make_fixtures.py)."""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TRUTH = FIXTURES / "truth.json"

pytestmark = pytest.mark.skipif(
    not (TRUTH.exists() and (FIXTURES / "cases").is_dir() and (FIXTURES / "base" / "_meta.json").exists()),
    reason="fixtures not built — run fixtures/make_fixtures.py",
)


@pytest.fixture(scope="module")
def truth():
    return json.loads(TRUTH.read_text())


def test_case_counts(truth):
    assert truth["counts"] == {
        "duplicate": 30,
        "watermark": 20,
        "location_mismatch": 25,
        "clean": 25,
        "caption_mismatch": 20,
    }
    assert len(truth["cases"]) == 120


def test_no_base_reuse_within_bundle(truth):
    for b in range(truth["bundles"]):
        bases = [c["base"] for c in truth["cases"] if c["bundle"] == b]
        assert len(bases) == len(set(bases)), f"bundle {b} reuses a base"


def test_all_case_images_exist(truth):
    missing = [
        e["image"]
        for c in truth["cases"]
        for e in c["entries"]
        if not (FIXTURES / e["image"]).exists()
    ]
    assert not missing, f"{len(missing)} referenced images missing: {missing[:5]}"


def test_duplicate_cases_have_two_entries_on_distinct_pages(truth):
    for c in truth["cases"]:
        if c["category"] == "duplicate":
            assert len(c["entries"]) == 2
            assert c["entries"][0]["page"] != c["entries"][1]["page"]


def test_expected_verdicts(truth):
    for c in truth["cases"]:
        exp = c["expected"]
        if c["category"] == "clean":
            assert exp["verdict"] == "KEEP" and exp["check"] is None
        else:
            assert exp["verdict"] == "REJECT"
            assert exp["check"] in ("c1", "c2", "c3", "c4")


def test_hard_location_cases_flagged(truth):
    hard = [c for c in truth["cases"] if c["hard"]]
    assert len(hard) == 5
    assert all(c["category"] == "location_mismatch" for c in hard)


def test_sources_manifest_covers_used_bases(truth):
    sources = (FIXTURES / "SOURCES.md").read_text()
    used = {c["base"] for c in truth["cases"]}
    unlisted = [b for b in used if f"| {b} |" not in sources]
    assert not unlisted, f"bases missing from SOURCES.md: {unlisted}"


def test_sources_licenses_are_free(truth):
    meta = json.loads((FIXTURES / "base" / "_meta.json").read_text())
    used = {c["base"] for c in truth["cases"]}
    allowed = ("cc0", "public domain", "pdm", "no restrictions")
    bad = {
        b: meta[b]["license"]
        for b in used
        if not any(a in meta[b]["license"].lower() for a in allowed)
    }
    assert not bad, f"non-CC0/PD licenses in use: {bad}"
