"""Perceptual hashing: pHash (DCT) + dHash (gradient), stdlib+numpy+PIL only."""

from __future__ import annotations

import numpy as np
from PIL import Image

HASH_BITS = 64


def _dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II transform matrix."""
    k = np.arange(n)
    m = np.sqrt(2.0 / n) * np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m[0, :] = np.sqrt(1.0 / n)
    return m


_DCT32 = _dct_matrix(32)


def phash(img: Image.Image, hash_size: int = 8) -> int:
    """64-bit DCT perceptual hash (equivalent construction to imagehash.phash)."""
    a = np.asarray(
        img.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float64
    )
    # errstate: macOS Accelerate BLAS emits spurious fp warnings on matmul;
    # the finite check below catches any real numeric failure.
    with np.errstate(all="ignore"):
        dct = _DCT32 @ a @ _DCT32.T
    if not np.isfinite(dct).all():
        raise ValueError("non-finite DCT output — corrupt image data?")
    low = dct[:hash_size, :hash_size].flatten()
    med = np.median(low[1:])  # exclude DC term from the median
    bits = low > med
    bits[0] = False
    return int("".join("1" if b else "0" for b in bits), 2)


def dhash(img: Image.Image, hash_size: int = 8) -> int:
    """64-bit difference hash (horizontal gradient)."""
    a = np.asarray(
        img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS),
        dtype=np.int16,
    )
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# Center-crop factors hashed per image: catches 10-30% center crops, which
# plain whole-frame hashes miss.
CROP_FACTORS = (1.0, 0.9, 0.8, 0.7)


def hash_family(img: Image.Image) -> list:
    """[(phash, dhash), ...] for the full frame and center crops."""
    w, h = img.size
    fam = []
    for f in CROP_FACTORS:
        if f >= 1.0:
            crop = img
        else:
            dx, dy = int(w * (1 - f) / 2), int(h * (1 - f) / 2)
            crop = img.crop((dx, dy, w - dx, h - dy))
        fam.append((phash(crop), dhash(crop)))
    return fam


def family_distance(fam_a: list, fam_b: list) -> tuple:
    """Min (phash_dist, dhash_dist) over all family pairings.

    The two distances are taken from the SAME best pairing (chosen by
    phash+dhash sum) so they describe one coherent comparison.
    """
    best = (HASH_BITS + 1, HASH_BITS + 1)
    for pa, da in fam_a:
        for pb, db in fam_b:
            d = (hamming(pa, pb), hamming(da, db))
            if d[0] + d[1] < best[0] + best[1]:
                best = d
    return best
