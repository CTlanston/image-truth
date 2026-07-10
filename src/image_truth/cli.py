"""image-truth CLI: `image-truth check <manifest> [--json] [--ci] [--no-cache]`.

Exit codes (the CI contract):
  0  every image KEEP or ADVISE
  1  at least one REJECT
  2  usage / manifest error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .manifest import parse
from .model import REJECT, exit_code
from .pipeline import audit
from .report import summary, to_json, to_markdown
from .vision import VisionClient


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(
        prog="image-truth",
        description="Image QA gate: duplicates, watermarks, location & caption mismatches.",
    )
    ap.add_argument("--version", action="version", version=f"image-truth {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="audit a manifest of images")
    chk.add_argument("manifest", help="manifest file (.md, .json, .yaml — or IMAGE_CREDITS.md)")
    chk.add_argument("--json", action="store_true", help="print report.json to stdout")
    chk.add_argument("--ci", action="store_true", help="exit 1 if any image is rejected")
    chk.add_argument("--no-cache", action="store_true", help="bypass the vision/OCR response cache")
    chk.add_argument("--out", default=".", help="directory for report.md / report.json (default: .)")
    chk.add_argument("--model", default=None, help="vision model override (default: claude-sonnet-5)")

    args = ap.parse_args(argv)
    try:
        entries = parse(args.manifest)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"image-truth: {exc}", file=sys.stderr)
        return 2

    base_dir = Path(args.manifest).resolve().parent
    vision = VisionClient(model=args.model, use_cache=not args.no_cache)
    verdicts = audit(entries, base_dir, vision=vision, use_cache=not args.no_cache)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    doc = to_json(verdicts, args.manifest)
    (out / "report.json").write_text(json.dumps(doc, indent=2))
    (out / "report.md").write_text(to_markdown(verdicts, args.manifest))

    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        s = doc["summary"]
        print(
            f"image-truth: {s['total']} images — "
            f"{s['keep']} keep, {s['reject']} reject, {s['advise']} advise"
        )
        for v in verdicts:
            if v.verdict == REJECT:
                print(f"  ❌ {v.entry.image}: {v.reason}")
        if s["unverified_checks"]:
            print(f"  (unverified checks: {', '.join(s['unverified_checks'])})")
        print(f"reports: {out / 'report.md'} · {out / 'report.json'}")

    return exit_code(verdicts) if args.ci else 0


if __name__ == "__main__":
    sys.exit(main())
