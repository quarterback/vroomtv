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


_BOX_KEY = re.compile(r"^(.+)__w(\d+)__(.+)$")
_college_cache: dict = {"key": None, "leagues": []}


def _college_leagues(path: str) -> list[dict]:
    """Parse all college box scores into per-session leagues, cached on the
    DB file's mtime."""
    try:
        cache_key = (path, os.path.getmtime(path))
    except OSError:
        return []
    if _college_cache["key"] == cache_key:
        return _college_cache["leagues"]
    sessions: dict[str, dict] = {}
    try:
        conn = _conn(path)
        rows = conn.execute(
            "SELECT save_key, data FROM saves WHERE save_type='box_score'"
        ).fetchall()
        conn.close()
    except Exception:
        return []
    for r in rows:
        m = _BOX_KEY.match(r["save_key"])
        if not m:
            continue
        sid, week, matchup_key = m.group(1), int(m.group(2)), m.group(3)
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
                    "rushing_yards": 0, "touchdowns": 0,
                    "kick_pass_yards": 0, "total_yards": 0})
                acc["games"] += 1
                acc["rushing_yards"] += p.get("rushing_yards", 0)
                acc["touchdowns"] += p.get("tds", 0)
                acc["kick_pass_yards"] += p.get("kick_pass_yards", 0)
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
        for lg in _college_leagues(path):
            for g in lg["games"][:limit_per_league]:
                results.append({"league": lg["label"], "save_key": lg["save_key"], **g})
    except Exception:
        pass
    return results


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
            out.append({"league": lg["label"], "save_key": lg["save_key"], "teams": teams})
        for lg in _college_leagues(path):
            out.append({"league": lg["label"], "save_key": lg["save_key"], "teams": lg["teams"]})
    except Exception:
        pass
    return out


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
                        "touchdowns": ps.get("touchdowns", 0),
                        "kick_pass_yards": ps.get("kick_pass_yards", 0),
                        "total_yards": ps.get("total_yards", 0),
                    })
            all_players.sort(key=lambda p: -p["rushing_yards"])
            out.append({"league": lg["label"], "save_key": lg["save_key"], "leaders": all_players[:limit]})
        for lg in _college_leagues(path):
            out.append({"league": lg["label"], "save_key": lg["save_key"], "leaders": lg["leaders"][:limit]})
    except Exception:
        pass
    return out


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
            return {
                "league": "College Viperball",
                "save_key": save_key, "week": week, "matchup_key": matchup_key,
                "home_name": home.get("team", ""), "away_name": away.get("team", ""),
                "home_score": _num(home.get("score", 0)),
                "away_score": _num(away.get("score", 0)),
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
