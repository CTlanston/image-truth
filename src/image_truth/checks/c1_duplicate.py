"""C1 — cross-page duplicate detection via pHash + dHash families.

Catches exact duplicates and resized / center-cropped (10-30%) / re-encoded /
lightly color-graded variants. Thresholds calibrated on the fixture set:
true derived pairs max out at (phash 4, dhash 5); the closest impostor pair
of distinct photos is (14, 17). See EVIDENCE.md iteration 2.
"""

from __future__ import annotations

from PIL import Image

from ..hashing import HASH_BITS, family_distance, hash_family
from ..model import FAIL, PASS, UNVERIFIED, CheckResult

PHASH_MAX = 10
DHASH_MAX = 14

CHECK = "c1"


def run(entries: list) -> list:
    """One CheckResult per entry, in order. Cross-image: needs all entries."""
    families = []
    load_errors = {}
    for i, e in enumerate(entries):
        try:
            with Image.open(e.local_path) as img:
                families.append(hash_family(img))
        except Exception as exc:  # noqa: BLE001 — unreadable file must not kill the run
            families.append(None)
            load_errors[i] = str(exc)

    # union-find over pairwise matches
    parent = list(range(len(entries)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    dist = {}
    for i in range(len(entries)):
        if families[i] is None:
            continue
        for j in range(i + 1, len(entries)):
            if families[j] is None:
                continue
            p, d = family_distance(families[i], families[j])
            if p <= PHASH_MAX and d <= DHASH_MAX:
                dist[(i, j)] = (p, d)
                parent[find(i)] = find(j)

    groups = {}
    for i in range(len(entries)):
        if families[i] is not None:
            groups.setdefault(find(i), []).append(i)

    results = []
    for i, e in enumerate(entries):
        if families[i] is None:
            results.append(CheckResult(CHECK, UNVERIFIED, reason=f"could not read image: {load_errors[i]}"))
            continue
        group = groups[find(i)]
        if len(group) == 1:
            results.append(CheckResult(CHECK, PASS, confidence=1.0, reason="no duplicates found"))
            continue
        others = [entries[j].ref for j in group if j != i]
        pair_ds = [dist[k] for k in dist if i in k]
        best = min(pair_ds, key=lambda x: x[0] + x[1])
        conf = 1.0 - (best[0] + best[1]) / (2 * HASH_BITS)
        results.append(
            CheckResult(
                CHECK,
                FAIL,
                confidence=round(conf, 3),
                reason=f"duplicate of {others[0]}" + (f" (+{len(others) - 1} more)" if len(others) > 1 else ""),
                details={"group": [entries[j].image for j in group], "phash_dist": best[0], "dhash_dist": best[1]},
            )
        )
    return results
