"""Read-only adapter for a ZenGM Hockey League File (JSON export).

Mirrors the function surface of the SQLite adapters (baseball/viperball/
tennis) so app.py and the templates treat hockey like any other sport — but
the data source is a ZenGM League File on disk at ``$HOCKEY_LEAGUE_FILE``
rather than a synced SQLite snapshot. See adapters/zengm_common.py for why
and how the file is loaded/cached.

ZenGM conventions baked in here (verified against a real PWHL export):
  * ``game.teams[0]`` is the HOME team, ``game.teams[1]`` the AWAY team.
  * ``game.teams[i].pts`` is that team's goals; ``game.playoffs`` flags the
    postseason; a goal's ``t`` indexes into ``game.teams``.
  * Skater goals/assists are split ev/pp/sh (sum them); goalies are the
    rows with ``gpGoalie > 0`` and carry sv/ga/so/gW plus ``gMin`` minutes.
"""
from __future__ import annotations

import os
from typing import Any

from adapters import zengm_common as z

ENV = "HOCKEY_LEAGUE_FILE"


def _league() -> dict | None:
    return z.load(ENV)


def league_label() -> str:
    lg = _league()
    return z.league_label(lg, "Hockey") if lg else "Hockey"


# ── helpers ──────────────────────────────────────────────────────────────

def _skater_line(st: dict) -> dict:
    g = (st.get("evG", 0) or 0) + (st.get("ppG", 0) or 0) + (st.get("shG", 0) or 0)
    a = (st.get("evA", 0) or 0) + (st.get("ppA", 0) or 0) + (st.get("shA", 0) or 0)
    return {
        "gp": st.get("gp", 0) or 0, "g": g, "a": a, "pts": g + a,
        "pm": st.get("pm", 0) or 0, "pim": st.get("pim", 0) or 0,
        "s": st.get("s", 0) or 0, "hit": st.get("hit", 0) or 0,
        "blk": st.get("blk", 0) or 0,
        "toi": round((st.get("min", 0) or 0)),
    }


def _goalie_line(st: dict) -> dict:
    sv = st.get("sv", 0) or 0
    ga = st.get("ga", 0) or 0
    gmin = st.get("gMin", st.get("min", 0)) or 0
    shots = sv + ga
    return {
        "gp": st.get("gpGoalie", 0) or 0, "w": st.get("gW", 0) or 0,
        "l": st.get("gL", 0) or 0, "otl": st.get("gOTL", 0) or 0,
        "so": st.get("so", 0) or 0, "sv": sv, "ga": ga, "gMin": gmin,
        "svPct": (sv / shots) if shots else 0.0,
        "gaa": (ga * 60.0 / gmin) if gmin else 0.0,
    }


# ── scores ───────────────────────────────────────────────────────────────

def get_recent_scores(limit: int = 15) -> list[dict]:
    """Current-season games, newest first. HOME = teams[0], AWAY = teams[1]."""
    lg = _league()
    if not lg:
        return []
    season = z.current_season(lg)
    teams = z.team_index(lg)
    label = z.league_label(lg, "Hockey")
    games = [g for g in (lg.get("games") or []) if g.get("season") == season]
    games.sort(key=lambda g: (g.get("day", 0), g.get("gid", 0)), reverse=True)
    out = []
    for g in games[:limit]:
        home = teams.get(g["teams"][0]["tid"], {})
        away = teams.get(g["teams"][1]["tid"], {})
        out.append({
            "id": g["gid"], "league": label,
            "season": season, "day": g.get("day", 0),
            "game_date": f"{season} · Day {g.get('day', 0)}",
            "home_name": home.get("name", "?"), "home_abbrev": home.get("abbrev", ""),
            "away_name": away.get("name", "?"), "away_abbrev": away.get("abbrev", ""),
            "home_score": g["teams"][0].get("pts", 0),
            "away_score": g["teams"][1].get("pts", 0),
            "is_playoff": bool(g.get("playoffs")),
            "overtimes": g.get("overtimes", 0) or 0,
        })
    return out


# ── standings ────────────────────────────────────────────────────────────

def get_standings() -> list[dict]:
    """[{league, tier, teams}] for the current season. Hockey points =
    2*W + OTL + T; rows carry W/L/OTL/T/PTS/streak grouped by division."""
    lg = _league()
    if not lg:
        return []
    season = z.current_season(lg)
    teams = z.team_index(lg)
    rows = []
    for t in lg.get("teams") or []:
        if t.get("disabled"):
            continue
        s = next((s for s in reversed(t.get("seasons") or [])
                  if s.get("season") == season), None)
        if not s:
            continue
        w, l = s.get("won", 0) or 0, s.get("lost", 0) or 0
        otl, tied = s.get("otl", 0) or 0, s.get("tied", 0) or 0
        streak = s.get("streak", 0) or 0
        ident = teams.get(t["tid"], {})
        rows.append({
            "name": ident.get("name", "?"), "abbrev": ident.get("abbrev", ""),
            "conf": ident.get("conf", ""), "division": ident.get("division", ""),
            "wins": w, "losses": l, "otl": otl, "ties": tied,
            "pts": 2 * w + otl + tied,
            "streak": abs(streak),
            "streak_type": "W" if streak > 0 else "L" if streak < 0 else "",
        })
    if not rows:
        return []
    rows.sort(key=lambda r: r["pts"], reverse=True)
    return [{"league": z.league_label(lg, "Hockey"), "tier": "Pro", "teams": rows}]


# ── leaders ──────────────────────────────────────────────────────────────

def _season_aggregates(lg: dict, season: int):
    """Per-player regular-season totals, split into skaters and goalies. A
    player traded mid-season has multiple stat rows; sum the counting stats
    and attribute the team with the most games played."""
    teams = z.team_index(lg)
    skaters: dict[int, dict] = {}
    goalies: dict[int, dict] = {}
    for p in lg.get("players") or []:
        name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
        sk = None
        go = None
        sk_tid = go_tid = (-1, -1)  # (gp, tid) for team attribution
        for st in p.get("stats") or []:
            if st.get("playoffs") or st.get("season") != season:
                continue
            if (st.get("gpSkater", 0) or 0) > 0:
                line = _skater_line(st)
                if sk is None:
                    sk = {"name": name, **{k: 0 for k in line}}
                for k, v in line.items():
                    sk[k] = sk.get(k, 0) + v
                if (st.get("gpSkater", 0) or 0) > sk_tid[0]:
                    sk_tid = (st.get("gpSkater", 0) or 0, st.get("tid"))
            if (st.get("gpGoalie", 0) or 0) > 0:
                line = _goalie_line(st)
                if go is None:
                    go = {"name": name, **{k: 0 for k in line if k not in ("svPct", "gaa")}}
                for k in ("gp", "w", "l", "otl", "so", "sv", "ga", "gMin"):
                    go[k] = go.get(k, 0) + line[k]
                if (st.get("gpGoalie", 0) or 0) > go_tid[0]:
                    go_tid = (st.get("gpGoalie", 0) or 0, st.get("tid"))
        if sk:
            sk["team"] = teams.get(sk_tid[1], {}).get("abbrev", "")
            skaters[p["pid"]] = sk
        if go:
            shots = go["sv"] + go["ga"]
            go["svPct"] = (go["sv"] / shots) if shots else 0.0
            go["gaa"] = (go["ga"] * 60.0 / go["gMin"]) if go["gMin"] else 0.0
            go["team"] = teams.get(go_tid[1], {}).get("abbrev", "")
            goalies[p["pid"]] = go
    return list(skaters.values()), list(goalies.values())


def get_leader_boards(limit: int = 10) -> list[dict]:
    """Skater + goalie leader boards, shape matching the other sports
    ({title, sort, cols, rows}) so leaders.html renders them generically."""
    lg = _league()
    if not lg:
        return []
    season = z.current_season(lg)
    skaters, goalies = _season_aggregates(lg, season)
    if not skaters and not goalies:
        return []
    num_games = z.ga(lg, "numGames", 0) or 0
    g_floor = max(5, int(num_games * 0.2))  # games to qualify for rate boards

    boards: list[dict] = []

    def board(title, rows, sort, cols, reverse=True, floor=0):
        pool = [r for r in rows if (r.get("gp", 0) or 0) >= floor] if floor else \
            [r for r in rows if r.get(sort)]
        if not pool:
            return
        pool.sort(key=lambda r: (r.get(sort) or 0), reverse=reverse)
        boards.append({"title": title, "sort": sort, "cols": cols, "rows": pool[:limit]})

    board("Points", skaters, "pts",
          [("GP", "gp", None), ("G", "g", None), ("A", "a", None), ("PTS", "pts", None)])
    board("Goals", skaters, "g",
          [("GP", "gp", None), ("G", "g", None), ("A", "a", None)])
    board("Assists", skaters, "a",
          [("GP", "gp", None), ("A", "a", None), ("G", "g", None)])
    board("Plus/minus", skaters, "pm",
          [("GP", "gp", None), ("+/-", "pm", None), ("PTS", "pts", None)])
    board("Penalty minutes", skaters, "pim",
          [("GP", "gp", None), ("PIM", "pim", None)])
    board("Goalie wins", goalies, "w",
          [("GP", "gp", None), ("W", "w", None), ("SO", "so", None)])
    board("Goals-against average", goalies, "gaa",
          [("GP", "gp", None), ("GAA", "gaa", "%.2f"), ("SV%", "svPct", "%.3f")],
          reverse=False, floor=g_floor)
    board("Save percentage", goalies, "svPct",
          [("GP", "gp", None), ("SV%", "svPct", "%.3f"), ("GAA", "gaa", "%.2f")],
          floor=g_floor)
    board("Shutouts", goalies, "so",
          [("GP", "gp", None), ("SO", "so", None), ("W", "w", None)])
    return boards


# ── news ─────────────────────────────────────────────────────────────────

def get_news(limit: int = 6) -> list[dict]:
    """ZenGM has no gazette; recaps come from the mechanical wire instead."""
    return []


# ── game detail ──────────────────────────────────────────────────────────

def get_game_detail(game_id: int) -> dict[str, Any] | None:
    lg = _league()
    if not lg:
        return None
    g = next((x for x in (lg.get("games") or []) if x.get("gid") == game_id), None)
    if not g:
        return None
    teams = z.team_index(lg)
    home_tid = g["teams"][0]["tid"]
    away_tid = g["teams"][1]["tid"]
    home = teams.get(home_tid, {})
    away = teams.get(away_tid, {})

    skaters, goalies = [], []
    for side, box in (("home", g["teams"][0]), ("away", g["teams"][1])):
        tid = box["tid"]
        for p in box.get("players") or []:
            if (p.get("gpGoalie", 0) or 0) > 0:
                line = _goalie_line(p)
                line.update({"team_id": tid, "side": side,
                             "name": p.get("name", ""), "pos": "G"})
                goalies.append(line)
            else:
                line = _skater_line(p)
                line.update({"team_id": tid, "side": side,
                             "name": p.get("name", ""), "pos": p.get("pos", "")})
                skaters.append(line)

    scoring = []
    for s in g.get("scoringSummary") or []:
        if s.get("type") != "goal":
            continue
        names = s.get("names") or []
        scoring.append({
            "period": s.get("quarter", ""),
            "time": z.clock_mmss(s.get("clock", 0)),
            "team": teams.get(g["teams"][s.get("t", 0)]["tid"], {}).get("abbrev", ""),
            "scorer": names[0] if names else "",
            "assists": ", ".join(names[1:]) if len(names) > 1 else "unassisted",
            "kind": (s.get("goalType") or "").upper(),
        })

    return {
        "game": {
            "id": g["gid"],
            "home_name": home.get("name", "?"), "home_abbrev": home.get("abbrev", ""),
            "away_name": away.get("name", "?"), "away_abbrev": away.get("abbrev", ""),
            "home_team_id": home_tid, "away_team_id": away_tid,
            "home_score": g["teams"][0].get("pts", 0),
            "away_score": g["teams"][1].get("pts", 0),
            "is_playoff": bool(g.get("playoffs")),
            "overtimes": g.get("overtimes", 0) or 0,
            "game_date": f"{g.get('season')} · Day {g.get('day', 0)}",
        },
        "skaters": skaters,
        "goalies": goalies,
        "scoring_summary": scoring,
        # raw team box totals for the head-to-head bars
        "team_box": {"home": g["teams"][0], "away": g["teams"][1]},
    }
