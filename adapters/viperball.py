"""Read-only adapter for viperball SQLite database (JSON blob store).

Two kinds of viperball data live in the saves table:
  - pro leagues: one `pro_league` blob per save with the whole season
  - college seasons: one `box_score` blob PER GAME (the live season object
    is memory-only in the sim; only box scores persist), keyed
    `{session}__w{week}__{away}_at_{home}`

A college season means hundreds of ~200KB blobs, so scores/standings/
leaders are built in one pass and cached against the DB file's mtime —
the file only changes when the hub re-syncs.
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
from typing import Any


def _conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_path() -> str | None:
    return os.environ.get("VIPERBALL_DB") or None


def _num(x):
    """Scores come out of the JSON blob as floats; show 37 rather than 37.0."""
    try:
        return int(x) if float(x).is_integer() else x
    except (TypeError, ValueError):
        return x


def _load_leagues(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT save_key, label, data FROM saves WHERE save_type='pro_league' ORDER BY updated_at DESC"
    ).fetchall()
    leagues = []
    for row in rows:
        try:
            blob = json.loads(row["data"])
            leagues.append({"save_key": row["save_key"], "label": row["label"] or row["save_key"], "blob": blob})
        except (json.JSONDecodeError, TypeError):
            continue
    return leagues


def _load_wvl_leagues(conn: sqlite3.Connection) -> list[dict]:
    """WVL career leagues — one row per league, single blob carrying the
    full season (`standings`, `results`, `cards` with career_seasons).
    Save type added when viperball rebuilt WVL as a CVL-graduate career
    league this season."""
    rows = conn.execute(
        "SELECT save_key, label, data FROM saves WHERE save_type='wvl_career_league' ORDER BY updated_at DESC"
    ).fetchall()
    leagues = []
    for row in rows:
        try:
            blob = json.loads(row["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        leagues.append({"save_key": row["save_key"],
                        "label": row["label"] or f"WVL Y{blob.get('year', '')}".rstrip(),
                        "blob": blob})
    return leagues


_BOX_KEY = re.compile(r"^(.+)__w(\d+)__(.+)$")
_college_cache: dict = {"key": None, "leagues": [], "building": False}


def _college_leagues(path: str) -> list[dict]:
    """Parse all college box scores into per-session leagues, cached on the
    DB file's mtime.

    Stale-while-revalidate: when the DB changes and a previous result
    exists, serve it immediately and rebuild in a background thread — the
    full parse is hundreds of ~200KB JSON blobs and must never run on a
    visitor's request."""
    try:
        cache_key = (path, os.path.getmtime(path))
    except OSError:
        return []
    if _college_cache["key"] == cache_key:
        return _college_cache["leagues"]
    if _college_cache["leagues"] and _college_cache["key"] is not None:
        if not _college_cache["building"]:
            _college_cache["building"] = True
            import threading
            threading.Thread(target=_rebuild_college, args=(path, cache_key),
                             daemon=True, name="vb-college-rebuild").start()
        return _college_cache["leagues"]
    return _rebuild_college(path, cache_key)


def _active_session_ids(path: str) -> set[str] | None:
    """Sessions the viperball app currently reports active. Returns None
    when sessions.json hasn't been synced — callers fall back to "trust
    the DB" so local dev / pre-sync state isn't blanked out."""
    sessions_path = os.path.join(os.path.dirname(path) or ".",
                                 "viperball_sessions.json")
    if not os.path.exists(sessions_path):
        return None
    try:
        with open(sessions_path) as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return {s.get("session_id") for s in blob.get("college", [])
            if s.get("session_id")}


def _rebuild_college(path: str, cache_key) -> list[dict]:
    sessions: dict[str, dict] = {}
    try:
        conn = _conn(path)
        rows = conn.execute(
            "SELECT save_key, data FROM saves WHERE save_type='box_score'"
        ).fetchall()
        conn.close()
    except Exception:
        _college_cache["building"] = False
        return []
    active = _active_session_ids(path)
    for r in rows:
        m = _BOX_KEY.match(r["save_key"])
        if not m:
            continue
        sid, week, matchup_key = m.group(1), int(m.group(2)), m.group(3)
        if active is not None and sid not in active:
            continue
        try:
            fr = json.loads(r["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        fs = fr.get("final_score", {})
        home, away = fs.get("home", {}), fs.get("away", {})
        hs, as_ = float(home.get("score", 0)), float(away.get("score", 0))
        s = sessions.setdefault(sid, {"games": [], "standings": {}, "players": {}})
        s["games"].append({
            "week": week, "matchup_key": matchup_key,
            "home_name": home.get("team", ""), "away_name": away.get("team", ""),
            "home_score": _num(hs), "away_score": _num(as_),
        })
        for name, won, lost in ((home.get("team", ""), hs > as_, hs < as_),
                                (away.get("team", ""), as_ > hs, as_ < hs)):
            rec = s["standings"].setdefault(
                name, {"team_name": name, "wins": 0, "losses": 0, "ties": 0,
                       "streak": 0, "streak_type": ""})
            rec["wins"] += won
            rec["losses"] += lost
            rec["ties"] += not won and not lost
        for side, team in (("home", home.get("team", "")), ("away", away.get("team", ""))):
            for p in fr.get("player_stats", {}).get(side, []):
                acc = s["players"].setdefault((team, p.get("name", "")), {
                    "name": p.get("name", ""), "team_key": team,
                    "position": p.get("position", ""), "games": 0,
                    "rushing_yards": 0, "rushing_carries": 0, "touchdowns": 0,
                    "kick_pass_yards": 0, "kick_pass_completions": 0,
                    "kick_pass_tds": 0, "lateral_yards": 0, "laterals": 0,
                    "tackles": 0, "tfl": 0, "sacks": 0,
                    "dk_made": 0, "dk_att": 0, "total_yards": 0})
                acc["games"] += 1
                acc["rushing_yards"] += p.get("rushing_yards", 0)
                acc["rushing_carries"] += p.get("rush_carries", p.get("rushing_carries", 0))
                acc["touchdowns"] += p.get("tds", 0)
                acc["kick_pass_yards"] += p.get("kick_pass_yards", 0)
                acc["kick_pass_completions"] += p.get("kick_passes_completed",
                                                      p.get("kick_pass_completions", 0))
                acc["kick_pass_tds"] += p.get("kick_pass_tds", 0)
                acc["lateral_yards"] += p.get("lateral_yards", 0)
                acc["laterals"] += p.get("laterals_thrown", p.get("laterals", 0))
                acc["tackles"] += p.get("tackles", 0)
                acc["tfl"] += p.get("tfl", 0)
                acc["sacks"] += p.get("sacks", 0)
                acc["dk_made"] += p.get("dk_made", 0)
                acc["dk_att"] += p.get("dk_att", p.get("dk_attempted", 0))
                acc["total_yards"] += p.get("all_purpose_yards", p.get("yards", 0))
    leagues = []
    for sid, s in sessions.items():
        label = "College Viperball" if len(sessions) == 1 else f"College ({sid[:8]})"
        s["games"].sort(key=lambda g: -g["week"])
        teams = sorted(s["standings"].values(), key=lambda t: (-t["wins"], t["losses"]))
        leaders = sorted(s["players"].values(), key=lambda p: -p["rushing_yards"])
        leagues.append({"label": label, "save_key": sid, "games": s["games"],
                        "teams": teams, "leaders": leaders})
    _college_cache["key"] = cache_key
    _college_cache["leagues"] = leagues
    _college_cache["building"] = False
    return leagues


def get_recent_scores(limit_per_league: int = 8) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    results = []
    try:
        conn = _conn(path)
        leagues = _load_leagues(conn)
        conn.close()
        for lg in leagues:
            blob = lg["blob"]
            current_week = blob.get("current_week", 0)
            collected = 0
            for w in range(current_week, 0, -1):
                week_results = blob.get("results", {}).get(str(w), {})
                for matchup_key, res in week_results.items():
                    if collected >= limit_per_league:
                        break
                    results.append({
                        "league": lg["label"],
                        "save_key": lg["save_key"],
                        "week": w,
                        "matchup_key": matchup_key,
                        "home_name": res.get("home_name", ""),
                        "away_name": res.get("away_name", ""),
                        "home_score": _num(res.get("home_score", 0)),
                        "away_score": _num(res.get("away_score", 0)),
                    })
                    collected += 1
                if collected >= limit_per_league:
                    break
        kp = _portal_kenpom()
        for lg in _college_leagues(path):
            team_conf = {r["team"]: r.get("conference", "")
                         for r in kp.get(lg["save_key"], [])}
            for g in lg["games"][:limit_per_league]:
                hc, ac = team_conf.get(g["home_name"], ""), team_conf.get(g["away_name"], "")
                results.append({"league": lg["label"], "save_key": lg["save_key"],
                                "conf": hc if hc and hc == ac else "", **g})
        conn = _conn(path)
        for lg in _load_wvl_leagues(conn):
            blob = lg["blob"]
            current_week = int(blob.get("current_week", 0))
            collected = 0
            # WVL results come back as {week: [games]} with int keys after
            # load, but SQLite hands us the raw JSON where they're strings.
            for w in range(current_week, 0, -1):
                games = blob.get("results", {}).get(str(w)) \
                        or blob.get("results", {}).get(w) or []
                for g in games:
                    if collected >= limit_per_league:
                        break
                    home_key = g.get("home_key", "")
                    away_key = g.get("away_key", "")
                    results.append({
                        "league": lg["label"], "save_key": lg["save_key"],
                        "week": w,
                        "matchup_key": f"{away_key}_at_{home_key}",
                        "home_name": g.get("home_name", ""),
                        "away_name": g.get("away_name", ""),
                        "home_score": _num(g.get("home_score", 0)),
                        "away_score": _num(g.get("away_score", 0)),
                    })
                    collected += 1
                if collected >= limit_per_league:
                    break
        conn.close()
    except Exception:
        pass
    return results


def _portal_kenpom() -> dict[str, list]:
    """Map of session_id → KenPom rows the viperball stats site computed.
    Returns {} if no exports have been synced."""
    path = _db_path()
    if not path:
        return {}
    import glob as _glob
    out = {}
    for fp in _glob.glob(os.path.join(os.path.dirname(path) or ".", "vb_kp_*.json")):
        try:
            with open(fp) as fh:
                blob = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        out[blob.get("session_id", "")] = blob.get("standings", [])
    return out


def get_standings() -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        leagues = _load_leagues(conn)
        conn.close()
        for lg in leagues:
            blob = lg["blob"]
            teams = []
            for team_key, ts in blob.get("standings", {}).items():
                if isinstance(ts, dict):
                    teams.append({
                        "team_key": team_key,
                        "team_name": ts.get("team_name", team_key),
                        "wins": ts.get("wins", 0),
                        "losses": ts.get("losses", 0),
                        "ties": ts.get("ties", 0),
                        "streak": ts.get("streak", ""),
                        "streak_type": ts.get("streak_type", ""),
                    })
            teams.sort(key=lambda t: (-t["wins"], t["losses"]))
            out.append({"league": lg["label"], "save_key": lg["save_key"],
                        "tier": "Pro", "teams": teams})
        kp = _portal_kenpom()
        for lg in _college_leagues(path):
            teams = lg["teams"]
            kp_rows = kp.get(lg["save_key"])
            if kp_rows:
                # Merge KenPom efficiency + conference onto the team rows.
                by_name = {r["team"]: r for r in kp_rows}
                for t in teams:
                    extras = by_name.get(t["team_name"])
                    if extras:
                        for k in ("adj_o", "adj_d", "tempo", "em", "luck"):
                            if extras.get(k) is not None:
                                t[k] = extras[k]
                        if extras.get("conference"):
                            t["conf"] = extras["conference"]
            out.append({"league": lg["label"], "save_key": lg["save_key"],
                        "tier": "College", "teams": teams,
                        "has_kenpom": bool(kp_rows)})
        conn = _conn(path)
        for lg in _load_wvl_leagues(conn):
            blob = lg["blob"]
            teams = []
            for team_key, ts in blob.get("standings", {}).items():
                if not isinstance(ts, dict):
                    continue
                teams.append({
                    "team_key": team_key,
                    "team_name": ts.get("team_name", team_key),
                    "wins": ts.get("wins", 0),
                    "losses": ts.get("losses", 0),
                    "ties": ts.get("ties", 0),
                    "pf": ts.get("pf", 0),
                    "pa": ts.get("pa", 0),
                    "streak": "", "streak_type": "",
                })
            teams.sort(key=lambda t: (-t["wins"], t["losses"]))
            out.append({"league": lg["label"], "save_key": lg["save_key"],
                        "tier": "WVL", "teams": teams})
        conn.close()
    except Exception:
        pass
    return out


# Leader boards: (title, sort key, columns as (label, key, fmt|None)).
# A board only renders when at least one player has a nonzero sort stat,
# so leagues without e.g. drop kicks don't show an empty table.
_BOARDS = [
    ("Rushing", "rushing_yards",
     [("Yds", "rushing_yards", None), ("Car", "rushing_carries", None), ("TD", "touchdowns", None)]),
    ("Kick passing", "kick_pass_yards",
     [("Yds", "kick_pass_yards", None), ("Cmp", "kick_pass_completions", None), ("TD", "kick_pass_tds", None)]),
    ("Laterals", "lateral_yards",
     [("Yds", "lateral_yards", None), ("Thrown", "laterals", None)]),
    ("Defense", "tackles",
     [("Tkl", "tackles", None), ("TFL", "tfl", None), ("Sacks", "sacks", None)]),
    ("Kicking", "dk_made",
     [("DK", "dk_made", None), ("Att", "dk_att", None)]),
    ("All-purpose", "total_yards",
     [("Yds", "total_yards", None), ("TD", "touchdowns", None)]),
]


def _build_boards(players: list[dict], limit: int = 10) -> list[dict]:
    boards = []
    for title, sort_key, cols in _BOARDS:
        rows = [p for p in players if p.get(sort_key, 0)]
        if not rows:
            continue
        rows.sort(key=lambda p: -p.get(sort_key, 0))
        for r in rows:
            r.setdefault("team", r.get("team_key", ""))
        boards.append({"title": title, "sort": sort_key,
                       "cols": [("Pos", "position", None), ("G", "games", None)] + cols,
                       "rows": rows[:limit]})
    return boards


def get_stat_leaders(limit: int = 10) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        leagues = _load_leagues(conn)
        conn.close()
        for lg in leagues:
            blob = lg["blob"]
            all_players: list[dict] = []
            for team_key, team_players in blob.get("player_season_stats", {}).items():
                for pid, ps in team_players.items():
                    all_players.append({
                        "name": ps.get("name", pid),
                        "team_key": team_key,
                        "position": ps.get("position", ""),
                        "games": ps.get("games", 0),
                        "rushing_yards": ps.get("rushing_yards", 0),
                        "rushing_carries": ps.get("rushing_carries", 0),
                        "touchdowns": ps.get("touchdowns", 0),
                        "kick_pass_yards": ps.get("kick_pass_yards", 0),
                        "kick_pass_completions": ps.get("kick_pass_completions", 0),
                        "kick_pass_tds": ps.get("kick_pass_tds", 0),
                        "lateral_yards": ps.get("lateral_yards", 0),
                        "laterals": ps.get("laterals", 0),
                        "tackles": ps.get("tackles", 0),
                        "tfl": ps.get("tfl", 0),
                        "sacks": ps.get("sacks", 0),
                        "dk_made": ps.get("dk_made", 0),
                        "dk_att": ps.get("dk_attempted", 0),
                        "total_yards": ps.get("total_yards", 0),
                    })
            out.append({"league": lg["label"], "save_key": lg["save_key"],
                        "tier": "Pro", "boards": _build_boards(all_players, limit)})
        for lg in _college_leagues(path):
            out.append({"league": lg["label"], "save_key": lg["save_key"],
                        "tier": "College",
                        "boards": _build_boards(lg["leaders"], limit)})
        conn = _conn(path)
        for lg in _load_wvl_leagues(conn):
            out.append({"league": lg["label"], "save_key": lg["save_key"],
                        "tier": "WVL",
                        "boards": _build_boards(_wvl_player_leaders(lg["blob"]), limit)})
        conn.close()
    except Exception:
        pass
    return out


def _wvl_player_leaders(blob: dict) -> list[dict]:
    """Aggregate current-year stats from each card's career_seasons.

    A WVL career league stores everything in `cards[pid].career_seasons`,
    a list of SeasonStats — most recent (current year) at the end."""
    year = blob.get("year")
    players = []
    for pid, card in blob.get("cards", {}).items():
        seasons = card.get("career_seasons") or []
        if not seasons:
            continue
        # Prefer the season matching league.year; fall back to the last.
        season = next((s for s in reversed(seasons)
                       if s.get("season_year") == year), seasons[-1])
        rushing_yards = season.get("rushing_yards", 0)
        kp_yards = season.get("kick_pass_yards", 0)
        lateral_yards = season.get("lateral_yards", 0)
        first = card.get("first_name", "")
        last = card.get("last_name", "")
        name = (first + " " + last).strip() or card.get("player_id", pid)
        players.append({
            "name": name,
            "team_key": season.get("team", ""),
            "position": card.get("position", ""),
            "games": season.get("games_played", 0),
            "rushing_yards": rushing_yards,
            "rushing_carries": season.get("rush_carries", 0),
            "touchdowns": season.get("touchdowns", 0),
            "kick_pass_yards": kp_yards,
            "kick_pass_completions": season.get("kick_passes_completed", 0),
            "kick_pass_tds": season.get("kick_pass_tds", 0),
            "lateral_yards": lateral_yards,
            "laterals": season.get("laterals_thrown", 0),
            "tackles": season.get("tackles", 0),
            "tfl": season.get("tfl", 0),
            "sacks": season.get("sacks", 0),
            "dk_made": season.get("dk_makes", 0),
            "dk_att": season.get("dk_attempts", 0),
            "total_yards": season.get("total_yards",
                                       rushing_yards + kp_yards + lateral_yards),
        })
    return players


def get_game_detail(save_key: str, week: int, matchup_key: str) -> dict[str, Any] | None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        row = conn.execute(
            "SELECT label, data FROM saves WHERE save_type='pro_league' AND save_key=?",
            (save_key,)
        ).fetchone()
        if not row:
            # WVL career leagues: lookup by UUID save_key, then find the
            # game in `results` by parsing the `<away>_at_<home>` matchup.
            wvl = conn.execute(
                "SELECT label, data FROM saves WHERE save_type='wvl_career_league' AND save_key=?",
                (save_key,)
            ).fetchone()
            if wvl:
                conn.close()
                blob = json.loads(wvl["data"])
                games = (blob.get("results", {}).get(str(week))
                         or blob.get("results", {}).get(week) or [])
                away_key, _, home_key = matchup_key.partition("_at_")
                game = next((g for g in games
                             if g.get("away_key") == away_key
                             and g.get("home_key") == home_key), None)
                if not game:
                    return None
                return {
                    "league": wvl["label"] or f"WVL Y{blob.get('year', '')}".rstrip(),
                    "save_key": save_key, "week": week, "matchup_key": matchup_key,
                    "home_name": game.get("home_name", ""),
                    "away_name": game.get("away_name", ""),
                    "home_score": _num(game.get("home_score", 0)),
                    "away_score": _num(game.get("away_score", 0)),
                    "result": {},
                }
            # College: one box_score blob per game.
            box = conn.execute(
                "SELECT data FROM saves WHERE save_type='box_score' AND save_key=?",
                (f"{save_key}__w{week}__{matchup_key}",)
            ).fetchone()
            conn.close()
            if not box:
                return None
            fr = json.loads(box["data"])
            fs = fr.get("final_score", {})
            home, away = fs.get("home", {}), fs.get("away", {})
            mods = fr.get("modifier_stack", {}).get("home_defense", {})
            ref = fr.get("referee") or {}
            return {
                "league": "College Viperball",
                "save_key": save_key, "week": week, "matchup_key": matchup_key,
                "home_name": home.get("team", ""), "away_name": away.get("team", ""),
                "home_score": _num(home.get("score", 0)),
                "away_score": _num(away.get("score", 0)),
                "weather": " · ".join(
                    str(v).title() for v in
                    (mods.get("game_temperature"), mods.get("weather")) if v),
                "referee": {"name": ref.get("name", ""),
                            "overturned": ref.get("overturned_calls", 0),
                            "blown": ref.get("blown_calls", 0)},
                "styles": {
                    "home": " / ".join(s for s in (fr.get("home_style"), fr.get("home_defense_style")) if s),
                    "away": " / ".join(s for s in (fr.get("away_style"), fr.get("away_defense_style")) if s),
                },
                "rivalry": bool(fr.get("is_rivalry_game")),
                "result": {"stats": fr.get("stats", {}),
                           "player_stats": fr.get("player_stats", {}),
                           "drive_summary": fr.get("drive_summary", [])},
            }
        conn.close()
        blob = json.loads(row["data"])
        game = blob.get("results", {}).get(str(week), {}).get(matchup_key)
        if not game:
            return None
        return {
            "league": row["label"] or save_key,
            "save_key": save_key,
            "week": week,
            "matchup_key": matchup_key,
            "home_name": game.get("home_name", ""),
            "away_name": game.get("away_name", ""),
            "home_score": _num(game.get("home_score", 0)),
            "away_score": _num(game.get("away_score", 0)),
            "result": game.get("result", {}),
        }
    except Exception:
        return None
