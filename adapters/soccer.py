"""Association-football (soccer) adapter — reads a JSON cache scraped from a
self-hosted open-football instance and hands the app the same shapes the
SQLite adapters do (recent scores / standings / leader boards / game detail).

open-football serves only server-rendered HTML, so this module also owns the
scraper (:func:`refresh`), which ``sync.py`` runs on the shared timer. Page
renders only ever touch the cache file (``soccer_feeds.cache_path()``); they
never hit the network. If the source is unreachable, the last good cache is
kept and pages degrade to a placeholder rather than crashing.

Cache JSON shape::

    {"synced_at": "...UTC", "base_url": "...",
     "leagues": [
       {"slug", "label", "tier",
        "standings": [{"pos","team","team_slug","played","won","drawn",
                       "lost","gd","points"}],
        "matches":   [{"match_id","date","home","home_slug","away",
                       "away_slug","home_goals","away_goals","played"}],
        "scorers":   [{"name","team","goals"}]}]}

Scraping uses requests+BeautifulSoup when present, falling back to stdlib
urllib + a defensive regex parser so the hub still works in a bare sandbox.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from html import unescape
from typing import Any

from adapters import soccer_feeds

log = logging.getLogger("vroomtv.soccer")

# The open-football scoreline separator is an en dash (–, U+2013); be liberal
# and also accept hyphen / em dash.
_DASHES = "–—-"
_SCORE_RE = re.compile(r"(\d+)\s*[" + _DASHES + r"]\s*(\d+)")

# ── HTTP ──────────────────────────────────────────────────────────────────


def _fetch(url: str, timeout: int = 30) -> str | None:
    """GET a page, returning text or None. Prefers requests; falls back to
    urllib so the scraper still runs without third-party deps."""
    try:
        import requests  # type: ignore

        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "vroomtv-soccer/1.0"})
        if resp.status_code != 200:
            log.info("soccer fetch %s -> HTTP %s", url, resp.status_code)
            return None
        return resp.text
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 — network errors must not crash sync
        log.info("soccer fetch %s failed: %s", url, e)
        return None
    # urllib fallback
    try:
        import urllib.request

        req = urllib.request.Request(
            url, headers={"User-Agent": "vroomtv-soccer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log.info("soccer fetch %s failed: %s", url, e)
        return None


# ── Parsing ───────────────────────────────────────────────────────────────


def _soup(html: str):
    try:
        from bs4 import BeautifulSoup  # type: ignore

        return BeautifulSoup(html, "html.parser")
    except ImportError:
        return None


def _slug_from_href(href: str) -> str:
    # /en/teams/arsenal -> arsenal ; /en/match/123 -> 123
    return href.rstrip("/").rsplit("/", 1)[-1] if href else ""


def _int(txt: str, default: int = 0) -> int:
    m = re.search(r"-?\d+", txt or "")
    return int(m.group()) if m else default


def _parse_league(html: str) -> dict:
    """Return {"standings": [...], "matches": [...]} from a league page.

    open-football renders the table as <table class="fm-standings"> and the
    fixtures as <div class="fm-fixture"> rows (with .fx-home/.fx-score/
    .fx-away). We parse with BeautifulSoup when available, else regex.
    """
    soup = _soup(html)
    if soup is not None:
        return {"standings": _parse_standings_bs(soup),
                "matches": _parse_matches_bs(soup)}
    return {"standings": _parse_standings_re(html),
            "matches": _parse_matches_re(html)}


def _parse_standings_bs(soup) -> list[dict]:
    rows = []
    table = soup.find("table", class_="fm-standings")
    if not table:
        return rows
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 8:
            continue
        link = tr.find("a", href=True)
        team = link.get_text(strip=True) if link else \
            cells[1].get_text(strip=True)
        team_slug = _slug_from_href(link["href"]) if link else ""
        rows.append({
            "pos": _int(cells[0].get_text()),
            "team": team, "team_slug": team_slug,
            "played": _int(cells[2].get_text()),
            "won": _int(cells[3].get_text()),
            "drawn": _int(cells[4].get_text()),
            "lost": _int(cells[5].get_text()),
            "gd": _int(cells[6].get_text()),
            "points": _int(cells[7].get_text()),
        })
    return rows


def _parse_matches_bs(soup) -> list[dict]:
    out = []
    for fx in soup.find_all("div", class_="fm-fixture"):
        home_el = fx.find(class_="fx-home")
        away_el = fx.find(class_="fx-away")
        score_el = fx.find(class_="fx-score")
        if not (home_el and away_el and score_el):
            continue
        home_link = home_el.find("a", href=True)
        away_link = away_el.find("a", href=True)
        score_txt = score_el.get_text(" ", strip=True)
        m = _SCORE_RE.search(score_txt)
        match_id = ""
        if score_el.has_attr("href"):
            match_id = _slug_from_href(score_el["href"])
        out.append({
            "match_id": match_id,
            "home": home_el.get_text(strip=True),
            "home_slug": _slug_from_href(home_link["href"]) if home_link else "",
            "away": away_el.get_text(strip=True),
            "away_slug": _slug_from_href(away_link["href"]) if away_link else "",
            "home_goals": int(m.group(1)) if m else None,
            "away_goals": int(m.group(2)) if m else None,
            "played": bool(m),
        })
    return out


# Regex fallbacks — used only when BeautifulSoup isn't installed. Defensive:
# tolerant of whitespace, attribute order, and missing scores.
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)
_A_RE = re.compile(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return unescape(_TAG_RE.sub("", fragment or "")).replace("\xa0", " ").strip()


def _parse_standings_re(html: str) -> list[dict]:
    m = re.search(r'<table[^>]*class="[^"]*fm-standings[^"]*"[^>]*>(.*?)</table>',
                  html, re.S | re.I)
    if not m:
        return []
    rows = []
    for tr in _TR_RE.findall(m.group(1)):
        cells = _TD_RE.findall(tr)
        if len(cells) < 8:
            continue
        link = _A_RE.search(cells[1])
        team = _text(link.group(2)) if link else _text(cells[1])
        team_slug = _slug_from_href(link.group(1)) if link else ""
        rows.append({
            "pos": _int(_text(cells[0])), "team": team, "team_slug": team_slug,
            "played": _int(_text(cells[2])), "won": _int(_text(cells[3])),
            "drawn": _int(_text(cells[4])), "lost": _int(_text(cells[5])),
            "gd": _int(_text(cells[6])), "points": _int(_text(cells[7])),
        })
    return rows


_FIXTURE_RE = re.compile(
    r'<div[^>]*class="[^"]*fm-fixture\b[^"]*"[^>]*>(.*?)</div>\s*</div>',
    re.S | re.I)


def _parse_matches_re(html: str) -> list[dict]:
    out = []
    # Split on each fixture block; a block holds fx-home, fx-score, fx-away.
    for chunk in re.split(r'class="[^"]*fm-fixture\b', html)[1:]:
        block = chunk[:1200]
        home = re.search(r'fx-home"[^>]*>(.*?)</div>', block, re.S | re.I)
        away = re.search(r'fx-away"[^>]*>(.*?)</div>', block, re.S | re.I)
        score = re.search(r'fx-score"(.*?)</a>', block, re.S | re.I)
        if not (home and away and score):
            continue
        home_link = _A_RE.search(home.group(1))
        away_link = _A_RE.search(away.group(1))
        href = re.search(r'href="([^"]*)"', score.group(1))
        sm = _SCORE_RE.search(_text(score.group(1)))
        out.append({
            "match_id": _slug_from_href(href.group(1)) if href else "",
            "home": _text(home.group(1)),
            "home_slug": _slug_from_href(home_link.group(1)) if home_link else "",
            "away": _text(away.group(1)),
            "away_slug": _slug_from_href(away_link.group(1)) if away_link else "",
            "home_goals": int(sm.group(1)) if sm else None,
            "away_goals": int(sm.group(2)) if sm else None,
            "played": bool(sm),
        })
    return out


def _parse_squad_scorers(html: str, team_name: str) -> list[dict]:
    """Top scorers from a team page's <table class="fm-squad"> (sq-name link,
    sq-goals cell). Best-effort; returns [] when not present."""
    soup = _soup(html)
    out = []
    if soup is not None:
        table = soup.find("table", class_="fm-squad")
        if not table:
            return out
        for tr in (table.find("tbody") or table).find_all("tr"):
            name_cell = tr.find("td", class_="sq-name")
            goals_cell = tr.find("td", class_="sq-goals")
            if not name_cell:
                continue
            goals = _int(goals_cell.get_text()) if goals_cell else 0
            if goals <= 0:
                continue
            out.append({"name": name_cell.get_text(" ", strip=True),
                        "team": team_name, "goals": goals})
        return out
    # regex fallback
    for tr in _TR_RE.findall(html):
        name = re.search(r'class="sq-name"[^>]*>(.*?)</td>', tr, re.S | re.I)
        goals = re.search(r'class="sq-goals"[^>]*>(.*?)</td>', tr, re.S | re.I)
        if not name:
            continue
        g = _int(_text(goals.group(1))) if goals else 0
        if g > 0:
            out.append({"name": _text(name.group(1)), "team": team_name, "goals": g})
    return out


# ── Refresh (scrape -> cache) ─────────────────────────────────────────────


def refresh(scorers: bool = False) -> str:
    """Scrape every configured league and write the JSON cache atomically.

    On total failure the existing cache is left untouched. Returns a short
    status string for sync.py to log. ``scorers=True`` also walks each
    league's team pages to build a top-scorers board (many extra requests;
    off by default — preseason worlds have no goals anyway)."""
    base = soccer_feeds.base_url()
    dest = soccer_feeds.cache_path()
    leagues_out = []
    ok = 0
    for feed in soccer_feeds.feeds():
        url = f"{base}/en/leagues/{feed['slug']}"
        html = _fetch(url)
        if html is None:
            continue
        parsed = _parse_league(html)
        league = {"slug": feed["slug"], "label": feed["label"],
                  "tier": feed.get("tier", "Pro"),
                  "standings": parsed["standings"],
                  "matches": parsed["matches"], "scorers": []}
        if scorers and parsed["standings"]:
            tally: dict[str, dict] = {}
            for row in parsed["standings"]:
                if not row.get("team_slug"):
                    continue
                t_html = _fetch(f"{base}/en/teams/{row['team_slug']}")
                if not t_html:
                    continue
                for s in _parse_squad_scorers(t_html, row["team"]):
                    key = (s["name"], s["team"])
                    tally[key] = s
            league["scorers"] = sorted(
                tally.values(), key=lambda s: -s["goals"])[:15]
        leagues_out.append(league)
        if parsed["standings"] or parsed["matches"]:
            ok += 1

    if not leagues_out:
        return "error: source unreachable (kept last cache)"

    blob = {
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "base_url": base, "leagues": leagues_out,
    }
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(blob, out)
        os.replace(tmp, dest)  # atomic — readers never see a partial file
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return f"ok ({ok}/{len(leagues_out)} leagues with data)"


# ── Cache reads ───────────────────────────────────────────────────────────

_cache: dict = {"key": None, "data": None}


def _load() -> dict:
    """Read the JSON cache, memoised against its mtime. Returns {} when the
    cache is absent or unreadable (pages then show a placeholder)."""
    path = soccer_feeds.cache_path()
    try:
        key = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache["key"] == key and _cache["data"] is not None:
        return _cache["data"]
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    _cache["key"] = key
    _cache["data"] = data
    return data


def configured() -> bool:
    """Soccer is 'on' once it has a cache file to read. (The scrape always
    has a default base URL + leagues, so configuration is just the cache.)"""
    return bool(_load().get("leagues"))


def _leagues() -> list[dict]:
    return _load().get("leagues", [])


def last_synced() -> str | None:
    return _load().get("synced_at")


def get_recent_scores(limit_per_league: int = 8) -> list[dict]:
    """Recently played matches across all leagues, newest first within each
    league (open-football lists fixtures chronologically, so reverse)."""
    out = []
    for lg in _leagues():
        played = [m for m in lg.get("matches", []) if m.get("played")]
        for m in list(reversed(played))[:limit_per_league]:
            out.append({
                "slug": lg["slug"], "league": lg["label"], "tier": lg.get("tier", "Pro"),
                "match_id": m.get("match_id", ""), "date": m.get("date", ""),
                "home_name": m["home"], "away_name": m["away"],
                "home_score": m.get("home_goals") or 0,
                "away_score": m.get("away_goals") or 0,
            })
    return out


def get_standings() -> list[dict]:
    """One entry per league: {label, tier, teams:[...]} (app catalog shape)."""
    out = []
    for lg in _leagues():
        if not lg.get("standings"):
            continue
        out.append({"league": lg["label"], "slug": lg["slug"],
                    "tier": lg.get("tier", "Pro"),
                    "teams": lg["standings"]})
    return out


def get_leader_boards() -> list[dict]:
    """Top-scorer board per league (when scorer data was scraped):
    [{label, tier, boards:[...]}] matching the generic /leaders template."""
    out = []
    for lg in _leagues():
        scorers = lg.get("scorers") or []
        if not scorers:
            continue
        rows = [{"name": s["name"], "team": s.get("team", ""),
                 "goals": s.get("goals", 0)} for s in scorers]
        out.append({"label": lg["label"], "tier": lg.get("tier", "Pro"),
                    "boards": [{"title": "Goals", "sort": "goals",
                                "cols": [("G", "goals", None)], "rows": rows}]})
    return out


def get_game_detail(slug: str, match_id: str) -> dict[str, Any] | None:
    """Match detail for /game/soccer/<slug>/<match_id>. Built entirely from
    the cached league page (open-football match pages aren't scraped), so we
    show the scoreline + the league table as a ladder."""
    lg = next((l for l in _leagues() if l["slug"] == slug), None)
    if not lg:
        return None
    match = next((m for m in lg.get("matches", [])
                  if str(m.get("match_id", "")) == str(match_id)), None)
    if not match:
        return None
    return {
        "league_label": lg["label"], "slug": slug,
        "match": match,
        "home": match["home"], "away": match["away"],
        "home_goals": match.get("home_goals") or 0,
        "away_goals": match.get("away_goals") or 0,
        "ladder": lg.get("standings", []),
        "source_url": f"{_load().get('base_url', soccer_feeds.base_url())}"
                      f"/en/match/{match_id}" if match_id else "",
    }
