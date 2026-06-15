"""Registry of association-football (soccer) leagues ingested from a
self-hosted open-football instance (ZOXEXIVO/open-football).

open-football has no JSON API — it serves server-rendered HTML — so the
soccer adapter scrapes a handful of league pages and writes one JSON cache
file (``$SOCCER_DATA``, default ``data/soccer.json``). The scrape runs on
the same ``sync.py`` timer as the SQLite feeds; pages always read the cache,
never the live site. See ``adapters/soccer.py``.

Config (env):
  SOCCER_BASE_URL   open-football base, default https://football.superinnings.com
  SOCCER_DATA       cache file path, default data/soccer.json
  SOCCER_LEAGUES    optional override: comma-separated ``slug:Label:tier``
                    items, e.g. "premier-league:Premier League:Pro,
                    bundesliga:Bundesliga:Pro". When unset, FEEDS below is
                    used.

To add or rename a league: edit ``FEEDS`` (or set SOCCER_LEAGUES). The
``slug`` is the open-football league slug (the page is /en/leagues/<slug>);
it also becomes the URL key for the hub's match page (/game/soccer/<slug>/...).
"""
from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://football.superinnings.com"

# slug -> the open-football /en/leagues/<slug> page. label is the hub's
# display name; tier slots it under the Pro/College/International tabs (all
# top-flight leagues here are "Pro").
FEEDS = [
    {"slug": "premier-league", "label": "Premier League", "tier": "Pro"},
    {"slug": "spanish-first-division", "label": "La Liga", "tier": "Pro"},
    {"slug": "bundesliga", "label": "Bundesliga", "tier": "Pro"},
    {"slug": "italian-serie-a", "label": "Serie A", "tier": "Pro"},
    {"slug": "ligue-1", "label": "Ligue 1", "tier": "Pro"},
]


def base_url() -> str:
    return os.environ.get("SOCCER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def cache_path() -> str:
    return os.environ.get("SOCCER_DATA", "data/soccer.json")


def feeds() -> list[dict]:
    """Configured leagues. SOCCER_LEAGUES overrides FEEDS when set."""
    raw = os.environ.get("SOCCER_LEAGUES", "").strip()
    if not raw:
        return list(FEEDS)
    out = []
    for item in raw.split(","):
        parts = [p.strip() for p in item.split(":")]
        if not parts or not parts[0]:
            continue
        slug = parts[0]
        label = parts[1] if len(parts) > 1 and parts[1] else slug
        tier = parts[2] if len(parts) > 2 and parts[2] else "Pro"
        out.append({"slug": slug, "label": label, "tier": tier})
    return out


def by_slug(slug: str) -> dict | None:
    return next((f for f in feeds() if f["slug"] == slug), None)
