"""PFS — Peak Fantasy Sports Flask Blueprint."""
from __future__ import annotations
import os
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify
)
from . import db as pdb
from . import slate as slate_mod
from . import participants as part_mod
from . import settlement as settle_mod

pfs_bp = Blueprint(
    "pfs",
    __name__,
    url_prefix="/picks",
    template_folder=None,  # use app-level templates/picks/
)

_INITIALIZED = False


def _init():
    global _INITIALIZED
    if _INITIALIZED:
        return
    pdb.ensure_schema()
    part_mod.ensure_participants()
    _INITIALIZED = True


def _human_id() -> int:
    _init()
    return part_mod.get_human_id()


def _week() -> str:
    return slate_mod.current_week()


# ── Main lobby ─────────────────────────────────────────────────────────────

@pfs_bp.route("/")
def index():
    _init()
    week_key = _week()
    human_id = _human_id()
    games = slate_mod.get_slate(week_key, human_id)
    points = slate_mod.get_wallet()
    summary = slate_mod.slate_summary(week_key)
    return render_template(
        "picks/index.html",
        games=games,
        week_key=week_key,
        season=slate_mod.current_season(),
        points=points,
        summary=summary,
        pick_cost=slate_mod.POINTS_PER_PICK,
    )


@pfs_bp.route("/pick", methods=["POST"])
def pick():
    _init()
    week_key = _week()
    human_id = _human_id()
    slate_id = request.form.get("slate_id", type=int)
    picked_team = request.form.get("picked_team", "").strip()
    if not slate_id or not picked_team:
        flash("Invalid pick submission.", "error")
        return redirect(url_for("pfs.index"))
    result = slate_mod.submit_pick(human_id, slate_id, week_key, picked_team)
    if not result["ok"]:
        flash(result["error"], "error")
    return redirect(url_for("pfs.index"))


# ── Leaderboard ────────────────────────────────────────────────────────────

@pfs_bp.route("/leaderboard")
def leaderboard():
    _init()
    week_key = _week()
    human_id = _human_id()
    season = slate_mod.current_season()
    board = settle_mod.get_season_leaderboard(season, limit=100)
    my_rank = settle_mod.get_season_human_rank(season, human_id)
    points = slate_mod.get_wallet()
    # Find human row even if outside top 100
    my_row = next((r for r in board if r["is_human"]), None)
    return render_template(
        "picks/leaderboard.html",
        board=board,
        my_rank=my_rank,
        my_row=my_row,
        week_key=week_key,
        season=season,
        points=points,
    )


# ── My picks ───────────────────────────────────────────────────────────────

@pfs_bp.route("/me")
def me():
    _init()
    week_key = _week()
    human_id = _human_id()
    season = slate_mod.current_season()
    points = slate_mod.get_wallet()
    stats = settle_mod.get_my_stats(week_key, human_id)
    my_rank = settle_mod.get_season_human_rank(season, human_id)
    picks = pdb.fetchall("""
        SELECT p.picked_team, p.correct, p.points_earned,
               s.sport, s.home_team, s.away_team, s.point_value, s.settled, s.winner
        FROM picks p
        JOIN weekly_slate s ON s.id = p.slate_id
        WHERE p.participant_id=? AND p.week_key=?
        ORDER BY s.sport, s.id
    """, (human_id, week_key))
    # Past weeks (for career strip)
    past_weeks = pdb.fetchall("""
        SELECT lb.week_key, lb.total_points, lb.picks_correct, lb.picks_total
        FROM weekly_leaderboard lb
        WHERE lb.participant_id=?
        ORDER BY lb.week_key DESC
        LIMIT 8
    """, (human_id,))
    return render_template(
        "picks/me.html",
        week_key=week_key,
        season=season,
        points=points,
        stats=stats,
        my_rank=my_rank,
        picks=picks,
        past_weeks=past_weeks,
        points_total=slate_mod.POINTS_PER_SEASON,
    )


# ── Commissioner ───────────────────────────────────────────────────────────

@pfs_bp.route("/commissioner")
def commissioner():
    _init()
    week_key = _week()
    summary = slate_mod.slate_summary(week_key)
    points = slate_mod.get_wallet()
    return render_template(
        "picks/commissioner.html",
        week_key=week_key,
        season=slate_mod.current_season(),
        summary=summary,
        points=points,
    )


@pfs_bp.route("/commissioner/refresh", methods=["POST"])
def commissioner_refresh():
    _init()
    week_key = _week()
    added = slate_mod.build_slate(week_key)
    if added:
        flash(f"Added {added} new game{'s' if added != 1 else ''} to the Week {week_key} slate.", "success")
    else:
        flash("No new games to add — slate is up to date or at capacity.", "info")
    return redirect(url_for("pfs.commissioner"))


@pfs_bp.route("/commissioner/settle", methods=["POST"])
def commissioner_settle():
    _init()
    week_key = _week()
    result = settle_mod.settle_week(week_key)
    if result["ok"]:
        flash(
            f"Settled {result['settled']} game{'s' if result['settled'] != 1 else ''}. "
            f"Scored {result['human_scored']} human pick{'s' if result['human_scored'] != 1 else ''}. "
            f"Generated {result['ai_picks']:,} AI picks. Leaderboard updated.",
            "success"
        )
    else:
        flash(result["error"], "error")
    return redirect(url_for("pfs.commissioner"))


@pfs_bp.route("/commissioner/new-week", methods=["POST"])
def commissioner_new_week():
    _init()
    summary = slate_mod.slate_summary(_week())
    if summary["unsettled_games"] > 0:
        flash(
            "Settle the current week before starting a new one — it still has "
            f"{summary['unsettled_games']} unsettled game"
            f"{'s' if summary['unsettled_games'] != 1 else ''}.",
            "error"
        )
        return redirect(url_for("pfs.commissioner"))
    result = settle_mod.advance_week()
    flash(
        f"Started {result['week_key']}. Refresh the slate to pull this week's games — "
        "your season points carry over.",
        "success"
    )
    return redirect(url_for("pfs.commissioner"))


@pfs_bp.route("/commissioner/end-season", methods=["POST"])
def commissioner_end_season():
    _init()
    human_id = _human_id()
    season = slate_mod.current_season()
    board = settle_mod.get_season_leaderboard(season, limit=100)
    my_rank = settle_mod.get_season_human_rank(season, human_id)
    my_row = next((r for r in board if r["is_human"]), None)
    return render_template(
        "picks/season_over.html",
        board=board,
        my_rank=my_rank,
        my_row=my_row,
        season=season,
        points=slate_mod.get_wallet(),
        total_players=len(board),
    )


@pfs_bp.route("/commissioner/new-season", methods=["POST"])
def commissioner_new_season():
    _init()
    result = settle_mod.new_season()
    flash(
        f"New season started — everything cleared and your points reset to "
        f"{result['points']:,}. Refresh the slate to begin.",
        "success"
    )
    return redirect(url_for("pfs.commissioner"))
