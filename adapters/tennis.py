"""Read-only adapter for tennis-team-manager SQLite database."""
from __future__ import annotations
import json
import os
import sqlite3
from typing import Any


def _conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_path() -> str | None:
    return os.environ.get("TENNIS_DB") or None


def get_recent_scores(limit_per_source: int = 8) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        for lg in conn.execute("SELECT id, name FROM gtt_leagues ORDER BY id").fetchall():
            rows = conn.execute("""
                SELECT d.id, d.week, d.home_points, d.away_points,
                       hf.name AS home_name, hf.abbrev AS home_abbrev,
                       af.name AS away_name, af.abbrev AS away_abbrev
                FROM gtt_duals d
                JOIN gtt_franchises hf ON hf.id = d.home AND hf.league_id = d.league_id
                JOIN gtt_franchises af ON af.id = d.away AND af.league_id = d.league_id
                WHERE d.league_id = ? AND d.status = 'final'
                ORDER BY d.id DESC LIMIT ?
            """, (lg["id"], limit_per_source)).fetchall()
            for r in rows:
                out.append({
                    "source": "gtt", "league": lg["name"], "league_id": lg["id"],
                    "id": r["id"], "week": r["week"],
                    "home_name": r["home_name"], "home_abbrev": r["home_abbrev"],
                    "away_name": r["away_name"], "away_abbrev": r["away_abbrev"],
                    "home_points": r["home_points"], "away_points": r["away_points"],
                })
        for s in conn.execute("SELECT id, division, gender FROM seasons ORDER BY id").fetchall():
            label = f"{s['division'].upper()} {s['gender'].title()}"
            rows = conn.execute("""
                SELECT id, week, home, away, home_points, away_points
                FROM duals WHERE season_id = ? AND status = 'final'
                ORDER BY id DESC LIMIT ?
            """, (s["id"], limit_per_source)).fetchall()
            for r in rows:
                out.append({
                    "source": "ncaa", "league": label, "season_id": s["id"],
                    "id": r["id"], "week": r["week"],
                    "home_name": r["home"], "away_name": r["away"],
                    "home_points": r["home_points"], "away_points": r["away_points"],
                })
        conn.close()
    except Exception:
        pass
    return out


def get_standings() -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        for lg in conn.execute("SELECT id, name FROM gtt_leagues ORDER BY id").fetchall():
            franchises = {
                r["id"]: {"name": r["name"], "abbrev": r["abbrev"], "wins": 0, "losses": 0}
                for r in conn.execute(
                    "SELECT id, name, abbrev FROM gtt_franchises WHERE league_id=?", (lg["id"],)
                ).fetchall()
            }
            # winner is a 0/1 flag (0 = home won), not a franchise id.
            # Regular season only, matching the sim's own standings.
            for d in conn.execute(
                "SELECT home, away, winner FROM gtt_duals"
                " WHERE league_id=? AND round='REG' AND status='final'",
                (lg["id"],)
            ).fetchall():
                win_fid = d["home"] if d["winner"] == 0 else d["away"]
                lose_fid = d["away"] if d["winner"] == 0 else d["home"]
                if win_fid in franchises:
                    franchises[win_fid]["wins"] += 1
                if lose_fid in franchises:
                    franchises[lose_fid]["losses"] += 1
            out.append({"league": lg["name"], "source": "gtt",
                        "teams": sorted(franchises.values(), key=lambda t: (-t["wins"], t["losses"]))})
        for s in conn.execute("SELECT id, division, gender FROM seasons ORDER BY id").fetchall():
            label = f"{s['division'].upper()} {s['gender'].title()}"
            rows = conn.execute("""
                SELECT home AS school, SUM(CASE WHEN winner=0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN winner=1 THEN 1 ELSE 0 END) AS losses
                FROM duals WHERE season_id=? AND status='final' GROUP BY home
                UNION ALL
                SELECT away, SUM(CASE WHEN winner=1 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN winner=0 THEN 1 ELSE 0 END)
                FROM duals WHERE season_id=? AND status='final' GROUP BY away
            """, (s["id"], s["id"])).fetchall()
            agg: dict[str, dict] = {}
            for r in rows:
                sch = r["school"]
                if sch not in agg:
                    agg[sch] = {"name": sch, "wins": 0, "losses": 0}
                agg[sch]["wins"] += r["wins"]
                agg[sch]["losses"] += r["losses"]
            out.append({"league": label, "source": "ncaa",
                        "teams": sorted(agg.values(), key=lambda t: (-t["wins"], t["losses"]))})
        conn.close()
    except Exception:
        pass
    return out


def get_stat_leaders(limit: int = 10, min_matches: int = 3) -> list[dict]:
    """Singles match-win leaders aggregated from dual line scores.

    Season play persists per-match results only inside lines_json (the
    matches/match_stats tables are filled solely by the one-off CLI sims,
    and fast-fidelity duals zero their stat blocks), so wins are the one
    stat reliably available. Singles only: GTT slots MS*/WS*, NCAA S*.
    """
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    tally: dict[tuple, dict] = {}

    def _bump(key: tuple, name: str, team: str, league: str, won: bool):
        rec = tally.setdefault(key, {"name": name, "team": team, "league": league,
                                     "matches": 0, "wins": 0})
        rec["matches"] += 1
        rec["wins"] += 1 if won else 0

    try:
        conn = _conn(path)
        pid_info = {}
        for lg in conn.execute("SELECT id, name FROM gtt_leagues").fetchall():
            abbrevs = {r["id"]: r["abbrev"] for r in conn.execute(
                "SELECT id, abbrev FROM gtt_franchises WHERE league_id=?", (lg["id"],)).fetchall()}
            for p in conn.execute(
                "SELECT pid, fid, data FROM gtt_players WHERE league_id=?", (lg["id"],)
            ).fetchall():
                try:
                    nm = json.loads(p["data"]).get("name", p["pid"])
                except (json.JSONDecodeError, TypeError):
                    nm = p["pid"]
                pid_info[p["pid"]] = (nm, abbrevs.get(p["fid"], ""), lg["name"])
            for d in conn.execute(
                "SELECT lines_json FROM gtt_duals WHERE league_id=? AND status='final'",
                (lg["id"],)
            ).fetchall():
                for line in json.loads(d["lines_json"] or "[]"):
                    slot = line.get("slot", "")
                    if not line.get("completed") or slot[:2] not in ("MS", "WS"):
                        continue
                    for side, pids in (("home", line.get("home_pids", [])),
                                       ("away", line.get("away_pids", []))):
                        for pid in pids:
                            nm, team, league = pid_info.get(pid, (pid, "", lg["name"]))
                            won = line.get("home_won") == (side == "home")
                            _bump(("gtt", pid), nm, team, league, won)
        for s in conn.execute("SELECT id, division, gender FROM seasons").fetchall():
            label = f"{s['division'].upper()} {s['gender'].title()}"
            for d in conn.execute(
                "SELECT home, away, lines_json FROM duals WHERE season_id=? AND status='final'",
                (s["id"],)
            ).fetchall():
                for line in json.loads(d["lines_json"] or "[]"):
                    if not line.get("completed") or not line.get("slot", "").startswith("S"):
                        continue
                    for side, school in (("home", d["home"]), ("away", d["away"])):
                        nm = line.get(f"{side}_player")
                        if not nm:
                            continue
                        won = line.get("home_won") == (side == "home")
                        _bump(("ncaa", s["id"], school, nm), nm, school, label, won)
        conn.close()
    except Exception:
        return []
    leaders = [r for r in tally.values() if r["matches"] >= min_matches]
    for r in leaders:
        r["win_pct"] = r["wins"] / r["matches"]
    leaders.sort(key=lambda r: (-r["wins"], -r["win_pct"]))
    return leaders[:limit]


def get_game_detail(source: str, dual_id: int) -> dict[str, Any] | None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        if source == "gtt":
            dual = conn.execute("""
                SELECT d.*, hf.name AS home_name, hf.abbrev AS home_abbrev,
                             af.name AS away_name, af.abbrev AS away_abbrev,
                             lg.name AS league_name
                FROM gtt_duals d
                JOIN gtt_franchises hf ON hf.id=d.home AND hf.league_id=d.league_id
                JOIN gtt_franchises af ON af.id=d.away AND af.league_id=d.league_id
                JOIN gtt_leagues lg ON lg.id=d.league_id
                WHERE d.id=?
            """, (dual_id,)).fetchone()
        else:
            dual = conn.execute("""
                SELECT d.*, s.division, s.gender
                FROM duals d JOIN seasons s ON s.id=d.season_id WHERE d.id=?
            """, (dual_id,)).fetchone()
        conn.close()
        if not dual:
            return None
        d = dict(dual)
        d["lines"] = json.loads(d["lines_json"]) if d.get("lines_json") else []
        d["source"] = source
        return d
    except Exception:
        return None
