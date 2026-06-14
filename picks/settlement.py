"""PFS settlement: score picks, generate AI picks, publish leaderboard."""
from __future__ import annotations
import random
from . import db
from .slate import POINTS_PER_SEASON, season_prefix


def settle_week(week_key: str) -> dict:
    """Score all human picks, generate AI picks, rebuild leaderboard."""
    slate = db.fetchall(
        "SELECT id, sport, home_team, away_team, point_value, winner "
        "FROM weekly_slate WHERE week_key=? AND settled=0",
        (week_key,)
    )
    if not slate:
        return {"ok": False, "error": "No unsettled games on this week's slate.",
                "settled": 0, "ai_picks": 0}

    # Score existing human picks
    human_scored = 0
    for game in slate:
        human_picks = db.fetchall(
            "SELECT id, picked_team FROM picks WHERE slate_id=? AND correct IS NULL",
            (game["id"],)
        )
        for pick in human_picks:
            correct = int(
                (pick["picked_team"] == game["home_team"] and game["winner"] == "home") or
                (pick["picked_team"] == game["away_team"] and game["winner"] == "away")
            )
            points = game["point_value"] if correct else 0
            db.execute(
                "UPDATE picks SET correct=?, points_earned=? WHERE id=?",
                (correct, points, pick["id"])
            )
            human_scored += 1
        db.execute("UPDATE weekly_slate SET settled=1 WHERE id=?", (game["id"],))

    # Generate AI picks at settlement time
    ai_participants = db.fetchall(
        "SELECT id, skill_level FROM participants WHERE is_human=0"
    )
    rng = random.Random()
    ai_rows: list[tuple] = []

    for p in ai_participants:
        skill = p["skill_level"]
        # Higher skill → fewer but more selective picks
        # skill 0.67 → ~15 picks; 0.52 → ~30; 0.40 → ~45
        pick_count = max(5, min(50, int(15 + (0.67 - skill) / 0.27 * 30)))
        games_to_pick = rng.sample(slate, min(pick_count, len(slate)))

        for game in games_to_pick:
            pick_correct = rng.random() < skill
            if pick_correct:
                picked = game["home_team"] if game["winner"] == "home" else game["away_team"]
                correct, points = 1, game["point_value"]
            else:
                picked = game["away_team"] if game["winner"] == "home" else game["home_team"]
                correct, points = 0, 0
            ai_rows.append((p["id"], game["id"], week_key, picked, correct, points))

    if ai_rows:
        db.executemany(
            "INSERT OR IGNORE INTO picks "
            "(participant_id, slate_id, week_key, picked_team, correct, points_earned) "
            "VALUES (?,?,?,?,?,?)",
            ai_rows
        )

    _rebuild_leaderboard(week_key)
    return {
        "ok": True,
        "settled": len(slate),
        "human_scored": human_scored,
        "ai_picks": len(ai_rows),
    }


def _rebuild_leaderboard(week_key: str) -> None:
    rows = db.fetchall("""
        SELECT participant_id,
               SUM(points_earned)  AS total_points,
               SUM(correct)        AS picks_correct,
               COUNT(*)            AS picks_total
        FROM picks WHERE week_key=? AND correct IS NOT NULL
        GROUP BY participant_id
    """, (week_key,))

    db.executemany("""
        INSERT INTO weekly_leaderboard
            (participant_id, week_key, total_points, picks_correct, picks_total)
        VALUES (?,?,?,?,?)
        ON CONFLICT(participant_id, week_key) DO UPDATE SET
            total_points  = excluded.total_points,
            picks_correct = excluded.picks_correct,
            picks_total   = excluded.picks_total
    """, [(r["participant_id"], week_key,
           r["total_points"] or 0,
           r["picks_correct"] or 0,
           r["picks_total"] or 0) for r in rows])


def advance_week() -> dict:
    """Move to the next week of the current season.

    The season bankroll carries over — only the slate is fresh (empty until the
    commissioner refreshes it). Returns the new season/week.
    """
    db.execute("UPDATE season_state SET week = week + 1 WHERE id=1")
    s = db.fetchone("SELECT season, week FROM season_state WHERE id=1")
    return {"ok": True, "season": s["season"], "week": s["week"],
            "week_key": f"S{s['season']}-W{s['week']:02d}"}


def new_season() -> dict:
    """Wipe all play data and start a brand-new season at Week 1.

    Clears slates, picks, leaderboards, and the bankroll, then resets the season
    pointer. No history is kept — this is a clean slate.
    """
    # picks reference weekly_slate via a foreign key, so clear them first.
    db.execute("DELETE FROM picks")
    db.execute("DELETE FROM weekly_leaderboard")
    db.execute("DELETE FROM weekly_slate")
    db.execute("DELETE FROM human_wallet")
    db.execute("UPDATE season_state SET season = 1, week = 1 WHERE id=1")
    return {"ok": True, "points": POINTS_PER_SEASON}


def get_season_leaderboard(season: int | None = None, limit: int = 100) -> list[dict]:
    """Cumulative season standings across every settled week of the season."""
    prefix = season_prefix(season)
    rows = db.fetchall("""
        SELECT lb.participant_id, p.username, p.is_human,
               SUM(lb.total_points)  AS total_points,
               SUM(lb.picks_correct) AS picks_correct,
               SUM(lb.picks_total)   AS picks_total
        FROM weekly_leaderboard lb
        JOIN participants p ON p.id = lb.participant_id
        WHERE lb.week_key LIKE ?
        GROUP BY lb.participant_id
        ORDER BY total_points DESC, picks_correct DESC
        LIMIT ?
    """, (prefix + "%", limit))
    out = []
    for i, r in enumerate(rows, 1):
        pct = round(r["picks_correct"] / r["picks_total"] * 100) if r["picks_total"] else 0
        out.append({**r, "rank": i, "win_pct": pct})
    return out


def get_season_human_rank(season: int | None, human_id: int) -> int | None:
    """The human's overall rank across the whole season."""
    prefix = season_prefix(season)
    rows = db.fetchall("""
        SELECT lb.participant_id,
               SUM(lb.total_points)  AS total_points,
               SUM(lb.picks_correct) AS picks_correct
        FROM weekly_leaderboard lb
        WHERE lb.week_key LIKE ?
        GROUP BY lb.participant_id
        ORDER BY total_points DESC, picks_correct DESC
    """, (prefix + "%",))
    for i, r in enumerate(rows, 1):
        if r["participant_id"] == human_id:
            return i
    return None


def get_leaderboard(week_key: str, limit: int = 50) -> list[dict]:
    """Top N for the week, with rank and human flag."""
    rows = db.fetchall("""
        SELECT lb.participant_id, p.username, p.is_human,
               lb.total_points, lb.picks_correct, lb.picks_total
        FROM weekly_leaderboard lb
        JOIN participants p ON p.id = lb.participant_id
        WHERE lb.week_key = ?
        ORDER BY lb.total_points DESC, lb.picks_correct DESC
        LIMIT ?
    """, (week_key, limit))
    out = []
    for i, r in enumerate(rows, 1):
        pct = round(r["picks_correct"] / r["picks_total"] * 100) if r["picks_total"] else 0
        out.append({**r, "rank": i, "win_pct": pct})
    return out


def get_human_rank(week_key: str, human_id: int) -> int | None:
    """Return the human's rank for the week (1-indexed), or None if unranked."""
    rows = db.fetchall("""
        SELECT participant_id FROM weekly_leaderboard
        WHERE week_key=? ORDER BY total_points DESC, picks_correct DESC
    """, (week_key,))
    for i, r in enumerate(rows, 1):
        if r["participant_id"] == human_id:
            return i
    return None


def get_my_stats(week_key: str, human_id: int) -> dict:
    """Human stats for the current week and career."""
    week = db.fetchone("""
        SELECT total_points, picks_correct, picks_total
        FROM weekly_leaderboard WHERE participant_id=? AND week_key=?
    """, (human_id, week_key)) or {"total_points": 0, "picks_correct": 0, "picks_total": 0}

    career = db.fetchone("""
        SELECT SUM(total_points) AS pts, SUM(picks_correct) AS correct, SUM(picks_total) AS total,
               COUNT(DISTINCT week_key) AS weeks
        FROM weekly_leaderboard WHERE participant_id=?
    """, (human_id,)) or {"pts": 0, "correct": 0, "total": 0, "weeks": 0}

    week_pct = round(week["picks_correct"] / week["picks_total"] * 100) if week["picks_total"] else 0
    car_pct = round((career["correct"] or 0) / (career["total"] or 1) * 100)

    return {
        "week": {**week, "win_pct": week_pct},
        "career": {
            "total_points": career["pts"] or 0,
            "picks_correct": career["correct"] or 0,
            "picks_total": career["total"] or 0,
            "weeks_played": career["weeks"] or 0,
            "win_pct": car_pct,
        },
    }
