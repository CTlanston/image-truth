#!/usr/bin/env python3
"""Build the labeled fixture set for image-truth E2E tests.

Stage 1 (--download): fetch ~40 CC0/public-domain base images from Wikimedia
Commons (license verified via extmetadata, recorded in fixtures/SOURCES.md).
Stage 2 (--derive): deterministically derive 120 labeled cases into
fixtures/cases/ and write fixtures/truth.json:

  30 duplicate pairs   (resize / crop 10-30% / re-encode q=40 / color shift)
  20 watermark         (corner text / diagonal band / stock-style tile)
  25 location-mismatch (image of A labeled as B; 5 hard same-class pairs)
  25 clean controls    (correct labels — must NOT be rejected)
  20 caption-mismatch  (caption nouns absent from the image)

Cases are partitioned into bundles such that no base image appears in two
different cases of the same bundle (otherwise C1 would correctly flag a
cross-case duplicate and poison the labels).

Run:  python3 fixtures/make_fixtures.py            # both stages
      python3 fixtures/make_fixtures.py --download  # stage 1 only
      python3 fixtures/make_fixtures.py --derive    # stage 2 only
"""

import argparse
import io
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

FIXTURES = Path(__file__).resolve().parent
BASE_DIR = FIXTURES / "base"
CASES_DIR = FIXTURES / "cases"
TRUTH_PATH = FIXTURES / "truth.json"
SOURCES_PATH = FIXTURES / "SOURCES.md"
META_PATH = BASE_DIR / "_meta.json"

SEED = 42
API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia UA policy requires a descriptive agent with a contact URL;
# generic agents get 403 "Too Many Reqs".
UA = "image-truth-fixtures/0.1 (+https://github.com/CTlanston/image-truth) python-urllib"
ALLOWED_LICENSES = ("cc0", "public domain", "pdm", "no restrictions")

# klass drives location-mismatch pairing: easy mismatches pair different
# klasses (beach vs desert); hard mismatches pair within a klass.
SUBJECTS = [
    dict(slug="golden-gate", q="Golden Gate Bridge fog", loc="Golden Gate Bridge, San Francisco, California", cap="a sandy beach with people and a red bridge tower rising from fog over a bay", klass="bridge"),
    dict(slug="brooklyn-bridge", q="Brooklyn Bridge New York", loc="Brooklyn Bridge, New York City", cap="a stone-towered suspension bridge with cables", klass="bridge"),
    dict(slug="tower-bridge", q="Tower Bridge London", loc="Tower Bridge, London, England", cap="a bascule bridge with two towers over a river", klass="bridge"),
    dict(slug="eiffel-tower", q="Eiffel Tower Paris", loc="Eiffel Tower, Paris, France", cap="a tall iron lattice tower", klass="tower"),
    dict(slug="space-needle", q="Space Needle Seattle", loc="Space Needle, Seattle, Washington", cap="a futuristic observation tower with a saucer top", klass="tower"),
    dict(slug="grand-canyon", q="Grand Canyon Arizona", loc="Grand Canyon, Arizona", cap="a vast layered red rock canyon", klass="canyon"),
    dict(slug="antelope-canyon", q="Antelope Canyon Arizona", loc="Antelope Canyon, Arizona", cap="narrow sandstone slot canyon with light beams", klass="canyon"),
    dict(slug="statue-liberty", q="Statue of Liberty New York", loc="Statue of Liberty, New York Harbor", cap="a green copper statue holding a torch", klass="monument"),
    dict(slug="colosseum", q="Colosseum Rome exterior", loc="Colosseum, Rome, Italy", cap="an ancient elliptical stone amphitheatre", klass="monument"),
    dict(slug="stonehenge", q="Stonehenge Wiltshire", loc="Stonehenge, Wiltshire, England", cap="a prehistoric circle of standing stones in a field", klass="monument"),
    dict(slug="rio-panorama", q="Rio de Janeiro Sugarloaf panorama", loc="Rio de Janeiro, Brazil", cap="an aerial view of a coastal city and bay with mountains", klass="coastal-town"),
    dict(slug="pyramids-giza", q="Great Pyramid Giza", loc="Pyramids of Giza, Egypt", cap="large stone pyramids in the desert", klass="monument"),
    dict(slug="mount-fuji", q="Mount Fuji Japan", loc="Mount Fuji, Japan", cap="a snow-capped volcanic cone with aircraft flying past", klass="mountain"),
    dict(slug="matterhorn", q="Matterhorn mountain peak snow", loc="Matterhorn, Zermatt, Switzerland", cap="a sharp pyramid-shaped snowy alpine peak", klass="mountain"),
    dict(slug="yosemite", q="Yosemite Valley El Capitan", loc="Yosemite Valley, California", cap="granite cliffs above a forested valley", klass="mountain"),
    dict(slug="machu-picchu", q="Machu Picchu Peru", loc="Machu Picchu, Peru", cap="stone ruins on a green mountain ridge", klass="ruins"),
    dict(slug="venice-canal", q="Venice Grand Canal gondola", loc="Venice, Italy", cap="a canal lined with old buildings and boats", klass="canal"),
    dict(slug="amsterdam-canal", q="Amsterdam Prinsengracht canal houses boats", loc="Amsterdam, Netherlands", cap="houseboats moored along a tree-lined canal with old buildings", klass="canal"),
    dict(slug="sahara-dunes", q="Erg Chebbi sand dunes Morocco", loc="Sahara Desert, Morocco", cap="an aerial view of rippled orange sand dunes in a desert", klass="desert"),
    dict(slug="monument-valley", q="Monument Valley buttes", loc="Monument Valley, Utah", cap="red sandstone buttes on a desert plain", klass="desert"),
    dict(slug="death-valley", q="Death Valley dunes", loc="Death Valley, California", cap="arid desert basin with dunes", klass="desert"),
    dict(slug="uluru", q="Uluru Ayers Rock", loc="Uluru, Northern Territory, Australia", cap="a huge red monolith rock at sunset", klass="desert"),
    dict(slug="arches-utah", q="Delicate Arch Utah", loc="Arches National Park, Utah", cap="a freestanding natural red rock arch", klass="desert"),
    dict(slug="tropical-beach", q="tropical beach palm trees", loc="Kauai, Hawaii", cap="a sandy beach with palm trees and turquoise water", klass="beach"),
    dict(slug="santa-monica-pier", q="Santa Monica Pier ferris wheel", loc="Santa Monica Pier, California", cap="a pier with a ferris wheel by the ocean", klass="beach"),
    dict(slug="times-square", q="Times Square New York night", loc="Times Square, New York City", cap="a busy intersection with giant billboards", klass="city"),
    dict(slug="shibuya", q="Shibuya crossing Tokyo", loc="Shibuya Crossing, Tokyo, Japan", cap="a crowded urban pedestrian crossing", klass="city"),
    dict(slug="dubai-skyline", q="Burj Khalifa Dubai skyline", loc="Dubai, United Arab Emirates", cap="a supertall skyscraper above a city", klass="city"),
    dict(slug="mont-saint-michel", q="Mont Saint-Michel France", loc="Mont Saint-Michel, Normandy, France", cap="an island abbey rising above tidal flats", klass="coastal-town"),
    dict(slug="niagara-falls", q="Niagara Falls Horseshoe", loc="Niagara Falls, Ontario/New York", cap="a massive curved waterfall with mist", klass="waterfall"),
    dict(slug="great-wall", q="Great Wall of China Mutianyu", loc="Great Wall, Mutianyu, China", cap="a stone wall snaking over green mountains", klass="wall"),
    dict(slug="kinkakuji", q="Kinkaku-ji golden pavilion Kyoto", loc="Kinkaku-ji, Kyoto, Japan", cap="a gold-leafed pavilion beside a pond", klass="temple"),
    dict(slug="neuschwanstein", q="Neuschwanstein Castle Bavaria", loc="Neuschwanstein Castle, Bavaria, Germany", cap="a white fairy-tale castle on a forested hill", klass="castle"),
    dict(slug="st-basils", q="Saint Basil's Cathedral Moscow", loc="Saint Basil's Cathedral, Moscow, Russia", cap="a cathedral with colorful onion domes", klass="cathedral"),
    dict(slug="palm-drive", q="palm trees lined avenue California", loc="Los Angeles, California", cap="a street lined with tall palm trees", klass="street"),
    dict(slug="banff-lake", q="Moraine Lake Banff", loc="Moraine Lake, Banff, Alberta, Canada", cap="a turquoise glacial lake below rocky peaks", klass="lake"),
    dict(slug="lake-tahoe", q="Lake Tahoe shore", loc="Lake Tahoe, California/Nevada", cap="a clear blue mountain lake with boulders", klass="lake"),
    dict(slug="grand-prismatic", q="Grand Prismatic Spring Yellowstone", loc="Yellowstone National Park, Wyoming", cap="a rainbow-colored hot spring with steam", klass="geyser"),
    dict(slug="old-faithful", q="Old Faithful geyser eruption Yellowstone", loc="Yellowstone National Park, Wyoming", cap="a tall geyser erupting steam and water", klass="geyser"),
    dict(slug="mount-rushmore", q="Mount Rushmore presidents faces", loc="Mount Rushmore, South Dakota", cap="four giant presidential faces carved into a granite cliff", klass="monument"),
    dict(slug="bryce-canyon", q="Bryce Canyon hoodoos amphitheater", loc="Bryce Canyon National Park, Utah", cap="orange rock spires in a natural amphitheater", klass="canyon"),
    dict(slug="us-capitol", q="United States Capitol building Washington", loc="United States Capitol, Washington, D.C.", cap="a white domed neoclassical government building", klass="building"),
    dict(slug="hollywood-sign", q="Hollywood Sign Los Angeles", loc="Hollywood Sign, Los Angeles, California", cap="large white letters on a scrubby hillside", klass="hills"),
]

# 5 hard location mismatches: same-klass, visually confusable. (image, claimed-as)
HARD_LOC_PAIRS = [
    ("golden-gate", "brooklyn-bridge"),
    ("tower-bridge", "golden-gate"),
    ("venice-canal", "amsterdam-canal"),
    ("matterhorn", "mount-fuji"),
    ("palm-drive", "santa-monica-pier"),  # mirrors the real Santa Barbara/LA palm bug
]

WM_STYLES = ["corner", "diagonal", "tile"]
DUP_OPS = ["resize", "crop", "reencode", "color", "resize+reencode", "crop+color"]

FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def http_get(url, timeout=60):
    """GET with rate-limit backoff (Commons throttles aggressively)."""
    delays = [0, 5, 15, 45]
    for i, d in enumerate(delays):
        if d:
            time.sleep(d)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and i < len(delays) - 1:
                continue
            raise
    raise RuntimeError("unreachable")


def api_get(params):
    params = dict(params, format="json", maxlag=5)
    url = API + "?" + urllib.parse.urlencode(params)
    return json.loads(http_get(url, timeout=30).decode("utf-8"))


def license_ok(name):
    n = (name or "").lower()
    return any(a in n for a in ALLOWED_LICENSES)


def find_free_image(query):
    """Search Commons, return first hit whose verified license is CC0/PD."""
    data = api_get({
        "action": "query", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 40,
        "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
        "iiurlwidth": 1400,
    })
    pages = (data.get("query") or {}).get("pages") or {}
    ranked = sorted(pages.values(), key=lambda p: p.get("index", 999))
    for p in ranked:
        infos = p.get("imageinfo") or []
        if not infos:
            continue
        ii = infos[0]
        meta = ii.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not license_ok(lic):
            continue
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if (ii.get("width") or 0) < 900 or (ii.get("height") or 0) < 600:
            continue
        return {
            "title": p.get("title", ""),
            "url": ii.get("descriptionurl") or ii.get("descriptionshorturl", ""),
            "thumb": ii.get("thumburl") or ii.get("url"),
            "license": strip_html(lic),
            "artist": strip_html((meta.get("Artist") or {}).get("value", "")) or "unknown",
        }
    return None


def download_bases():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    meta = json.loads(META_PATH.read_text()) if META_PATH.exists() else {}
    for s in SUBJECTS:
        slug = s["slug"]
        out = BASE_DIR / f"{slug}.jpg"
        if out.exists() and slug in meta:
            continue
        try:
            # prefer the pinned URL from a committed _meta.json so rebuilds
            # fetch the exact images that passed visual QA
            hit = meta.get(slug) or find_free_image(s["q"])
            if not hit:
                print(f"  MISS  {slug}: no CC0/PD hit for '{s['q']}'")
                continue
            raw = http_get(hit["thumb"])
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            img.thumbnail((1400, 1400))
            img.save(out, "JPEG", quality=90)
            meta[slug] = hit
            print(f"  OK    {slug}: {hit['title']} [{hit['license']}]")
        except Exception as e:  # noqa: BLE001 — a single subject failing is fine
            print(f"  FAIL  {slug}: {e}")
        time.sleep(1.2)
    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    write_sources(meta)
    n = len([s for s in SUBJECTS if (BASE_DIR / (s['slug'] + '.jpg')).exists() and s['slug'] in meta])
    print(f"downloaded bases: {n}/{len(SUBJECTS)}")
    return n


def write_sources(meta):
    lines = [
        "# Fixture image sources",
        "",
        "All base images are CC0 or public-domain files from Wikimedia Commons;",
        "license verified programmatically via the Commons `extmetadata` API at",
        "download time. Derived test cases (resizes, crops, synthetic watermarks)",
        "are transformations of these bases made by `make_fixtures.py`.",
        "",
        "| slug | Commons file | author | license | source |",
        "|------|--------------|--------|---------|--------|",
    ]
    for slug in sorted(meta):
        m = meta[slug]
        artist = m["artist"][:60].replace("|", "/")
        lines.append(f"| {slug} | {m['title'].replace('|', '/')} | {artist} | {m['license']} | {m['url']} |")
    SOURCES_PATH.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- derivation

def load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def derive_dup(img, op, rng):
    """One derived variant that a human would call 'the same photo'."""
    w, h = img.size
    if "resize" in op:
        f = rng.uniform(0.5, 0.7)
        img = img.resize((int(w * f), int(h * f)), Image.LANCZOS)
    if "crop" in op:
        f = rng.uniform(0.10, 0.30)  # crop away 10-30% of each dimension
        w2, h2 = img.size
        dx, dy = int(w2 * f / 2), int(h2 * f / 2)
        img = img.crop((dx, dy, w2 - dx, h2 - dy))
    if "color" in op:
        img = ImageEnhance.Color(img).enhance(rng.uniform(0.85, 1.15))
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.92, 1.08))
    quality = 40 if "reencode" in op else 88
    return img, quality


def add_watermark(img, style, rng):
    img = img.convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if style == "corner":
        text = rng.choice(["© STOCKPHOTO.COM", "© PHOTOBANK 2024", "SAMPLE IMAGE"])
        font = load_font(max(24, h // 22))
        tw = draw.textlength(text, font=font)
        draw.text((w - tw - w // 40, h - h // 22 - h // 40), text,
                  font=font, fill=(255, 255, 255, 200), stroke_width=2,
                  stroke_fill=(0, 0, 0, 140))
    elif style == "diagonal":
        band = Image.new("RGBA", (int((w**2 + h**2) ** 0.5), h // 6), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(band)
        font = load_font(max(30, h // 12))
        text = "  SAMPLE   •   SAMPLE   •   SAMPLE  "
        bdraw.text((0, band.height // 6), text * 3, font=font, fill=(255, 255, 255, 130))
        band = band.rotate(30, expand=True)
        overlay.alpha_composite(band, (-(band.width - w) // 2, (h - band.height) // 2))
    else:  # tile
        font = load_font(max(20, h // 30))
        text = "stockphoto"
        step_x, step_y = w // 3, h // 5
        for i, y in enumerate(range(0, h, step_y)):
            for x in range(-step_x // 2 if i % 2 else 0, w, step_x):
                draw.text((x, y), text, font=font, fill=(255, 255, 255, 95),
                          stroke_width=1, stroke_fill=(0, 0, 0, 60))
    img.alpha_composite(overlay)
    return img.convert("RGB")


def pick_base(rng, usage, exclude=()):
    """Least-used base first (keeps bundles small); seeded tiebreak."""
    avail = [s for s in usage if s not in exclude]
    low = min(usage[s] for s in avail)
    pool = sorted(s for s in avail if usage[s] == low)
    return rng.choice(pool)


def derive_cases():
    subj = {s["slug"]: s for s in SUBJECTS}
    meta = json.loads(META_PATH.read_text())
    slugs = sorted(s for s in meta if (BASE_DIR / f"{s}.jpg").exists() and s in subj)
    if len(slugs) < 40:
        sys.exit(f"only {len(slugs)} base images; need >=40 — rerun --download")

    rng = random.Random(SEED)
    usage = {s: 0 for s in slugs}
    cases = []
    if CASES_DIR.exists():
        for p in sorted(CASES_DIR.rglob("*")):
            if p.is_file():
                p.unlink()

    def save(img, rel, quality=88):
        out = CASES_DIR / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=quality)
        return f"cases/{rel}"

    def entry(path, loc, cap, page, slot):
        return {"image": path, "claimed_location": loc, "caption": cap,
                "page": page, "slot": slot}

    def new_case(cat, cid, base, entries, expected, hard=False):
        bundle = usage[base]
        usage[base] += 1
        cases.append({"id": cid, "category": cat, "base": base, "bundle": bundle,
                      "hard": hard, "entries": entries, "expected": expected})

    # --- 30 duplicate pairs -------------------------------------------------
    for i in range(30):
        base = pick_base(rng, usage)
        s = subj[base]
        op = DUP_OPS[i % len(DUP_OPS)]
        img = Image.open(BASE_DIR / f"{base}.jpg")
        cid = f"dup_{i:03d}"
        orig = save(img, f"{cid}/original.jpg")
        variant, q = derive_dup(img.copy(), op, rng)
        var = save(variant, f"{cid}/variant_{op.replace('+', '_')}.jpg", quality=q)
        new_case("duplicate", cid, base, [
            entry(orig, s["loc"], s["cap"], f"{cid}-page-a.html", "hero"),
            entry(var, s["loc"], s["cap"], f"{cid}-page-b.html", "gallery-1"),
        ], {"verdict": "REJECT", "check": "c1", "note": f"derived by {op}"})

    # --- 20 watermark cases -------------------------------------------------
    for i in range(20):
        base = pick_base(rng, usage)
        s = subj[base]
        style = WM_STYLES[i % len(WM_STYLES)]
        img = Image.open(BASE_DIR / f"{base}.jpg")
        cid = f"wm_{i:03d}"
        path = save(add_watermark(img, style, rng), f"{cid}/{style}.jpg")
        new_case("watermark", cid, base, [
            entry(path, s["loc"], s["cap"], f"{cid}-page.html", "hero"),
        ], {"verdict": "REJECT", "check": "c2", "note": f"synthetic {style} watermark"})

    # --- 25 location mismatches (5 hard) ------------------------------------
    loc_pairs = []
    for img_slug, claimed_slug in HARD_LOC_PAIRS:
        if img_slug in usage and claimed_slug in subj:
            loc_pairs.append((img_slug, claimed_slug, True))
    while len(loc_pairs) < 25:
        img_slug = pick_base(rng, usage, exclude=[p[0] for p in loc_pairs])
        candidates = [s for s in slugs
                      if subj[s]["klass"] != subj[img_slug]["klass"] and s != img_slug]
        loc_pairs.append((img_slug, rng.choice(sorted(candidates)), False))
    for i, (img_slug, claimed_slug, hard) in enumerate(loc_pairs):
        s_img, s_claim = subj[img_slug], subj[claimed_slug]
        img = Image.open(BASE_DIR / f"{img_slug}.jpg")
        cid = f"loc_{i:03d}"
        path = save(img, f"{cid}/photo.jpg")
        new_case("location_mismatch", cid, img_slug, [
            entry(path, s_claim["loc"], s_img["cap"], f"{cid}-page.html", "hero"),
        ], {"verdict": "REJECT", "check": "c3",
            "note": f"photo of {s_img['loc']} labeled {s_claim['loc']}"}, hard=hard)

    # --- 25 clean controls ---------------------------------------------------
    for i in range(25):
        base = pick_base(rng, usage)
        s = subj[base]
        img = Image.open(BASE_DIR / f"{base}.jpg")
        cid = f"clean_{i:03d}"
        path = save(img, f"{cid}/photo.jpg")
        new_case("clean", cid, base, [
            entry(path, s["loc"], s["cap"], f"{cid}-page.html", "hero"),
        ], {"verdict": "KEEP", "check": None, "note": "control — must not reject"})

    # --- 20 caption mismatches ----------------------------------------------
    for i in range(20):
        base = pick_base(rng, usage)
        s = subj[base]
        others = sorted(x for x in slugs if subj[x]["klass"] != s["klass"])
        wrong = subj[rng.choice(others)]
        img = Image.open(BASE_DIR / f"{base}.jpg")
        cid = f"cap_{i:03d}"
        path = save(img, f"{cid}/photo.jpg")
        new_case("caption_mismatch", cid, base, [
            entry(path, s["loc"], wrong["cap"], f"{cid}-page.html", "hero"),
        ], {"verdict": "REJECT", "check": "c4",
            "note": f"caption describes {wrong['slug']}, image is {base}"})

    truth = {
        "seed": SEED,
        "counts": {c: sum(1 for x in cases if x["category"] == c)
                   for c in ("duplicate", "watermark", "location_mismatch", "clean", "caption_mismatch")},
        "bundles": max(c["bundle"] for c in cases) + 1,
        "cases": cases,
    }
    assert len(cases) == 120, f"expected 120 cases, got {len(cases)}"
    for b in range(truth["bundles"]):
        seen = [c["base"] for c in cases if c["bundle"] == b]
        assert len(seen) == len(set(seen)), f"bundle {b} reuses a base image"
    TRUTH_PATH.write_text(json.dumps(truth, indent=2))
    print(f"cases: {len(cases)}  counts: {truth['counts']}  bundles: {truth['bundles']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--derive", action="store_true")
    args = ap.parse_args()
    do_all = not (args.download or args.derive)
    if args.download or do_all:
        n = download_bases()
        if n < 40:
            sys.exit(f"only {n} bases downloaded; need >=40")
    if args.derive or do_all:
        derive_cases()


if __name__ == "__main__":
    main()
