"""Read-only adapter for ZenGM **Basketball** (BBGM) League Files (JSON).

Same feed-cfg contract as adapters/zengm_rink.py (one league file per feed,
grouped under sport tabs by adapters/zengm_feeds.py) — so the user can run
NBA, WNBA, and gender-split college leagues side by side, each its own
uploaded file. A feed dict:
    {"key": "nba", "sport": "Basketball", "league": "NBA",
     "env": "NBA_LEAGUE_FILE", "engine": "basketball"}

Conventions verified against a real BBGM export (NBA 2K26): ``game.teams[0]``
is HOME, ``[1]`` AWAY (matched to ``wonHome/lostHome``); ``game.playoffs``
flags the postseason; rebounds are ``orb + drb`` (no ``trb`` at game level).
Standings use the ``baseball`` kind (W/L/Pct/GB by league→division) — so
``league`` = conference, ``division`` = division.
"""
from __future__ import annotations

from typing import Any

from adapters import zengm_common as z

STANDINGS_KIND = "zengm"
GAME_TEMPLATE = "game_basketball.html"
_DUEL_PAIRS = [("Points", "pts"), ("Rebounds", "reb"), ("Assists", "ast"),
               ("Steals", "stl"), ("Blocks", "blk"), ("Turnovers", "tov")]


def _league(cfg: dict) -> dict | None:
    return z.load(cfg["env"])


def league_label(cfg: dict) -> str:
    # The feed's configured name is authoritative (the dropdown label, e.g.
    # NBA vs WNBA) — the in-file name is only a default.
    return cfg["league"]


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
        ident = teams.get(t["tid"], {})
        w, l = s.get("won", 0) or 0, s.get("lost", 0) or 0
        rows.append({
            "name": ident.get("name", "?"), "abbrev": ident.get("abbrev", ""),
            "wins": w, "losses": l,
            "conf": ident.get("conf", ""), "division": ident.get("division", ""),
            "pct": (w / (w + l)) if (w + l) else 0.0,
        })
    return rows


def standings(cfg: dict) -> list[dict]:
    """Catalog-ready league entry rendered conference -> division. Basketball
    columns: W/L/Pct, ranked by win percentage."""
    rows = _standings_rows(cfg)
    if not rows:
        return []
    return [{"label": league_label(cfg), "kind": STANDINGS_KIND, "tier": "Pro",
             "teams": rows, "sort": "pct", "reverse": True,
             "cols": [("W", "wins", None), ("L", "losses", None),
                      ("Pct", "pct", "%.3f")]}]


# ── playoffs ─────────────────────────────────────────────────────────────

def playoffs(cfg: dict) -> list[dict]:
    """Bracket entries (shared adapters.bracket shape), [] when no postseason."""
    lg = _league(cfg)
    if not lg:
        return []
    b = z.playoff_bracket(lg, league_label(cfg))
    return [b] if b else []


# ── leaders ──────────────────────────────────────────────────────────────

def _season_totals(lg: dict, season: int) -> list[dict]:
    """Per-player current-season regular totals (summed across teams if
    traded), attributed to the team with the most games played."""
    teams = z.team_index(lg)
    agg: dict[int, dict] = {}
    keys = ("gp", "min", "pts", "orb", "drb", "ast", "stl", "blk", "tov",
            "fg", "fga", "tp", "tpa", "ft", "fta")
    for p in lg.get("players") or []:
        name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
        rec = None
        best = (-1, -1)  # (gp, tid)
        for st in p.get("stats") or []:
            if st.get("playoffs") or st.get("season") != season:
                continue
            if rec is None:
                rec = {"name": name, **{k: 0 for k in keys}}
            for k in keys:
                rec[k] += st.get(k, 0) or 0
            if (st.get("gp", 0) or 0) > best[0]:
                best = (st.get("gp", 0) or 0, st.get("tid"))
        if rec and rec["gp"]:
            rec["team"] = teams.get(best[1], {}).get("abbrev", "")
            rec["trb"] = rec["orb"] + rec["drb"]
            gp = rec["gp"]
            rec["ppg"] = rec["pts"] / gp
            rec["rpg"] = rec["trb"] / gp
            rec["apg"] = rec["ast"] / gp
            rec["spg"] = rec["stl"] / gp
            rec["bpg"] = rec["blk"] / gp
            rec["fgPct"] = (rec["fg"] / rec["fga"]) if rec["fga"] else 0.0
            rec["tpPct"] = (rec["tp"] / rec["tpa"]) if rec["tpa"] else 0.0
            rec["ftPct"] = (rec["ft"] / rec["fta"]) if rec["fta"] else 0.0
            agg[p["pid"]] = rec
    return list(agg.values())


def leader_boards(cfg: dict, limit: int = 10) -> list[dict]:
    """Per-game-average leader boards ({title, sort, cols, rows})."""
    lg = _league(cfg)
    if not lg:
        return []
    season = z.current_season(lg)
    players = _season_totals(lg, season)
    if not players:
        return []
    num_games = z.ga(lg, "numGames", 0) or 0
    g_floor = max(5, int(num_games * 0.4))  # games to qualify for rate boards

    boards: list[dict] = []

    def board(title, sort, cols, floor=g_floor, attempts_key=None, attempts_floor=0):
        pool = [r for r in players if (r.get("gp", 0) or 0) >= floor]
        if attempts_key:
            pool = [r for r in pool if (r.get(attempts_key, 0) or 0) >= attempts_floor]
        if not pool:
            return
        pool.sort(key=lambda r: (r.get(sort) or 0), reverse=True)
        boards.append({"title": title, "sort": sort, "cols": cols, "rows": pool[:limit]})

    board("Points per game", "ppg",
          [("GP", "gp", None), ("PPG", "ppg", "%.1f"), ("RPG", "rpg", "%.1f"),
           ("APG", "apg", "%.1f")])
    board("Rebounds per game", "rpg",
          [("GP", "gp", None), ("RPG", "rpg", "%.1f"), ("PPG", "ppg", "%.1f")])
    board("Assists per game", "apg",
          [("GP", "gp", None), ("APG", "apg", "%.1f"), ("PPG", "ppg", "%.1f")])
    board("Steals per game", "spg",
          [("GP", "gp", None), ("SPG", "spg", "%.1f")])
    board("Blocks per game", "bpg",
          [("GP", "gp", None), ("BPG", "bpg", "%.1f")])
    board("Field goal %", "fgPct",
          [("GP", "gp", None), ("FGA", "fga", None), ("FG%", "fgPct", "%.3f")],
          attempts_key="fga", attempts_floor=max(50, num_games * 3))
    board("Three-point %", "tpPct",
          [("GP", "gp", None), ("3PA", "tpa", None), ("3P%", "tpPct", "%.3f")],
          attempts_key="tpa", attempts_floor=max(25, num_games))
    board("Free throw %", "ftPct",
          [("GP", "gp", None), ("FTA", "fta", None), ("FT%", "ftPct", "%.3f")],
          attempts_key="fta", attempts_floor=max(25, num_games))
    return boards


# ── game detail ──────────────────────────────────────────────────────────

def _box_line(p: dict) -> dict:
    return {
        "name": p.get("name", ""), "pos": p.get("pos", ""),
        "min": round(p.get("min", 0) or 0),
        "fg": p.get("fg", 0) or 0, "fga": p.get("fga", 0) or 0,
        "tp": p.get("tp", 0) or 0, "tpa": p.get("tpa", 0) or 0,
        "ft": p.get("ft", 0) or 0, "fta": p.get("fta", 0) or 0,
        "reb": (p.get("orb", 0) or 0) + (p.get("drb", 0) or 0),
        "ast": p.get("ast", 0) or 0, "stl": p.get("stl", 0) or 0,
        "blk": p.get("blk", 0) or 0, "tov": p.get("tov", 0) or 0,
        "pf": p.get("pf", 0) or 0, "pts": p.get("pts", 0) or 0,
    }


def _team_totals(box: dict) -> dict:
    return {"pts": box.get("pts", 0),
            "reb": (box.get("orb", 0) or 0) + (box.get("drb", 0) or 0),
            "ast": box.get("ast", 0), "stl": box.get("stl", 0),
            "blk": box.get("blk", 0), "tov": box.get("tov", 0)}


def game_detail(cfg: dict, game_id: int) -> dict[str, Any] | None:
    """Fully render-ready box score (duel + ladder included) for game_basketball.html."""
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

    players = []
    for side, box in (("home", g["teams"][0]), ("away", g["teams"][1])):
        rows = [{**_box_line(p), "team_id": box["tid"], "side": side}
                for p in box.get("players") or []]
        rows.sort(key=lambda r: (r["min"], r["pts"]), reverse=True)
        players.extend(rows)

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
        "players": players,
        "duel": z.duel(_team_totals(g["teams"][1]), _team_totals(g["teams"][0]), _DUEL_PAIRS),
        "ladder": [t for t in rows if t["division"] in divs],
    }
