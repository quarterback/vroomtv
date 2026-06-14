"""PFS slate: pull completed games from all three sport adapters."""
from __future__ import annotations
import random

from adapters import baseball, viperball, tennis
from . import db

# Points are the player's spendable budget — one bankroll for the whole season.
POINTS_PER_SEASON = 1000
POINTS_PER_PICK = 20
_POINT_MIN = 7499
_POINT_MAX = 11205


def current_season() -> int:
    s = db.fetchone("SELECT season FROM season_state WHERE id=1")
    return s["season"] if s else 1


def current_week() -> str:
    """The active week key, e.g. 'S1-W03'. Driven by season_state, not the calendar."""
    s = db.fetchone("SELECT season, week FROM season_state WHERE id=1")
    if not s:
        return "S1-W01"
    return f"S{s['season']}-W{s['week']:02d}"


def season_prefix(season: int | None = None) -> str:
    """The week_key prefix that groups all weeks belonging to a season."""
    return f"S{current_season() if season is None else season}-"


def _rand_points(rng: random.Random | None = None) -> int:
    r = rng or random
    return r.randint(_POINT_MIN, _POINT_MAX)


def _gather_games(limit: int = 50) -> list[dict]:
    """Pull recently completed games from all adapters, normalized to a common shape."""
    games: list[dict] = []

    try:
        for g in baseball.get_recent_scores(limit=limit):
            hs, as_ = g.get("home_score", 0) or 0, g.get("away_score", 0) or 0
            if hs == as_:
                continue
            games.append({
                "sport": "Baseball",
                "game_id": f"bb_{g['id']}",
                "home_team": g["home_name"],
                "away_team": g["away_name"],
                "winner": "home" if hs > as_ else "away",
            })
    except Exception:
        pass

    try:
        for g in viperball.get_recent_scores(limit_per_league=limit):
            hs = float(g.get("home_score") or 0)
            as_ = float(g.get("away_score") or 0)
            if hs == as_:
                continue
            gid = f"vb_{g['save_key']}_{g['week']}_{g['matchup_key']}"
            games.append({
                "sport": "Viperball",
                "game_id": gid,
                "home_team": g["home_name"],
                "away_team": g["away_name"],
                "winner": "home" if hs > as_ else "away",
            })
    except Exception:
        pass

    try:
        for g in tennis.get_recent_scores(limit_per_source=limit):
            hp = g.get("home_points") or 0
            ap = g.get("away_points") or 0
            if hp == ap:
                continue
            gid = f"tn_{g['source']}_{g['id']}"
            games.append({
                "sport": "Tennis",
                "game_id": gid,
                "home_team": g["home_name"],
                "away_team": g["away_name"],
                "winner": "home" if hp > ap else "away",
            })
    except Exception:
        pass

    return games


def build_slate(week_key: str, max_games: int = 30) -> int:
    """Add new completed games to the given week's slate. Returns count added.

    Games are deduped per-week (not globally), so the same completed source game
    can appear again in a later week. The stored game_id is week-prefixed to keep
    it globally unique while still allowing reuse across weeks.
    """
    used = {r["game_id"] for r in db.fetchall(
        "SELECT game_id FROM weekly_slate WHERE week_key=?", (week_key,)
    )}
    current_count = len(used)

    candidates = _gather_games()
    rng = random.Random()
    added = 0
    for g in candidates:
        if current_count + added >= max_games:
            break
        stored_id = f"{week_key}:{g['game_id']}"
        if stored_id in used:
            continue
        try:
            db.execute(
                "INSERT OR IGNORE INTO weekly_slate "
                "(week_key, sport, game_id, home_team, away_team, point_value, winner, settled) "
                "VALUES (?,?,?,?,?,?,?,0)",
                (week_key, g["sport"], stored_id, g["home_team"],
                 g["away_team"], _rand_points(rng), g["winner"])
            )
            added += 1
        except Exception:
            pass
    return added


def get_slate(week_key: str, human_id: int) -> list[dict]:
    """Return the slate for the week. Winner is hidden until settled."""
    rows = db.fetchall(
        "SELECT id, sport, home_team, away_team, point_value, settled "
        "FROM weekly_slate WHERE week_key=? ORDER BY sport, id",
        (week_key,)
    )
    picks_map = {r["slate_id"]: r for r in db.fetchall(
        "SELECT slate_id, picked_team, correct, points_earned FROM picks "
        "WHERE participant_id=? AND week_key=?",
        (human_id, week_key)
    )}
    out = []
    for r in rows:
        p = picks_map.get(r["id"])
        # Only reveal winner/result if settled
        entry: dict = {**r}
        if r["settled"]:
            entry["winner"] = db.fetchone(
                "SELECT winner FROM weekly_slate WHERE id=?", (r["id"],)
            )["winner"]
        else:
            entry["winner"] = None
        entry["picked"] = p["picked_team"] if p else None
        entry["correct"] = p["correct"] if p else None
        entry["points_earned"] = p["points_earned"] if p else 0
        out.append(entry)
    return out


def _wallet_key(season: int | None = None) -> str:
    """The human_wallet row key for a season's bankroll, e.g. 'S1'."""
    return f"S{current_season() if season is None else season}"


def get_wallet() -> int:
    """Points remaining in the current season's bankroll."""
    r = db.fetchone(
        "SELECT zoras_remaining FROM human_wallet WHERE week_key=?", (_wallet_key(),)
    )
    return r["zoras_remaining"] if r else POINTS_PER_SEASON


def debit(amount: int = POINTS_PER_PICK) -> bool:
    """Deduct points from the season bankroll. Returns False if insufficient funds."""
    if get_wallet() < amount:
        return False
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO human_wallet (week_key, zoras_remaining) VALUES (?,?) "
        "ON CONFLICT(week_key) DO UPDATE SET zoras_remaining = zoras_remaining - ?",
        (_wallet_key(), POINTS_PER_SEASON - amount, amount)
    )
    conn.commit()
    conn.close()
    return True


def submit_pick(human_id: int, slate_id: int, week_key: str, picked_team: str) -> dict:
    """Record a human pick. Returns {ok, error?, zoras_remaining?}."""
    existing = db.fetchone(
        "SELECT id FROM picks WHERE participant_id=? AND slate_id=?",
        (human_id, slate_id)
    )
    if existing:
        return {"ok": False, "error": "Already picked this game."}

    game = db.fetchone(
        "SELECT id, week_key, home_team, away_team, settled FROM weekly_slate WHERE id=?",
        (slate_id,)
    )
    if not game:
        return {"ok": False, "error": "Game not found."}
    if game["week_key"] != week_key:
        return {"ok": False, "error": "Game is not on this week's slate."}
    if game["settled"]:
        return {"ok": False, "error": "This game has already been settled."}
    if picked_team not in (game["home_team"], game["away_team"]):
        return {"ok": False, "error": "Invalid team selection."}

    if not debit():
        return {"ok": False,
                "error": f"Not enough points — you need {POINTS_PER_PICK} per pick."}

    db.execute(
        "INSERT INTO picks (participant_id, slate_id, week_key, picked_team) VALUES (?,?,?,?)",
        (human_id, slate_id, week_key, picked_team)
    )
    return {"ok": True, "points_remaining": get_wallet()}


def slate_summary(week_key: str) -> dict:
    """Stats for the commissioner dashboard."""
    total = (db.fetchone("SELECT COUNT(*) AS c FROM weekly_slate WHERE week_key=?", (week_key,)) or {"c": 0})["c"]
    settled = (db.fetchone("SELECT COUNT(*) AS c FROM weekly_slate WHERE week_key=? AND settled=1", (week_key,)) or {"c": 0})["c"]
    picks_count = (db.fetchone("SELECT COUNT(*) AS c FROM picks WHERE week_key=?", (week_key,)) or {"c": 0})["c"]
    human_picks = (db.fetchone(
        "SELECT COUNT(*) AS c FROM picks p "
        "JOIN participants pt ON pt.id=p.participant_id "
        "WHERE p.week_key=? AND pt.is_human=1", (week_key,)
    ) or {"c": 0})["c"]
    return {
        "week_key": week_key,
        "total_games": total,
        "settled_games": settled,
        "unsettled_games": total - settled,
        "total_picks": picks_count,
        "human_picks": human_picks,
    }
