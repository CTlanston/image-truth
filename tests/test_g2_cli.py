"""G2 integration: manifest formats, full CLI run, exit codes, JSON schema."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_truth.manifest import parse

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "truth.json").exists(), reason="fixtures not built"
)


# ------------------------------------------------------------- parser

def test_parse_markdown_manifest():
    entries = parse(str(FIXTURES / "g2_manifest.md"))
    assert len(entries) == 12
    assert all(e.image.startswith("cases/") for e in entries)
    assert all(e.claimed_location and e.caption for e in entries)


def test_parse_json_manifest(tmp_path):
    doc = [
        {"image": "a.jpg", "claimed_location": "X", "caption": "c", "page": "p", "slot": "s"},
        {"path": "b.png", "location": "Y"},
    ]
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    entries = parse(str(p))
    assert [e.image for e in entries] == ["a.jpg", "b.png"]
    assert entries[1].claimed_location == "Y"


def test_parse_yaml_manifest(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("images:\n  - image: a.jpg\n    location: X\n")
    entries = parse(str(p))
    assert entries[0].image == "a.jpg"
    assert entries[0].claimed_location == "X"


def test_parse_legacy_image_credits_format(tmp_path):
    """The IMAGE_CREDITS.md convention: Place/Subject/Local path columns."""
    p = tmp_path / "IMAGE_CREDITS.md"
    p.write_text(
        "# Image Credits\n\nSome prose here.\n\n"
        "## Chapter heroes\n\n"
        "| # | Day | Place | Subject | Local path | Original source |\n"
        "|---|---|---|---|---|---|\n"
        "| 0 | 06.08 · MON | Sunnyvale | Bay Area skyline at night | `images/heroes/00-bay.jpg` | [Pexels 351](https://www.pexels.com/photo/351/) |\n"
        "| 1 | 06.09 · TUE | Apple Park | Apple Park aerial | `images/heroes/01-apple.jpg` | [Pexels 136](https://www.pexels.com/photo/136/) |\n"
        "\n## Homepage Hero\n\n"
        "| Where | Local path | Original source | Subject |\n"
        "|---|---|---|---|\n"
        "| `index.html` hero | `images/heroes/00-hero.jpg` | [Pexels 708](https://www.pexels.com/photo/708/) | Bixby Bridge at golden hour |\n"
    )
    entries = parse(str(p))
    assert len(entries) == 3
    assert entries[0].image == "images/heroes/00-bay.jpg"
    assert entries[0].claimed_location == "Sunnyvale"
    assert entries[0].caption == "Bay Area skyline at night"
    assert entries[0].page == "06.08 · MON"
    assert entries[2].image == "images/heroes/00-hero.jpg"
    assert entries[2].caption == "Bixby Bridge at golden hour"


def test_parse_errors():
    with pytest.raises(FileNotFoundError):
        parse("nope.md")
    with pytest.raises(ValueError):
        parse(__file__)  # .py unsupported


# ------------------------------------------------------------- CLI e2e

def _run_cli(*args, cwd=ROOT):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-m", "image_truth", *args],
        capture_output=True, text=True, env=env, cwd=cwd, timeout=600,
    )


@pytest.fixture(scope="module")
def g2_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("g2out")
    proc = _run_cli("check", "fixtures/g2_manifest.md", "--ci", "--out", str(out))
    return proc, out


def test_cli_ci_exit_code_on_rejects(g2_run):
    proc, _ = g2_run
    assert proc.returncode == 1, proc.stderr  # manifest contains known-bad images


def test_cli_report_json_schema(g2_run):
    proc, out = g2_run
    doc = json.loads((out / "report.json").read_text())
    assert doc["tool"] == "image-truth"
    s = doc["summary"]
    assert s["total"] == 12 and s["reject"] >= 8 and s["keep"] >= 1
    assert doc["exit_code"] == 1
    for img in doc["images"]:
        assert img["verdict"] in ("KEEP", "REJECT", "ADVISE")
        checks = {c["check"] for c in img["checks"]}
        assert checks == {"c1", "c2", "c3", "c4", "c5"}
        for c in img["checks"]:
            assert c["status"] in ("PASS", "FAIL", "WARN", "UNSURE", "UNVERIFIED")


def test_cli_g2_verdicts_match_truth(g2_run):
    proc, out = g2_run
    doc = json.loads((out / "report.json").read_text())
    truth = json.loads((FIXTURES / "truth.json").read_text())
    expected = json.loads((FIXTURES / "g2_expected.json").read_text())
    by_image = {img["image"]: img["verdict"] for img in doc["images"]}
    wrong = []
    for c in truth["cases"]:
        if c["id"] not in expected:
            continue
        verdicts = [by_image[e["image"]] for e in c["entries"]]
        want = expected[c["id"]]
        ok = all(v == "REJECT" for v in verdicts) if want == "REJECT" else all(
            v in ("KEEP", "ADVISE") for v in verdicts
        )
        if not ok:
            wrong.append((c["id"], want, verdicts))
    assert not wrong, f"G2 verdict mismatches: {wrong}"


def test_cli_report_md_is_contact_sheet(g2_run):
    proc, out = g2_run
    md = (out / "report.md").read_text()
    assert md.startswith("# image-truth report")
    assert "❌ REJECT" in md and "✅ KEEP" in md
    # rejects render before keeps: a human sees problems first
    assert md.index("❌ REJECT") < md.index("✅ KEEP")


def test_cli_bad_manifest_exits_2():
    proc = _run_cli("check", "does-not-exist.md")
    assert proc.returncode == 2
    assert "not found" in proc.stderr


def test_cli_missing_image_rejects(tmp_path):
    m = tmp_path / "m.json"
    m.write_text(json.dumps([{"image": "ghost.jpg", "location": "X"}]))
    proc = _run_cli("check", str(m), "--ci", "--out", str(tmp_path))
    assert proc.returncode == 1
    doc = json.loads((tmp_path / "report.json").read_text())
    assert doc["images"][0]["verdict"] == "REJECT"
    assert "could not be loaded" in doc["images"][0]["reason"]
