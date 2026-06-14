"""Registry of ZenGM league feeds (one uploaded league file per league).

Each sport tab on the hub can carry several leagues (a dropdown), the same
way baseball/tennis/viperball do — e.g. Hockey = NHL + PWHL, pro Basketball
= NBA + WNBA, with men's/women's college basketball as their own sport tabs,
and softball on its own. Every league is one ZenGM League File uploaded to
``PUT /upload/<key>`` and read from ``$<env>``.

To add or rename a league: edit ``FEEDS``. Nothing lights up until that
league's file is actually uploaded (``enabled()`` filters to files present),
so unused rows are harmless placeholders.

Each feed dict: {key, sport, league, env, engine}. ``engine`` selects the
adapter + standings kind + box-score template (see ``ENGINES``). ``key`` is
the URL slug for the game page (/game/zg/<key>/<gid>) and the upload route.
"""
from __future__ import annotations

import os

from adapters import basketball, zengm_common, zengm_rink

# engine -> {module, standings kind, game template}. The module exposes the
# shared feed-cfg surface: recent_scores/standings/leader_boards/game_detail/
# league_label.
ENGINES = {
    "rink": {"module": zengm_rink, "kind": zengm_rink.STANDINGS_KIND,
             "template": zengm_rink.GAME_TEMPLATE},
    "basketball": {"module": basketball, "kind": basketball.STANDINGS_KIND,
                   "template": basketball.GAME_TEMPLATE},
}

FEEDS = [
    # ── Hockey engine (ZGMH) ──────────────────────────────────────────────
    {"key": "nhl", "sport": "Hockey", "league": "NHL",
     "env": "NHL_LEAGUE_FILE", "engine": "rink"},
    {"key": "pwhl", "sport": "Hockey", "league": "PWHL",
     "env": "PWHL_LEAGUE_FILE", "engine": "rink"},
    {"key": "box-lacrosse", "sport": "Box Lacrosse", "league": "NLL",
     "env": "BOX_LACROSSE_LEAGUE_FILE", "engine": "rink"},
    {"key": "indoor-soccer", "sport": "Indoor Soccer", "league": "MASL",
     "env": "INDOOR_SOCCER_LEAGUE_FILE", "engine": "rink"},
    {"key": "floorball", "sport": "Floorball", "league": "Floorball",
     "env": "FLOORBALL_LEAGUE_FILE", "engine": "rink"},
    # ── Basketball engine (BBGM) ──────────────────────────────────────────
    {"key": "nba", "sport": "Basketball", "league": "NBA",
     "env": "NBA_LEAGUE_FILE", "engine": "basketball"},
    {"key": "wnba", "sport": "Basketball", "league": "WNBA",
     "env": "WNBA_LEAGUE_FILE", "engine": "basketball"},
    {"key": "cbb-men", "sport": "Men's College Basketball", "league": "NCAA",
     "env": "CBB_MEN_LEAGUE_FILE", "engine": "basketball"},
    {"key": "cbb-women", "sport": "Women's College Basketball", "league": "NCAA",
     "env": "CBB_WOMEN_LEAGUE_FILE", "engine": "basketball"},
]

# Sport-tab display order on /scores, /standings, /leaders.
SPORT_ORDER = ["Hockey", "Basketball", "Men's College Basketball",
               "Women's College Basketball", "Box Lacrosse",
               "Indoor Soccer", "Floorball"]


def enabled() -> list[dict]:
    """Feeds whose league file is present on disk."""
    out = []
    for f in FEEDS:
        path = os.environ.get(f["env"])
        if path and os.path.exists(path):
            out.append(f)
    return out


def by_key(key: str) -> dict | None:
    return next((f for f in FEEDS if f["key"] == key), None)


def module(feed: dict):
    return ENGINES[feed["engine"]]["module"]


def clickable(feed: dict) -> bool:
    """Whether this feed's games link to a box-score page (small files only;
    big leagues are compacted to scores-only and shown unlinked)."""
    return zengm_common.has_box(feed["env"])


def template(feed: dict) -> str:
    return ENGINES[feed["engine"]]["template"]


def sports() -> list[str]:
    """Distinct sports with at least one enabled feed, in display order."""
    have = {f["sport"] for f in enabled()}
    ordered = [s for s in SPORT_ORDER if s in have]
    return ordered + [s for s in have if s not in ordered]


def feeds_for(sport: str) -> list[dict]:
    return [f for f in enabled() if f["sport"] == sport]
