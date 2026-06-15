"""Read-only adapter for ZenGM **hockey-engine** League Files (JSON exports).

The user reskins one ZenGM hockey codebase into several sports/leagues —
hockey (NHL, PWHL), box lacrosse (NLL), indoor soccer (MASL), floorball —
whose exports are structurally identical. This module is a generic ZGMH
reader: every function takes a *feed* dict (one league file), defined in
adapters/zengm_feeds.py, so any number of leagues can be grouped under any
number of sport tabs without copying code.

A feed dict looks like:
    {"key": "pwhl", "sport": "Hockey", "league": "PWHL",
     "env": "PWHL_LEAGUE_FILE", "engine": "rink"}

ZenGM conventions (verified against a real PWHL export): ``game.teams[0]`` is
HOME, ``[1]`` AWAY; ``game.playoffs`` flags the postseason; a goal's ``t``
indexes into ``game.teams``; goalies are the rows with ``gpGoalie > 0``.
"""
from __future__ import annotations

from typing import Any

from adapters import zengm_common as z

# How standings/box scores for this engine are rendered (read by app.py +
# zengm_feeds). Conference -> division standings, rink box-score template.
STANDINGS_KIND = "zengm"
GAME_TEMPLATE = "game_zgmh.html"
# Head-to-head bars on the game page (away vs home team box totals).
_DUEL_PAIRS = [("Shots", "s"), ("Hits", "hit"), ("Blocks", "blk"),
               ("PIM", "pim"), ("Faceoff wins", "fow")]


def _league(cfg: dict) -> dict | None:
    return z.load(cfg["env"])


def league_label(cfg: dict) -> str:
    # The feed's configured name is authoritative (it's the dropdown label the
    # user picked, e.g. NHL vs PWHL) — the in-file name is only a default.
    return cfg["league"]


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

def recent_scores(cfg: dict, limit: int = 15) -> list[dict]:
    """Current-season games, newest first. HOME = teams[0], AWAY = teams[1]."""
    lg = _league(cfg)
    if not lg:
        return []
    season = z.current_season(lg)
    teams = z.team_index(lg)
    label = cfg["league"]
    games = [g for g in (lg.get("games") or []) if g.get("season") == season]
    games.sort(key=lambda g: (g.get("day", 0), g.get("gid", 0)), reverse=True)
    out = []
    for g in games[:limit]:
        home = teams.get(g["teams"][0]["tid"], {})
        away = teams.get(g["teams"][1]["tid"], {})
        out.append({
            "id": g["gid"], "league": label,
            "key": cfg["key"], "sport_label": cfg["sport"],
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

def _standings_rows(cfg: dict) -> list[dict]:
    lg = _league(cfg)
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
    rows.sort(key=lambda r: r["pts"], reverse=True)
    return rows


def standings(cfg: dict) -> list[dict]:
    """Catalog-ready league entry rendered conference -> division. Hockey
    columns: W/L/OTL/PTS, ranked by points."""
    rows = _standings_rows(cfg)
    if not rows:
        return []
    return [{"label": league_label(cfg), "kind": STANDINGS_KIND, "tier": "Pro",
             "teams": rows, "sort": "pts", "reverse": True,
             "cols": [("W", "wins", None), ("L", "losses", None),
                      ("OTL", "otl", None), ("PTS", "pts", None)]}]


# ── playoffs ─────────────────────────────────────────────────────────────

def playoffs(cfg: dict) -> list[dict]:
    """Bracket entries (shared adapters.bracket shape), [] when no postseason."""
    lg = _league(cfg)
    if not lg:
        return []
    b = z.playoff_bracket(lg, league_label(cfg))
    return [b] if b else []


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


def leader_boards(cfg: dict, limit: int = 10) -> list[dict]:
    """Skater + goalie leader boards ({title, sort, cols, rows})."""
    lg = _league(cfg)
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


# ── game detail ──────────────────────────────────────────────────────────

def game_detail(cfg: dict, game_id: int) -> dict[str, Any] | None:
    """Fully render-ready box score (duel + ladder included) for game_zgmh.html."""
    lg = _league(cfg)
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

    game = {
        "id": g["gid"], "key": cfg["key"], "sport_label": cfg["sport"],
        "home_name": home.get("name", "?"), "home_abbrev": home.get("abbrev", ""),
        "away_name": away.get("name", "?"), "away_abbrev": away.get("abbrev", ""),
        "home_team_id": home_tid, "away_team_id": away_tid,
        "home_score": g["teams"][0].get("pts", 0),
        "away_score": g["teams"][1].get("pts", 0),
        "is_playoff": bool(g.get("playoffs")),
        "overtimes": g.get("overtimes", 0) or 0,
        "game_date": f"{g.get('season')} · Day {g.get('day', 0)}",
    }
    rows = _standings_rows(cfg)
    divs = {t["division"] for t in rows if t["name"] in (game["home_name"], game["away_name"])}
    return {
        "game": game,
        "skaters": skaters,
        "goalies": goalies,
        "scoring_summary": scoring,
        "duel": z.duel(g["teams"][1], g["teams"][0], _DUEL_PAIRS),  # away, home
        "ladder": [t for t in rows if t["division"] in divs],
    }
