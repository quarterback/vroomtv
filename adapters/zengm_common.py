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

# Hub policy: per-game box-score detail is never retained — there's no reason
# to keep that content. Every league is projected to scores + team records +
# season stats (see _project), so games show results everywhere but aren't
# clickable. Flip KEEP_BOX_SCORES to True (and tune the size cap) only if you
# ever want clickable box-score pages for small leagues again.
KEEP_BOX_SCORES = False
KEEP_BOX_MAX_BYTES = 120_000_000

# env-var -> (mtime, lean-league)
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _file_size(env_var: str) -> int | None:
    path = os.environ.get(env_var)
    if not path or not os.path.exists(path):
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def has_box(env_var: str) -> bool:
    """Whether this feed's games should be clickable — only when box scores
    are retained at all (off by hub policy) and the file is small enough."""
    if not KEEP_BOX_SCORES:
        return False
    size = _file_size(env_var)
    return size is not None and size <= KEEP_BOX_MAX_BYTES


def _project(d: dict, keep_box: bool) -> dict:
    """Compact a parsed league to just what the adapters read, so the big
    raw dict can be freed. Always keeps game *scores*, team records, and the
    current season's player stats; keeps per-game player box lines (and goal
    summaries) only when ``keep_box``. Shapes match what the adapters expect."""
    season = current_season(d)
    ga = d.get("gameAttributes") or {}
    lean_ga = {k: ga[k] for k in ("confs", "divs", "season", "numGames",
                                  "phase", "startingSeason") if k in ga}
    teams = [{k: t.get(k) for k in ("tid", "cid", "did", "region", "name",
                                    "abbrev", "imgURL", "colors", "disabled",
                                    "seasons")}
             for t in d.get("teams") or []]
    players = [{"pid": p.get("pid"), "firstName": p.get("firstName"),
                "lastName": p.get("lastName"),
                "stats": [s for s in (p.get("stats") or [])
                          if s.get("season") == season]}
               for p in d.get("players") or []]
    games = []
    for g in d.get("games") or []:
        lg = {k: g.get(k) for k in ("gid", "day", "season", "playoffs",
                                    "overtimes", "numPeriods", "won", "lost")}
        gt = g.get("teams") or []
        if keep_box:
            lg["teams"] = gt
            lg["scoringSummary"] = g.get("scoringSummary")
        else:
            lg["teams"] = [{"tid": t.get("tid"), "pts": t.get("pts")} for t in gt]
        games.append(lg)
    return {"meta": d.get("meta"), "gameAttributes": lean_ga,
            "teams": teams, "players": players, "games": games}


def load(env_var: str) -> dict | None:
    """Compacted league file at ``$<env_var>``, cached on mtime. Returns None
    when the var is unset, the file is missing, or the JSON won't parse.

    The raw export is parsed once per upload and immediately projected down
    to the lean shape (see ``_project``); only the lean copy is cached."""
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
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    keep_box = KEEP_BOX_SCORES and os.path.getsize(path) <= KEEP_BOX_MAX_BYTES
    lean = _project(raw, keep_box=keep_box)
    del raw
    with _lock:
        _cache[env_var] = (mtime, lean)
    return lean


def project_file(src_path: str, dst_path: str) -> bool:
    """Parse a raw ZenGM export and write the lean projection to ``dst_path``
    (atomically), so box-score bulk never persists on disk. Used by the upload
    route: a 266 MB export lands as a few-MB lean file. Returns False if the
    source won't parse as JSON (caller should fall back to the raw bytes)."""
    try:
        with open(src_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    keep_box = KEEP_BOX_SCORES and os.path.getsize(src_path) <= KEEP_BOX_MAX_BYTES
    lean = _project(raw, keep_box=keep_box)
    del raw
    tmp = dst_path + ".lean.tmp"
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lean, fh, separators=(",", ":"))
    os.replace(tmp, dst_path)
    return True


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
    """The season to display. With box scores, the latest season that has
    games. Without them (e.g. a big college league exported without box
    scores to save memory), the latest season a team actually played —
    `gameAttributes.season` can point at an empty upcoming season in an
    offseason export, which would blank the standings."""
    games = league.get("games") or []
    if games:
        return max(g["season"] for g in games)
    best = None
    for t in league.get("teams") or []:
        for s in t.get("seasons") or []:
            if (s.get("won", 0) or 0) + (s.get("lost", 0) or 0) + \
               (s.get("tied", 0) or 0) + (s.get("otl", 0) or 0) > 0:
                yr = s.get("season")
                if yr is not None and (best is None or yr > best):
                    best = yr
    return best if best is not None else (ga(league, "season", 0) or 0)


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
