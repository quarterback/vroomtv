"""Read-only adapter for hybrid-baseball o27v2 SQLite database."""
from __future__ import annotations
import os
import sqlite3
import zlib
from typing import Any


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


def get_batting_leaders(limit: int = 10) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        season = _current_season(conn)
        rows = conn.execute("""
            SELECT b.player_name, b.team_abbrev,
                   b.g, b.ab, b.h, b.hr, b.rbi, b.bb, b.k,
                   CASE WHEN b.ab > 0 THEN ROUND(CAST(b.h AS REAL)/b.ab, 3) ELSE 0 END AS avg
            FROM season_player_batting b
            WHERE b.season_id = (SELECT id FROM seasons WHERE season_number = ?)
              AND b.ab >= 20
            ORDER BY avg DESC
            LIMIT ?
        """, (season, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_pitching_leaders(limit: int = 10) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        season = _current_season(conn)
        rows = conn.execute("""
            SELECT p.player_name, p.team_abbrev,
                   p.g, p.gs, p.w, p.l, p.k, p.er, p.outs,
                   CASE WHEN p.outs > 0
                        THEN ROUND(CAST(p.er AS REAL) * 27.0 / p.outs, 2)
                        ELSE 0 END AS era
            FROM season_player_pitching p
            WHERE p.season_id = (SELECT id FROM seasons WHERE season_number = ?)
              AND p.outs >= 15
            ORDER BY era ASC
            LIMIT ?
        """, (season, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
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
