"""Read-only adapter for hybrid-baseball o27v2 SQLite database."""
from __future__ import annotations
import os
import sqlite3
import json
import zlib
from typing import Any


def _portal_leaders() -> dict | None:
    """Advanced-stat leaders the baseball portal computes (wOBA, OPS+,
    K%/BB%, K/9, WHIP). Returns None if not synced."""
    db = _db_path()
    if not db:
        return None
    path = os.path.join(os.path.dirname(db) or ".", "baseball_leaders.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_path() -> str | None:
    return os.environ.get("BASEBALL_DB") or None


def _current_season(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(season) FROM games WHERE played=1").fetchone()
    return row[0] or 1


def get_recent_scores(limit: int = 15) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        rows = conn.execute("""
            SELECT g.id, g.game_date, g.home_score, g.away_score,
                   ht.name AS home_name, ht.abbrev AS home_abbrev,
                   at.name AS away_name, at.abbrev AS away_abbrev,
                   g.is_playoff
            FROM games g
            JOIN teams ht ON ht.id = g.home_team_id
            JOIN teams at ON at.id = g.away_team_id
            WHERE g.played = 1
            ORDER BY g.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


_EXTRA_QUERIES = [
    ("College", """
        SELECT hp.name AS home_name, ap.name AS away_name,
               g.home_score, g.away_score, g.phase AS note
        FROM college_games g
        JOIN college_programs hp ON hp.id = g.home_program_id
        JOIN college_programs ap ON ap.id = g.away_program_id
        WHERE g.played = 1 ORDER BY g.id DESC LIMIT ?"""),
    ("Youth Cup", """
        SELECT ht.name AS home_name, at.name AS away_name,
               g.home_score, g.away_score, g.bracket_round AS note
        FROM youth_games g
        JOIN youth_teams ht ON ht.id = g.home_team_id
        JOIN youth_teams at ON at.id = g.away_team_id
        WHERE g.played = 1 ORDER BY g.id DESC LIMIT ?"""),
    ("World Cup", """
        SELECT ht.name AS home_name, at.name AS away_name,
               g.home_score, g.away_score, g.phase AS note
        FROM wc_games g
        JOIN wc_teams ht ON ht.id = g.home_wc_team_id
        JOIN wc_teams at ON at.id = g.away_wc_team_id
        WHERE g.played = 1 ORDER BY g.id DESC LIMIT ?"""),
]


def get_extra_scores(limit_per_league: int = 8) -> list[dict]:
    """College / youth / World Cup games — separate competitions that live
    in the same o27v2 DB. Each is optional: tables only exist once that
    mode has been played, so per-league failures are silently skipped.
    Items now carry id so the Rocky can link to a tier-specific game page."""
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    queries = [
        ("College", "college", """
            SELECT g.id, hp.name AS home_name, ap.name AS away_name,
                   g.home_score, g.away_score, g.phase AS note
            FROM college_games g
            JOIN college_programs hp ON hp.id = g.home_program_id
            JOIN college_programs ap ON ap.id = g.away_program_id
            WHERE g.played = 1 ORDER BY g.id DESC LIMIT ?"""),
        ("Youth Cup", "youth", """
            SELECT g.id, ht.name AS home_name, at.name AS away_name,
                   g.home_score, g.away_score, g.bracket_round AS note
            FROM youth_games g
            JOIN youth_teams ht ON ht.id = g.home_team_id
            JOIN youth_teams at ON at.id = g.away_team_id
            WHERE g.played = 1 ORDER BY g.id DESC LIMIT ?"""),
        ("World Cup", "wc", """
            SELECT g.id, ht.name AS home_name, at.name AS away_name,
                   g.home_score, g.away_score, g.phase AS note
            FROM wc_games g
            JOIN wc_teams ht ON ht.id = g.home_wc_team_id
            JOIN wc_teams at ON at.id = g.away_wc_team_id
            WHERE g.played = 1 ORDER BY g.id DESC LIMIT ?"""),
    ]
    out = []
    try:
        conn = _conn(path)
    except Exception:
        return []
    for league, tier, sql in queries:
        try:
            for r in conn.execute(sql, (limit_per_league,)).fetchall():
                d = dict(r); d["league"] = league; d["tier"] = tier
                out.append(d)
        except Exception:
            continue
    conn.close()
    return out


_TIER_TABLES = {
    "college": {"games": "college_games", "batters": "college_batter_stats",
                "pitchers": "college_pitcher_stats", "teams": "college_programs",
                "team_pk": "id", "home_fk": "home_program_id", "away_fk": "away_program_id",
                "team_side": "program_id"},
    "wc": {"games": "wc_games", "batters": "game_wc_batter_stats",
           "pitchers": "game_wc_pitcher_stats", "teams": "wc_teams",
           "team_pk": "id", "home_fk": "home_wc_team_id", "away_fk": "away_wc_team_id",
           "team_side": "wc_team_id"},
    "youth": {"games": "youth_games", "batters": "game_youth_batter_stats",
              "pitchers": "game_youth_pitcher_stats", "teams": "youth_teams",
              "team_pk": "id", "home_fk": "home_team_id", "away_fk": "away_team_id",
              "team_side": "team_id"},
}


def get_extra_game_detail(tier: str, game_id: int) -> dict[str, Any] | None:
    """Box score for a college / youth-cup / World Cup game. Returns the
    same shape as get_game_detail so the template can be shared."""
    if tier not in _TIER_TABLES:
        return None
    cfg = _TIER_TABLES[tier]
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        game = conn.execute(f"""
            SELECT g.*, ht.name AS home_name, at.name AS away_name,
                   ht.id AS home_team_id, at.id AS away_team_id
            FROM {cfg['games']} g
            JOIN {cfg['teams']} ht ON ht.{cfg['team_pk']} = g.{cfg['home_fk']}
            JOIN {cfg['teams']} at ON at.{cfg['team_pk']} = g.{cfg['away_fk']}
            WHERE g.id = ?
        """, (game_id,)).fetchone()
        if not game:
            conn.close()
            return None
        batters = conn.execute(f"""
            SELECT b.*, b.{cfg['team_side']} AS team_id,
                   COALESCE(pl.name, '') AS player_name
            FROM {cfg['batters']} b
            LEFT JOIN players pl ON pl.id = b.player_id
            WHERE b.game_id = ? ORDER BY b.{cfg['team_side']}, b.player_id
        """, (game_id,)).fetchall()
        pitchers = conn.execute(f"""
            SELECT p.*, p.{cfg['team_side']} AS team_id,
                   COALESCE(pl.name, '') AS player_name
            FROM {cfg['pitchers']} p
            LEFT JOIN players pl ON pl.id = p.player_id
            WHERE p.game_id = ? ORDER BY p.{cfg['team_side']}, p.player_id
        """, (game_id,)).fetchall()
        conn.close()
        # Normalize column names across tiers so the template doesn't care.
        def _norm_b(r):
            d = dict(r)
            d.setdefault("runs", d.get("r", 0))
            d.setdefault("hits", d.get("h", 0))
            return d
        def _norm_p(r):
            d = dict(r)
            d.setdefault("hits_allowed", d.get("h", 0))
            d.setdefault("runs_allowed", d.get("r", 0))
            d.setdefault("er", d.get("er", 0))
            d.setdefault("hr_allowed", d.get("hr", 0))
            d.setdefault("outs_recorded", d.get("outs", 0))
            d.setdefault("batters_faced", d.get("bf", 0))
            return d
        return {"game": dict(game),
                "batters": [_norm_b(b) for b in batters],
                "pitchers": [_norm_p(p) for p in pitchers],
                "pbp": "", "tier": tier}
    except Exception:
        return None


def get_standings() -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        rows = conn.execute("""
            SELECT name, abbrev, wins, losses, division, league
            FROM teams
            ORDER BY league, division, wins DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _qual_floors(conn: sqlite3.Connection, season: int) -> tuple[int, int]:
    """Qualification floors that scale with how far the season has gone
    (a fixed floor empties the boards early in a season): ~2 AB and ~3
    outs per team-game, with small absolute minimums."""
    row = conn.execute(
        "SELECT COUNT(*), (SELECT COUNT(*) FROM teams) FROM games"
        " WHERE season = ? AND played = 1", (season,)).fetchone()
    played, teams = row[0] or 0, row[1] or 1
    team_games = 2 * played / teams
    return max(8, int(team_games * 2)), max(6, int(team_games * 3))


def get_batting_leaders(limit: int = 10) -> list[dict]:
    portal = _portal_leaders()
    if portal and portal.get("batting"):
        return portal["batting"][:limit]
    return _db_batting_leaders(limit)


def _db_batting_leaders(limit: int = 10) -> list[dict]:
    """Current-season batting leaders, aggregated from the per-game tables.

    The season_player_* rollups are only written when a season is archived,
    so mid-season the game tables are the one true source."""
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        season = _current_season(conn)
        rows = conn.execute("""
            SELECT p.name AS player_name, t.abbrev AS team_abbrev,
                   COUNT(DISTINCT b.game_id) AS g, SUM(b.ab) AS ab,
                   SUM(b.hits) AS h, SUM(b.hr) AS hr, SUM(b.rbi) AS rbi,
                   SUM(b.bb) AS bb, SUM(b.k) AS k,
                   ROUND(CAST(SUM(b.hits) AS REAL) / SUM(b.ab), 3) AS avg
            FROM game_batter_stats b
            JOIN games gm ON gm.id = b.game_id AND gm.season = ? AND gm.played = 1
            JOIN players p ON p.id = b.player_id
            JOIN teams t ON t.id = b.team_id
            WHERE b.phase = 0 AND b.is_playoff = 0
            GROUP BY b.player_id
            HAVING SUM(b.ab) >= ?
            ORDER BY avg DESC
            LIMIT ?
        """, (season, _qual_floors(conn, season)[0], limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_pitching_leaders(limit: int = 10) -> list[dict]:
    portal = _portal_leaders()
    if portal and portal.get("pitching"):
        return portal["pitching"][:limit]
    return _db_pitching_leaders(limit)


def _db_pitching_leaders(limit: int = 10) -> list[dict]:
    """Current-season ERA leaders from the per-game tables (see batting)."""
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        season = _current_season(conn)
        rows = conn.execute("""
            SELECT pl.name AS player_name, t.abbrev AS team_abbrev,
                   COUNT(DISTINCT p.game_id) AS g, SUM(p.k) AS k,
                   SUM(p.er) AS er, SUM(p.outs_recorded) AS outs,
                   ROUND(CAST(SUM(p.er) AS REAL) * 27.0 / SUM(p.outs_recorded), 2) AS era
            FROM game_pitcher_stats p
            JOIN games gm ON gm.id = p.game_id AND gm.season = ? AND gm.played = 1
            JOIN players pl ON pl.id = p.player_id
            JOIN teams t ON t.id = p.team_id
            WHERE p.phase = 0 AND p.is_playoff = 0
            GROUP BY p.player_id
            HAVING SUM(p.outs_recorded) >= ?
            ORDER BY era ASC
            LIMIT ?
        """, (season, _qual_floors(conn, season)[1], limit)).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["ip"] = f"{d['outs'] // 3}.{d['outs'] % 3}"  # innings, baseball-style
            out.append(d)
        return out
    except Exception:
        return []


def _split_article(article: str) -> tuple[str, str]:
    """(headline, body) from gazette prose — first non-empty line is the head."""
    lines = article.strip().splitlines()
    head = ""
    while lines and not head:
        head = lines.pop(0).strip().lstrip("#").strip()
    return head, "\n".join(lines).strip()


def get_news(limit: int = 6) -> list[dict]:
    """Gazette articles cached in the sim DB (one per slate_date+voice).

    The table only exists once the sim has generated an article, so a
    missing table is normal, not an error.
    """
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        rows = conn.execute("""
            SELECT slate_date, voice_id, article, created_at
            FROM gazette_articles
            ORDER BY slate_date DESC, created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
    except Exception:
        return []
    out = []
    for r in rows:
        head, body = _split_article(r["article"])
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        out.append({
            "slate_date": r["slate_date"],
            "voice_id": r["voice_id"],
            "headline": head or f"The Gazette — {r['slate_date']}",
            "lede": paragraphs[0] if paragraphs else "",
            "paragraphs": paragraphs,
            "art_seed": zlib.crc32(f"{r['slate_date']}|{r['voice_id']}".encode()),
        })
    return out


def get_article(slate_date: str, voice_id: str) -> dict[str, Any] | None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        r = conn.execute(
            "SELECT slate_date, voice_id, article, created_at FROM gazette_articles"
            " WHERE slate_date=? AND voice_id=?", (slate_date, voice_id)
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not r:
        return None
    head, body = _split_article(r["article"])
    return {
        "slate_date": r["slate_date"],
        "voice_id": r["voice_id"],
        "headline": head or f"The Gazette — {r['slate_date']}",
        "paragraphs": [p.strip() for p in body.split("\n\n") if p.strip()],
    }


def get_game_detail(game_id: int) -> dict[str, Any] | None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        game = conn.execute("""
            SELECT g.*, ht.name AS home_name, ht.abbrev AS home_abbrev,
                         at.name AS away_name, at.abbrev AS away_abbrev
            FROM games g
            JOIN teams ht ON ht.id = g.home_team_id
            JOIN teams at ON at.id = g.away_team_id
            WHERE g.id = ?
        """, (game_id,)).fetchone()
        if not game:
            conn.close()
            return None
        batters = conn.execute("""
            SELECT b.*, p.name AS player_name, t.abbrev AS team_abbrev
            FROM game_batter_stats b
            JOIN players p ON p.id = b.player_id
            JOIN teams t ON t.id = b.team_id
            WHERE b.game_id = ? AND b.phase = 0
            ORDER BY b.team_id, b.id
        """, (game_id,)).fetchall()
        pitchers = conn.execute("""
            SELECT p2.*, pl.name AS player_name, t.abbrev AS team_abbrev
            FROM game_pitcher_stats p2
            JOIN players pl ON pl.id = p2.player_id
            JOIN teams t ON t.id = p2.team_id
            WHERE p2.game_id = ? AND p2.phase = 0
            ORDER BY p2.team_id, p2.id
        """, (game_id,)).fetchall()
        pbp_row = conn.execute(
            "SELECT pbp_text FROM game_pbp WHERE game_id = ?", (game_id,)
        ).fetchone()
        conn.close()
        return {
            "game": dict(game),
            "batters": [dict(b) for b in batters],
            "pitchers": [dict(p) for p in pitchers],
            "pbp": pbp_row["pbp_text"] if pbp_row else "",
        }
    except Exception:
        return None
