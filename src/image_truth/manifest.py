"""Manifest parsing: JSON, YAML, markdown tables — incl. IMAGE_CREDITS.md.

An entry needs an image path/URL; claimed_location, caption, page, and slot
are optional. Markdown parsing is header-driven so both simple manifests and
the legacy IMAGE_CREDITS.md convention (columns like "Local path", "Subject",
"Place") work unmodified.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .model import Entry

# header aliases -> canonical field (lowercased, non-alnum stripped)
FIELD_ALIASES = {
    "image": "image", "imagepath": "image", "path": "image", "file": "image",
    "localpath": "image", "url": "image", "src": "image",
    "claimedlocation": "claimed_location", "location": "claimed_location",
    "place": "claimed_location",
    "caption": "caption", "subject": "caption", "description": "caption",
    "alt": "caption",
    "page": "page", "where": "page", "day": "page",
    "slot": "slot",
}

IMG_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|avif|tiff?|bmp)($|\?)", re.IGNORECASE)

# image-serving CDN hosts whose URLs are direct images even without an
# extension (Unsplash/Pexels/Wikimedia). Landing pages like www.pexels.com/
# photo/351/ are deliberately NOT here — they are source links, not images.
IMG_HOST_RE = re.compile(
    r"^https?://(images\.unsplash\.com|images\.pexels\.com|upload\.wikimedia\.org"
    r"|live\.staticflickr\.com|i\.imgur\.com|[^/]*\.(?:cloudfront|fastly|akamaized)\.net)/",
    re.IGNORECASE,
)


def _is_image_url(u: str) -> bool:
    return bool(IMG_EXT_RE.search(u) or IMG_HOST_RE.match(u))


def parse(path: str) -> list:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    suffix = p.suffix.lower()
    if suffix == ".json":
        entries = _parse_json(p)
    elif suffix in (".yaml", ".yml"):
        entries = _parse_yaml(p)
    elif suffix in (".md", ".markdown"):
        entries = _parse_markdown(p)
    else:
        raise ValueError(f"unsupported manifest format: {suffix} (use .md/.json/.yaml)")
    if not entries:
        raise ValueError(f"no image entries found in {path}")
    return entries


def _mk_entry(d: dict) -> Entry:
    return Entry(
        image=str(d.get("image", "")).strip(),
        claimed_location=str(d.get("claimed_location", "") or "").strip(),
        caption=str(d.get("caption", "") or "").strip(),
        page=str(d.get("page", "") or "").strip(),
        slot=str(d.get("slot", "") or "").strip(),
    )


def _normalize_keys(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        canon = FIELD_ALIASES.get(re.sub(r"[^a-z0-9]", "", str(k).lower()))
        if canon and canon not in out:
            out[canon] = v
    return out


def _rows(data) -> list:
    if isinstance(data, dict):
        data = data.get("images") or data.get("entries") or []
    if not isinstance(data, list):
        raise ValueError("manifest must be a list of entries (or {images: [...]})")
    return [d for d in data if isinstance(d, dict)]


def _parse_json(p: Path) -> list:
    data = json.loads(p.read_text())
    return [_mk_entry(_normalize_keys(d)) for d in _rows(data) if _normalize_keys(d).get("image")]


def _parse_yaml(p: Path) -> list:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("YAML manifests need PyYAML: pip install image-truth[yaml]") from exc
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML manifest: {exc}") from exc
    return [_mk_entry(_normalize_keys(d)) for d in _rows(data) if _normalize_keys(d).get("image")]


def _clean_cell(cell: str) -> str:
    """Strip markdown decoration from a table cell, keep the payload."""
    c = cell.strip()
    c = re.sub(r"!\[[^\]]*\]\(([^)]+)\)", r"\1", c)      # image -> url
    c = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\1", c)      # link -> text
    c = c.strip("`*_ ").strip()
    return c


# a cell that is *wholly* a path/URL ending in an image extension (allowing
# spaces inside the path — real directories have them, e.g. "My Trips/…")
_PATH_CELL_RE = re.compile(
    r"^(\S[^|]*?\.(?:jpe?g|png|gif|webp|avif|tiff?|bmp))(\?\S*)?$", re.IGNORECASE
)


def _cell_image_ref(cell: str):
    """Pull a local path or image URL out of a cell, if any."""
    c = cell.strip()
    m = re.search(r"!?\[[^\]]*\]\((https?://[^)]+)\)", c)   # markdown-linked URL
    if m and _is_image_url(m.group(1)):
        return m.group(1)
    c = _clean_cell(c)
    if re.match(r"^https?://\S+$", c) and _is_image_url(c):  # bare URL
        return c
    m2 = _PATH_CELL_RE.match(c)                              # local path
    if m2:
        return (m2.group(1) + (m2.group(2) or "")).strip()
    return None


def _parse_markdown(p: Path) -> list:
    """Header-mapped parsing of every table in the file."""
    entries = []
    lines = p.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # a table starts with a |...| line followed by a |---|---| separator
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            headers = [_clean_cell(h) for h in _split_row(line)]
            canon = [
                FIELD_ALIASES.get(re.sub(r"[^a-z0-9]", "", h.lower())) for h in headers
            ]
            if not any(canon):
                # not a manifest table (settings/config/etc) — don't invent entries
                i += 2
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1
                continue
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _split_row(lines[i])
                d = {}
                for field, cell in zip(canon, cells):
                    if field == "image":
                        ref = _cell_image_ref(cell)
                        if ref:
                            d["image"] = ref
                    elif field and field not in d:
                        d[field] = _clean_cell(cell)
                if "image" not in d:
                    # image may live in a non-aliased column (e.g. "Original source")
                    for cell in cells:
                        ref = _cell_image_ref(cell)
                        if ref:
                            d["image"] = ref
                            break
                if d.get("image"):
                    entries.append(_mk_entry(d))
                i += 1
        else:
            i += 1
    return entries


def _split_row(line: str) -> list:
    row = line.strip().strip("|")
    # split on pipes not escaped
    return [c for c in re.split(r"(?<!\\)\|", row)]
