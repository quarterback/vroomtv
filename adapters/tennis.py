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


def _school_confs(conn, season_id: int) -> dict[str, str]:
    """school → conference, derived from conference duals (both sides of an
    is_conf dual belong to that dual's conference)."""
    out: dict[str, str] = {}
    for r in conn.execute(
        "SELECT home, away, conf FROM duals WHERE season_id=? AND is_conf=1 AND conf IS NOT NULL",
        (season_id,)
    ).fetchall():
        out.setdefault(r["home"], r["conf"])
        out.setdefault(r["away"], r["conf"])
    return out


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
                SELECT id, week, home, away, home_points, away_points, conf, is_conf
                FROM duals WHERE season_id = ? AND status = 'final'
                ORDER BY id DESC LIMIT ?
            """, (s["id"], limit_per_source)).fetchall()
            for r in rows:
                out.append({
                    "source": "ncaa", "league": label, "season_id": s["id"],
                    "id": r["id"], "week": r["week"],
                    "home_name": r["home"], "away_name": r["away"],
                    "home_points": r["home_points"], "away_points": r["away_points"],
                    "conf": r["conf"] if r["is_conf"] else "",
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
            out.append({"league": lg["name"], "source": "gtt", "tier": "Pro",
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
            confs = _school_confs(conn, s["id"])
            agg: dict[str, dict] = {}
            for r in rows:
                sch = r["school"]
                if sch not in agg:
                    agg[sch] = {"name": sch, "wins": 0, "losses": 0,
                                "conf": confs.get(sch, "")}
                agg[sch]["wins"] += r["wins"]
                agg[sch]["losses"] += r["losses"]
            out.append({"league": label, "source": "ncaa", "tier": "College",
                        "teams": sorted(agg.values(), key=lambda t: (-t["wins"], t["losses"]))})
        conn.close()
    except Exception:
        pass
    return out


_leaders_cache: dict = {"key": None, "leagues": [], "building": False}


def _portal_data() -> dict | None:
    """The tennis stats portal's JSON export (live rankings via the
    season's power index, player STR ratings, junior prospects). Returns
    None when not synced — the basic DB adapter still works."""
    path = _db_path()
    if not path:
        return None
    fp = os.path.join(os.path.dirname(path) or ".", "tennis_portal.json")
    try:
        with open(fp) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def get_portal_universes() -> list[dict]:
    """Public passthrough so the app can render the rich portal view."""
    blob = _portal_data()
    return blob.get("universes", []) if blob else []


def has_gtt() -> bool:
    """Cheap existence check so callers can skip the expensive DB-derived
    leader aggregation when the portal covers everything else."""
    path = _db_path()
    if not path or not os.path.exists(path):
        return False
    try:
        conn = _conn(path)
        n = conn.execute("SELECT COUNT(*) FROM gtt_leagues").fetchone()[0]
        conn.close()
        return bool(n)
    except Exception:
        return False


def get_stat_leaders(limit: int = 10, min_matches: int = 3) -> list[dict]:
    """Singles match-win leaders per league, aggregated from dual line
    scores: [{"league": label, "leaders": [...]}].

    Season play persists per-match results only inside lines_json (the
    matches/match_stats tables are filled solely by the one-off CLI sims,
    and fast-fidelity duals zero their stat blocks), so wins are the one
    stat reliably available. Singles only: GTT slots MS*/WS*, NCAA S*.

    A full world is thousands of duals (each a JSON parse), so the result
    is cached against the DB file's mtime — it only changes on sync.
    """
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        cache_key = (path, os.path.getmtime(path), limit, min_matches)
    except OSError:
        return []
    if _leaders_cache["key"] == cache_key:
        return _leaders_cache["leagues"]
    # Stale-while-revalidate: a full world is thousands of dual JSON
    # parses — never run that on a visitor's request when an older result
    # can be served while a background thread rebuilds.
    if _leaders_cache["leagues"] and _leaders_cache["key"] is not None:
        if not _leaders_cache["building"]:
            _leaders_cache["building"] = True
            import threading
            threading.Thread(target=_build_stat_leaders,
                             args=(limit, min_matches, cache_key),
                             daemon=True, name="tennis-leaders-rebuild").start()
        return _leaders_cache["leagues"]
    return _build_stat_leaders(limit, min_matches, cache_key)


def _build_stat_leaders(limit: int, min_matches: int, cache_key) -> list[dict]:
    path = _db_path()
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
        _leaders_cache["building"] = False
        return []
    by_league: dict[str, list] = {}
    for r in tally.values():
        if r["matches"] < min_matches:
            continue
        r["win_pct"] = r["wins"] / r["matches"]
        by_league.setdefault(r["league"], []).append(r)
    gtt_names = {info[2] for info in pid_info.values()} if pid_info else set()
    leagues = []
    for label, rows in by_league.items():
        rows.sort(key=lambda r: (-r["wins"], -r["win_pct"]))
        leagues.append({"league": label, "leaders": rows[:limit],
                        "tier": "Pro" if label in gtt_names else "College"})
    _leaders_cache["key"] = cache_key
    _leaders_cache["leagues"] = leagues
    _leaders_cache["building"] = False
    return leagues


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
        if not dual:
            conn.close()
            return None
        d = dict(dual)
        d["lines"] = json.loads(d["lines_json"]) if d.get("lines_json") else []
        d["source"] = source
        if source == "gtt":
            # GTT lines carry pids, not names — resolve via the league roster.
            names = {}
            for p in conn.execute(
                "SELECT pid, data FROM gtt_players WHERE league_id=?", (d["league_id"],)
            ).fetchall():
                try:
                    names[p["pid"]] = json.loads(p["data"]).get("name", p["pid"])
                except (json.JSONDecodeError, TypeError):
                    names[p["pid"]] = p["pid"]
            for line in d["lines"]:
                line["home_names"] = [names.get(p, p) for p in line.get("home_pids", [])]
                line["away_names"] = [names.get(p, p) for p in line.get("away_pids", [])]
        conn.close()
        return d
    except Exception:
        return None
