"""Pipeline: resolve entries → run checks (parallel where safe) → aggregate."""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

from .checks import c1_duplicate, c2_watermark, c3_location, c4_caption, c5_aesthetic
from .model import (
    ADVISE, FAIL, KEEP, REJECT, UNSURE, UNVERIFIED, WARN,
    CheckResult, ImageVerdict,
)
from .vision import CACHE_DIR, VisionClient

DOWNLOAD_TIMEOUT = 30
C2_CACHE_VERSION = "1"
WORKERS = 4


def resolve_entries(entries: list, base_dir: Path) -> None:
    """Fill entry.local_path: resolve local paths, download remote URLs."""
    tmp = None
    for e in entries:
        if e.image.startswith(("http://", "https://")):
            if tmp is None:
                tmp = Path(tempfile.mkdtemp(prefix="image-truth-"))
            name = hashlib.sha256(e.image.encode()).hexdigest()[:16] + ".img"
            target = tmp / name
            try:
                req = urllib.request.Request(
                    e.image, headers={"User-Agent": "image-truth/0.1"}
                )
                with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                    target.write_bytes(r.read())
                e.local_path = str(target)
            except Exception:  # noqa: BLE001 — reported as unreadable by checks
                e.local_path = ""
        else:
            p = (base_dir / e.image).resolve()
            e.local_path = str(p) if p.exists() else ""


def _content_hash(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _cached_per_image(check_module, entries, cache_dir: Path, use_cache: bool, workers: int):
    """Run a per-image check with a content-hash disk cache (C2 is slow)."""
    cache_dir.mkdir(exist_ok=True)
    results = [None] * len(entries)
    to_run = []
    for i, e in enumerate(entries):
        h = _content_hash(e.local_path) if e.local_path else ""
        cpath = cache_dir / f"{check_module.CHECK}-{C2_CACHE_VERSION}-{h[:32]}.json"
        cached = None
        if use_cache and h and cpath.exists():
            try:
                cached = CheckResult(**json.loads(cpath.read_text()))
            except (ValueError, TypeError):
                cached = None  # corrupt cache entry (crashed run) — re-run
        if cached is not None:
            results[i] = cached
        else:
            to_run.append((i, e, cpath if h else None))

    def worker(item):
        i, e, cpath = item
        r = check_module.run_one(e)
        if cpath is not None and r.status != UNVERIFIED:
            cpath.write_text(json.dumps(r.__dict__))
        return i, r

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for i, r in pool.map(worker, to_run):
            results[i] = r
    return results


def run_checks(entries: list, vision: VisionClient = None, use_cache: bool = True) -> list:
    """Returns list[list[CheckResult]] aligned with entries."""
    vision = vision or VisionClient(use_cache=use_cache)
    cache_dir = Path(CACHE_DIR)

    c1_results = c1_duplicate.run(entries)
    c2_results = _cached_per_image(c2_watermark, entries, cache_dir, use_cache, WORKERS)
    c5_results = [c5_aesthetic.run_one(e) for e in entries]

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        c3_results = list(pool.map(lambda e: c3_location.run_one(e, vision), entries))
        c4_results = list(pool.map(lambda e: c4_caption.run_one(e, vision), entries))

    return [
        [c1_results[i], c2_results[i], c3_results[i], c4_results[i], c5_results[i]]
        for i in range(len(entries))
    ]


BLOCKING = ("c1", "c2", "c3", "c4")


def aggregate(entry, results: list) -> ImageVerdict:
    """KEEP / REJECT / ADVISE. Blocking FAILs reject; UNSURE/WARN advise."""
    fails = [r for r in results if r.status == FAIL and r.check in BLOCKING]
    unsure = [r for r in results if r.status == UNSURE]
    warns = [r for r in results if r.status == WARN]

    if not entry.local_path:
        return ImageVerdict(
            entry, REJECT, results, reason="image could not be loaded (missing file or failed download)"
        )
    if fails:
        top = max(fails, key=lambda r: r.confidence)
        return ImageVerdict(entry, REJECT, results, reason=f"{top.check}: {top.reason}")
    if unsure:
        top = unsure[0]
        return ImageVerdict(entry, ADVISE, results, reason=f"{top.check} unsure: {top.reason}")
    if warns:
        top = warns[0]
        return ImageVerdict(entry, ADVISE, results, reason=f"{top.check}: {top.reason}")
    unverified = [r.check for r in results if r.status == UNVERIFIED]
    if unverified:
        return ImageVerdict(
            entry, KEEP, results,
            reason=f"all runnable checks passed ({', '.join(unverified)} unverified)",
        )
    return ImageVerdict(entry, KEEP, results, reason="all checks passed")


def audit(entries: list, base_dir: Path, vision: VisionClient = None, use_cache: bool = True) -> list:
    resolve_entries(entries, base_dir)
    all_results = run_checks(entries, vision=vision, use_cache=use_cache)
    return [aggregate(e, rs) for e, rs in zip(entries, all_results)]
