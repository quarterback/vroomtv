"""Shared loader for ZenGM League-File (JSON export) adapters.

ZenGM (Hockey GM, Basketball GM, …) has no live, token-protected export
endpoint like the other sims do — instead the user manually exports a
League File (in-game: Tools > Export League, **with Box Scores**) and the
JSON is uploaded to the hub (see /upload/<sport> in app.py). This module
loads that file once and caches it keyed on the file's mtime — the same
trick the SQLite adapters use — so a multi-megabyte league file is parsed
once per upload, not once per page view.
"""
from __future__ import annotations

import json
import os
import threading

# env-var -> (mtime, parsed-json)
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def load(env_var: str) -> dict | None:
    """Parsed league file at ``$<env_var>``, cached on mtime. Returns None
    when the var is unset, the file is missing, or the JSON won't parse."""
    path = os.environ.get(env_var)
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        hit = _cache.get(env_var)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    with _lock:
        _cache[env_var] = (mtime, data)
    return data


def ga(league: dict, key: str, default=None):
    """A ``gameAttributes`` value. ZenGM stores these either as a plain
    ``{key: value}`` mapping or, in some exports, as a list of
    ``{start, value}`` history rows — return the latest value either way."""
    attrs = league.get("gameAttributes") or {}
    val = attrs.get(key, default)
    if isinstance(val, list) and val and isinstance(val[0], dict) and "value" in val[0]:
        return val[-1]["value"]
    return val


def current_season(league: dict) -> int:
    """Latest season with games on record (falls back to the save's season)."""
    games = league.get("games") or []
    if games:
        return max(g["season"] for g in games)
    return ga(league, "season", 0) or 0


def team_index(league: dict) -> dict[int, dict]:
    """``tid`` -> display identity {name, abbrev, region, conf, division}."""
    confs = {c["cid"]: c["name"] for c in (ga(league, "confs") or [])}
    divs = {d["did"]: d["name"] for d in (ga(league, "divs") or [])}
    out: dict[int, dict] = {}
    for t in league.get("teams") or []:
        out[t["tid"]] = {
            "tid": t["tid"],
            "name": f"{t.get('region', '')} {t.get('name', '')}".strip(),
            "abbrev": t.get("abbrev", ""),
            "region": t.get("region", ""),
            "conf": confs.get(t.get("cid"), ""),
            "division": divs.get(t.get("did"), ""),
            "imgURL": t.get("imgURL", ""),
        }
    return out


def league_label(league: dict, fallback: str = "League") -> str:
    """A short name for the whole league. With one conference we use its
    single division's name (PWHL's "PWHL"), which reads better as a tab than
    the long conference name; otherwise the conference name."""
    confs = ga(league, "confs") or []
    divs = ga(league, "divs") or []
    if len(confs) == 1:
        if len(divs) == 1:
            return divs[0].get("name") or confs[0].get("name") or fallback
        return confs[0].get("name") or fallback
    return fallback


def duel(stats_a: dict, stats_b: dict, pairs: list) -> list[dict]:
    """ABC-style head-to-head bars: [{label, a, b, a_pct}] for each stat
    present on either side (a = away, b = home, matching the templates)."""
    out = []
    for label, key in pairs:
        a, b = stats_a.get(key), stats_b.get(key)
        if a is None and b is None:
            continue
        a, b = float(a or 0), float(b or 0)
        total = a + b
        out.append({"label": label, "a": f"{a:g}", "b": f"{b:g}",
                    "a_pct": round(100 * a / total) if total else 50})
    return out


def clock_mmss(clock: float) -> str:
    """ZenGM stores a goal's clock as *minutes remaining* in the period
    (0–periodLength). Render it as M:SS."""
    try:
        minutes = int(clock)
        seconds = int(round((float(clock) - minutes) * 60))
        if seconds == 60:
            minutes, seconds = minutes + 1, 0
        return f"{minutes}:{seconds:02d}"
    except (TypeError, ValueError):
        return ""
