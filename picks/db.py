"""PFS — Peak Fantasy Sports: writable SQLite for picks, wallets, leaderboard."""
from __future__ import annotations
import os
import sqlite3

_SCHEMA_CREATED = False


def _path() -> str:
    return os.environ.get("PICKS_DB") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "picks.db"
    )


def get_conn() -> sqlite3.Connection:
    path = _path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def ensure_schema() -> None:
    global _SCHEMA_CREATED
    if _SCHEMA_CREATED:
        return
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS participants (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL UNIQUE,
            is_human    INTEGER NOT NULL DEFAULT 0,
            skill_level REAL NOT NULL DEFAULT 0.5,
            joined_week TEXT
        );
        CREATE TABLE IF NOT EXISTS human_wallet (
            week_key        TEXT PRIMARY KEY,
            zoras_remaining INTEGER NOT NULL DEFAULT 1000
        );
        CREATE TABLE IF NOT EXISTS weekly_slate (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            week_key    TEXT NOT NULL,
            sport       TEXT NOT NULL,
            game_id     TEXT NOT NULL UNIQUE,
            home_team   TEXT NOT NULL,
            away_team   TEXT NOT NULL,
            point_value INTEGER NOT NULL,
            winner      TEXT NOT NULL,
            settled     INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS picks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id  INTEGER NOT NULL,
            slate_id        INTEGER NOT NULL,
            week_key        TEXT NOT NULL,
            picked_team     TEXT NOT NULL,
            correct         INTEGER,
            points_earned   INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(participant_id, slate_id),
            FOREIGN KEY(participant_id) REFERENCES participants(id),
            FOREIGN KEY(slate_id) REFERENCES weekly_slate(id)
        );
        CREATE TABLE IF NOT EXISTS weekly_leaderboard (
            participant_id  INTEGER NOT NULL,
            week_key        TEXT NOT NULL,
            total_points    INTEGER NOT NULL DEFAULT 0,
            picks_correct   INTEGER NOT NULL DEFAULT 0,
            picks_total     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(participant_id, week_key),
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
    """)
    conn.commit()
    conn.close()
    _SCHEMA_CREATED = True


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    conn = get_conn()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> int:
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    lastrowid = cur.lastrowid
    conn.close()
    return lastrowid


def executemany(sql: str, param_list: list) -> None:
    conn = get_conn()
    conn.executemany(sql, param_list)
    conn.commit()
    conn.close()
