#!/usr/bin/env python3
"""G3 E2E@scale: full 120-case suite x N reps against ground truth.

Runs each bundle as one manifest (bundles guarantee no base repeats within a
bundle, so C1 never mis-groups distinct cases). Every case is evaluated per
rep; a case is CORRECT when the tool's keep/reject decision matches the label:

  clean            -> KEEP   (any REJECT is a false-reject)
  duplicate        -> REJECT via c1 on BOTH entries
  watermark        -> REJECT (c2 expected)
  location_mismatch-> REJECT (c3 expected)
  caption_mismatch -> REJECT (c4 expected)

Also runs a cache-less subset of >=20 vision cases to prove the live path.

Writes metrics/e2e_results.json (per-case rows + summary) and, on any miss,
metrics/failure_analysis.md grouped by failure class.

Usage: python3 scripts/run_e2e.py [--reps 3] [--cacheless 24]
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from image_truth.model import KEEP, REJECT, ADVISE, FAIL  # noqa: E402
from image_truth.pipeline import resolve_entries, run_checks, aggregate  # noqa: E402
from image_truth.model import Entry  # noqa: E402
from image_truth.vision import VisionClient  # noqa: E402

FIXTURES = ROOT / "fixtures"
METRICS = ROOT / "metrics"
EXPECT_REJECT = {"duplicate", "watermark", "location_mismatch", "caption_mismatch"}
CHECK_FOR = {"watermark": "c2", "location_mismatch": "c3", "caption_mismatch": "c4"}


def load_bundles():
    truth = json.loads((FIXTURES / "truth.json").read_text())
    bundles = defaultdict(list)
    for case in truth["cases"]:
        bundles[case["bundle"]].append(case)
    return truth, bundles


def build_entries(cases):
    """Flat entry list for a bundle + index map back to (case, slot)."""
    entries, owner = [], []
    for ci, case in enumerate(cases):
        for si, spec in enumerate(case["entries"]):
            e = Entry(
                image=spec["image"], claimed_location=spec["claimed_location"],
                caption=spec["caption"], page=spec["page"], slot=spec["slot"],
            )
            entries.append(e)
            owner.append((ci, si))
    return entries, owner


def evaluate_case(case, verdicts, results):
    """Return (correct: bool, got: str, detail: str). verdicts/results are the
    per-entry outputs for this case's entries only."""
    cat = case["category"]
    got = "REJECT" if any(v.verdict == REJECT for v in verdicts) else (
        "ADVISE" if any(v.verdict == ADVISE for v in verdicts) else KEEP
    )
    if cat == "clean":
        correct = all(v.verdict == KEEP for v in verdicts)
        return correct, got, "" if correct else f"false {got}: {verdicts[0].reason}"
    if cat == "duplicate":
        # both entries must REJECT with c1 as a blocking fail
        both_c1 = all(
            any(r.check == "c1" and r.status == FAIL for r in rs) for rs in results
        )
        return both_c1, got, "" if both_c1 else "c1 did not flag both entries"
    # single-entry reject categories: the INTENDED check must fire (stricter
    # than "rejected somehow" — a reject for the wrong reason is not a pass)
    want_check = CHECK_FOR[cat]
    fired = any(r.check == want_check and r.status == FAIL for r in results[0])
    correct = fired  # firing the intended blocking check implies REJECT
    detail = ""
    if not correct:
        if verdicts[0].verdict == REJECT:
            detail = f"rejected but not by {want_check} (wrong reason): {verdicts[0].reason}"
        else:
            detail = f"expected REJECT via {want_check}, got {verdicts[0].verdict}: {verdicts[0].reason}"
    return correct, got, detail


def run_rep(bundles, vision, use_cache, rep):
    rows = []
    for bundle_id in sorted(bundles):
        cases = bundles[bundle_id]
        entries, owner = build_entries(cases)
        resolve_entries(entries, FIXTURES)
        all_results = run_checks(entries, vision=vision, use_cache=use_cache)
        verdicts = [aggregate(e, rs) for e, rs in zip(entries, all_results)]
        # regroup per case
        per_case_v = defaultdict(list)
        per_case_r = defaultdict(list)
        for (ci, _si), v, rs in zip(owner, verdicts, all_results):
            per_case_v[ci].append(v)
            per_case_r[ci].append(rs)
        for ci, case in enumerate(cases):
            correct, got, detail = evaluate_case(case, per_case_v[ci], per_case_r[ci])
            rows.append({
                "rep": rep, "bundle": bundle_id, "id": case["id"],
                "category": case["category"], "base": case["base"],
                "hard": case.get("hard", False),
                "expected": case["expected"]["verdict"], "got": got,
                "correct": correct, "detail": detail,
            })
    return rows


def summarize(rows, reps):
    total = len(rows)
    correct = sum(r["correct"] for r in rows)
    dup = [r for r in rows if r["category"] == "duplicate"]
    clean = [r for r in rows if r["category"] == "clean"]
    dup_detected = sum(r["correct"] for r in dup)
    clean_false_reject = sum(r["got"] == REJECT for r in clean)
    by_class = {}
    for cat in ("duplicate", "watermark", "location_mismatch", "clean", "caption_mismatch"):
        c = [r for r in rows if r["category"] == cat]
        by_class[cat] = {
            "n": len(c), "correct": sum(r["correct"] for r in c),
            "accuracy": round(sum(r["correct"] for r in c) / len(c), 4) if c else None,
        }
    return {
        "reps": reps,
        "case_evaluations": total,
        "overall_accuracy": round(correct / total, 4),
        "duplicate_detection": round(dup_detected / len(dup), 4) if dup else None,
        "clean_false_reject_rate": round(clean_false_reject / len(clean), 4) if clean else None,
        "by_class": by_class,
        "gates": {
            "case_evaluations>=360": total >= 360,
            "overall_accuracy>=0.95": correct / total >= 0.95,
            "duplicate_detection==1.0": dup_detected == len(dup),
            "clean_false_reject<=0.05": clean_false_reject / len(clean) <= 0.05,
        },
    }


def failure_analysis(rows):
    misses = [r for r in rows if not r["correct"]]
    if not misses:
        return None
    lines = ["# G3 failure analysis", "", f"{len(misses)} missed case-evaluations.", ""]
    by_cat = defaultdict(list)
    for m in misses:
        by_cat[m["category"]].append(m)
    for cat, ms in sorted(by_cat.items()):
        lines.append(f"## {cat} ({len(ms)})")
        for m in ms:
            hard = " [hard]" if m["hard"] else ""
            lines.append(f"- **{m['id']}**{hard} (base {m['base']}, rep {m['rep']}): {m['detail']}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--cacheless", type=int, default=24)
    ap.add_argument("--provider", default=None, help="vision provider (gemini|dashscope|ark|anthropic)")
    ap.add_argument("--model", default=None, help="vision model override")
    args = ap.parse_args()
    METRICS.mkdir(exist_ok=True)

    truth, bundles = load_bundles()
    vision = VisionClient(provider=args.provider, model=args.model)
    print(f"vision available: {vision.available} | model: {vision.model}")

    t0 = time.time()
    all_rows = []
    for rep in range(1, args.reps + 1):
        rt = time.time()
        rows = run_rep(bundles, vision, use_cache=True, rep=rep)
        acc = sum(r["correct"] for r in rows) / len(rows)
        all_rows.extend(rows)
        print(f"rep {rep}: {len(rows)} cases, accuracy {acc:.3f}, {time.time()-rt:.0f}s")

    # cache-less vision proof: run a subset of loc/cap/clean cases with no cache
    cacheless_cases = [c for c in truth["cases"]
                       if c["category"] in ("location_mismatch", "caption_mismatch", "clean")]
    cacheless_cases = cacheless_cases[:args.cacheless]
    fresh = VisionClient(use_cache=False, provider=args.provider, model=args.model)
    calls_before = fresh.calls_made
    cl_correct = 0
    if fresh.available:
        for case in cacheless_cases:
            entries, _ = build_entries([case])
            resolve_entries(entries, FIXTURES)
            rs = run_checks(entries, vision=fresh, use_cache=False)
            vs = [aggregate(e, r) for e, r in zip(entries, rs)]
            correct, _, _ = evaluate_case(case, vs, rs)
            cl_correct += correct
    cacheless_calls = fresh.calls_made - calls_before

    summary = summarize(all_rows, args.reps)
    summary["cacheless"] = {
        "cases": len(cacheless_cases),
        "live_vision_calls": cacheless_calls,
        "correct": cl_correct,
        "proves_live_path": cacheless_calls >= 20,
    }
    summary["wall_seconds"] = round(time.time() - t0, 1)
    summary["gates"]["cacheless_vision>=20"] = cacheless_calls >= 20

    (METRICS / "e2e_results.json").write_text(
        json.dumps({"summary": summary, "rows": all_rows}, indent=2)
    )
    fa = failure_analysis(all_rows)
    if fa:
        (METRICS / "failure_analysis.md").write_text(fa)

    print("\n=== G3 SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print("\nALL GATES PASS:", all(summary["gates"].values()))
    return 0 if all(summary["gates"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
