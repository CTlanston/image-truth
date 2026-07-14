#!/usr/bin/env python3
"""Compare vision providers on the labeled fixture set: accuracy, speed, cost.

Runs the vision-relevant categories (25 location_mismatch + 20 caption_mismatch
+ 25 clean) through C3 + C4 for each candidate — 140 live calls per model by
default (cache off, so latency and usage are real). Scoring uses the same CI
semantics as G3: a mismatch case is correct iff the responsible check FAILs; a
clean case is correct iff neither C3 nor C4 FAILs (UNSURE never rejects, but
unsure counts are reported — a model that shrugs is not pulling its weight).

Cost is computed from the usage tokens the APIs actually report, priced by the
PRICES table below (USD per 1M tokens — edit as prices move), then extrapolated
to the standard "60-image audit" (120 calls).

Usage:
  python3 scripts/compare_models.py                      # default candidates
  python3 scripts/compare_models.py --candidates dashscope:qwen3-vl-flash,ark:doubao-seed-1-6-vision-250815
  python3 scripts/compare_models.py --use-cache          # cheap re-score, no latency data
  python3 scripts/compare_models.py --limit 10           # smoke-test subset per category

Writes metrics/model_comparison.json and prints a markdown table.
"""

import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from image_truth.checks import c3_location, c4_caption  # noqa: E402
from image_truth.model import FAIL, UNSURE, UNVERIFIED, Entry  # noqa: E402
from image_truth.vision import VisionClient  # noqa: E402

FIXTURES = ROOT / "fixtures"

DEFAULT_CANDIDATES = [
    ("dashscope", "qwen3-vl-flash"),
    ("ark", "doubao-seed-1-6-vision-250815"),
    ("gemini", "gemini-2.5-flash-lite"),
    ("anthropic", "claude-haiku-4-5"),
]

# USD per 1M tokens (input, output) — verified 2026-07; edit as prices move.
PRICES = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "qwen3-vl-flash": (0.05, 0.40),
    "doubao-seed-1-6-vision-250815": (0.11, 0.28),  # ≤200-tok-output discounted tier
}

WORKERS = 4


def load_cases(limit=None):
    truth = json.loads((FIXTURES / "truth.json").read_text())
    by_cat = {"location_mismatch": [], "caption_mismatch": [], "clean": []}
    for c in truth["cases"]:
        if c["category"] in by_cat:
            by_cat[c["category"]].append(c)
    if limit:
        by_cat = {k: v[:limit] for k, v in by_cat.items()}
    return by_cat


def entry_of(case):
    e = Entry(**case["entries"][0])
    e.local_path = str(FIXTURES / e.image)
    return e


def eval_model(provider, model, by_cat, use_cache):
    client = VisionClient(provider=provider, model=model, use_cache=use_cache)
    if not client.available:
        return {"provider": provider, "model": model,
                "error": f"key missing: {client.key_env} not configured"}

    jobs = []  # (kind, case_id, callable)
    for c in by_cat["location_mismatch"]:
        jobs.append(("loc", c["id"], lambda e=entry_of(c): c3_location.run_one(e, client)))
    for c in by_cat["caption_mismatch"]:
        jobs.append(("cap", c["id"], lambda e=entry_of(c): c4_caption.run_one(e, client)))
    for c in by_cat["clean"]:
        e = entry_of(c)
        jobs.append(("clean_c3", c["id"], lambda e=e: c3_location.run_one(e, client)))
        jobs.append(("clean_c4", c["id"], lambda e=e: c4_caption.run_one(e, client)))

    t0 = time.monotonic()
    results = {}
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fn): (kind, cid) for kind, cid, fn in jobs}
        for fut in cf.as_completed(futures):
            kind, cid = futures[fut]
            results[(kind, cid)] = fut.result()
    wall = time.monotonic() - t0

    # score with G3/CI semantics
    misses, unsure_count, unverified = [], 0, 0
    loc_ok = cap_ok = clean_ok = 0
    for c in by_cat["location_mismatch"]:
        r = results[("loc", c["id"])]
        unsure_count += r.status == UNSURE
        unverified += r.status == UNVERIFIED
        if r.status == FAIL:
            loc_ok += 1
        else:
            misses.append(("loc", c["id"], r.status, r.reason[:80]))
    for c in by_cat["caption_mismatch"]:
        r = results[("cap", c["id"])]
        unsure_count += r.status == UNSURE
        unverified += r.status == UNVERIFIED
        if r.status == FAIL:
            cap_ok += 1
        else:
            misses.append(("cap", c["id"], r.status, r.reason[:80]))
    for c in by_cat["clean"]:
        r3, r4 = results[("clean_c3", c["id"])], results[("clean_c4", c["id"])]
        unsure_count += (r3.status == UNSURE) + (r4.status == UNSURE)
        unverified += (r3.status == UNVERIFIED) + (r4.status == UNVERIFIED)
        if r3.status != FAIL and r4.status != FAIL:
            clean_ok += 1
        else:
            bad = r3 if r3.status == FAIL else r4
            misses.append(("clean", c["id"], f"{bad.check} FAIL", bad.reason[:80]))

    if unverified:
        first = next(m for m in misses if m[2] == UNVERIFIED)
        return {"provider": provider, "model": model,
                "error": f"{unverified} calls UNVERIFIED (API errors) — scores would be "
                         f"meaningless. First error: {first[3]}"}

    n_loc, n_cap, n_clean = (len(by_cat[k]) for k in
                             ("location_mismatch", "caption_mismatch", "clean"))
    total = n_loc + n_cap + n_clean
    correct = loc_ok + cap_ok + clean_ok

    if model not in PRICES:
        print(f"   ⚠️ no price entry for {model} — cost will read $0; add it to PRICES")
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    cost = client.tokens_in / 1e6 * price_in + client.tokens_out / 1e6 * price_out
    per_call = cost / client.calls_made if client.calls_made else 0.0

    return {
        "provider": provider,
        "model": model,
        "cases": total,
        "accuracy": round(correct / total, 4) if total else 0,
        "loc_mismatch_recall": f"{loc_ok}/{n_loc}",
        "cap_mismatch_recall": f"{cap_ok}/{n_cap}",
        "clean_pass": f"{clean_ok}/{n_clean}",
        "unsure_verdicts": unsure_count,
        "unverified": unverified,
        "live_calls": client.calls_made,
        "avg_latency_s": round(client.live_seconds / client.calls_made, 2)
        if client.calls_made else None,
        "wall_s": round(wall, 1),
        "tokens_in": client.tokens_in,
        "tokens_out": client.tokens_out,
        "eval_cost_usd": round(cost, 4),
        "audit60_cost_usd": round(per_call * 120, 4),
        "misses": misses,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=None,
                    help="comma list of provider:model (default: qwen/doubao/gemini/haiku)")
    ap.add_argument("--use-cache", action="store_true",
                    help="allow cached verdicts (no latency/cost measurement)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap cases per category (smoke test)")
    args = ap.parse_args()

    if args.candidates:
        candidates = []
        for c in args.candidates.split(","):
            if ":" not in c:
                sys.exit(f"--candidates entries must be provider:model (got '{c}')")
            candidates.append(tuple(c.split(":", 1)))
    else:
        candidates = DEFAULT_CANDIDATES
    by_cat = load_cases(args.limit)
    n = sum(len(v) for v in by_cat.values())
    print(f"comparing {len(candidates)} models on {n} cases "
          f"({'cached ok' if args.use_cache else 'all live'})\n")

    rows = []
    for provider, model in candidates:
        print(f"→ {provider}/{model} ...", flush=True)
        rows.append(eval_model(provider, model, by_cat, args.use_cache))
        r = rows[-1]
        lat = f"{r['avg_latency_s']}s/call" if r.get("avg_latency_s") is not None else "cached"
        print("   " + (r.get("error") or
              f"acc {r['accuracy']:.1%} · {lat} · ${r['eval_cost_usd']} eval"))

    # merge into existing results by (provider, model) so incremental runs
    # (one model at a time, as keys arrive) don't wipe earlier rows
    out = ROOT / "metrics" / "model_comparison.json"
    merged = {}
    if out.exists():
        try:
            for r in json.loads(out.read_text()).get("results", []):
                merged[(r["provider"], r["model"])] = r
        except ValueError:
            pass
    for r in rows:
        merged[(r["provider"], r["model"])] = r
    out.write_text(json.dumps({"cases_per_category": {k: len(v) for k, v in by_cat.items()},
                               "results": list(merged.values())}, indent=2))

    ok_rows = [r for r in rows if "error" not in r]
    if ok_rows:
        print("\n| model | accuracy | loc recall | cap recall | clean pass | unsure | s/call | eval $ | 60-img audit $ |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in sorted(ok_rows, key=lambda r: -r["accuracy"]):
            print(f"| {r['provider']}/{r['model']} | {r['accuracy']:.1%} | {r['loc_mismatch_recall']} "
                  f"| {r['cap_mismatch_recall']} | {r['clean_pass']} | {r['unsure_verdicts']} "
                  f"| {r['avg_latency_s']} | ${r['eval_cost_usd']} | ${r['audit60_cost_usd']} |")
    for r in rows:
        if "error" in r:
            print(f"\n⚠️ {r['provider']}/{r['model']}: {r['error']}")
    print(f"\nfull results: {out}")


if __name__ == "__main__":
    main()
