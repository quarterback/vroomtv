"""Editor's desk — hand-filed articles that live in `desk/*.md`.

The newspaper's wire and gazette feeds are mechanical; the desk is where
a human (or an LLM acting as a reporter) drops a real story. Files are
markdown with a `---`-fenced frontmatter block; images live in
`static/desk/`. No DB, no admin UI — `git push` is the publish action.

See `desk/README.md` for the reporter-facing template and field guide.
"""
from __future__ import annotations
import os
import zlib
from typing import Any

# Folder layout (relative to repo root):
#   desk/<slug>.md          one article per file
#   static/desk/<file>      images referenced by frontmatter `image:`
_DESK_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "desk")
_STATIC_DESK_URL = "/static/desk"

_PLACEMENTS = ("lead", "featured", "rail", "wire")
_DEFAULT_PLACEMENT = "rail"

_cache: dict = {"key": None, "items": []}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (meta, body). Frontmatter is a `---`-fenced block of
    `key: value` lines at the very top — no YAML parser dependency,
    just one level of flat string keys, which is all the template needs.
    Unknown keys are kept verbatim so the loader doesn't have to be
    updated when we add new fields."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip()
        i += 1
    body = "\n".join(lines[i + 1:]).lstrip("\n") if i < len(lines) else ""
    return meta, body


def _paragraphs(body: str) -> list[dict]:
    """Split markdown body into rendered blocks. Two flavors only —
    `## heading` and paragraph — because that's what the gazette renders
    today and we want the article template to stay shared."""
    blocks = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("## "):
            blocks.append({"kind": "h2", "text": chunk[3:].strip()})
        elif chunk.startswith("# "):
            blocks.append({"kind": "h2", "text": chunk[2:].strip()})
        else:
            blocks.append({"kind": "p", "text": chunk})
    return blocks


def _slug_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _load_one(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    meta, body = _parse_frontmatter(raw)
    headline = meta.get("headline") or meta.get("title")
    if not headline:
        return None  # an untitled file is a draft — skip it silently
    slug = _slug_from_path(path)
    blocks = _paragraphs(body)
    lede = next((b["text"] for b in blocks if b["kind"] == "p"), "")
    placement = (meta.get("placement") or _DEFAULT_PLACEMENT).lower()
    if placement not in _PLACEMENTS:
        placement = _DEFAULT_PLACEMENT
    image = meta.get("image", "").strip()
    image_url = f"{_STATIC_DESK_URL}/{image}" if image else None
    return {
        "slug": slug,
        "headline": headline,
        "dek": meta.get("dek", ""),
        "byline": meta.get("byline", "Staff"),
        "kicker": meta.get("kicker", ""),
        "sport": meta.get("sport", "Desk"),
        "placement": placement,
        "published_at": meta.get("published_at", ""),
        "image_url": image_url,
        "image_alt": meta.get("image_alt", ""),
        "lede": lede,
        "blocks": blocks,
        # Newsroom uses crc32 for its pixel-art fallback; reuse it so an
        # imageless desk article still gets a deterministic stand-in.
        "art_seed": zlib.crc32(slug.encode()),
        "url": f"/news/desk/{slug}",
    }


def _cache_key() -> tuple:
    """Cache invalidates when any desk file is added, removed, or
    touched. Cheap because the directory is small and reads are local."""
    if not os.path.isdir(_DESK_DIR):
        return ()
    stamps = []
    for name in sorted(os.listdir(_DESK_DIR)):
        if not name.endswith(".md") or name.startswith("_") or name == "README.md":
            continue
        try:
            stamps.append((name, os.path.getmtime(os.path.join(_DESK_DIR, name))))
        except OSError:
            continue
    return tuple(stamps)


def all_articles() -> list[dict]:
    """Every published article in the desk folder, freshest first."""
    key = _cache_key()
    if _cache["key"] == key:
        return _cache["items"]
    items: list[dict] = []
    if os.path.isdir(_DESK_DIR):
        for name in os.listdir(_DESK_DIR):
            if not name.endswith(".md") or name.startswith("_") or name == "README.md":
                continue
            item = _load_one(os.path.join(_DESK_DIR, name))
            if item:
                items.append(item)
    items.sort(key=lambda a: a["published_at"], reverse=True)
    _cache["key"] = key
    _cache["items"] = items
    return items


def by_placement(slot: str) -> list[dict]:
    """Desk articles tagged for a specific slot on the front page."""
    return [a for a in all_articles() if a["placement"] == slot]


def get(slug: str) -> dict[str, Any] | None:
    for a in all_articles():
        if a["slug"] == slug:
            return a
    return None
