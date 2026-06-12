"""Unassociated Press — cross-sport scores and news hub."""
from __future__ import annotations
import os
from datetime import datetime
from flask import Flask, Response, render_template, abort
from adapters import baseball, viperball, tennis
import newsroom

app = Flask(__name__)


def _ticker(per_sport: int = 8) -> list[dict]:
    """Scoreboard strip shown on every page; league-tagged for the filter."""
    items = []
    for g in baseball.get_recent_scores(limit=per_sport):
        items.append({
            "sport": "Baseball", "league": "O27 League",
            "away": g["away_abbrev"], "home": g["home_abbrev"],
            "away_score": g["away_score"], "home_score": g["home_score"],
            "note": "Playoffs" if g["is_playoff"] else "Final",
            "url": f"/game/baseball/{g['id']}",
        })
    for g in viperball.get_recent_scores(limit_per_league=per_sport):
        items.append({
            "sport": "Viperball", "league": g["league"],
            "away": g["away_name"][:3].upper(), "home": g["home_name"][:3].upper(),
            "away_score": g["away_score"], "home_score": g["home_score"],
            "note": f"Wk {g['week']}",
            "url": f"/game/viperball/{g['save_key']}/{g['week']}/{g['matchup_key']}",
        })
    for g in tennis.get_recent_scores(limit_per_source=per_sport):
        items.append({
            "sport": "Tennis", "league": g["league"],
            "away": g.get("away_abbrev") or g["away_name"][:3].upper(),
            "home": g.get("home_abbrev") or g["home_name"][:3].upper(),
            "away_score": g["away_points"], "home_score": g["home_points"],
            "note": "Final",
            "url": f"/game/tennis/{g['source']}/{g['id']}",
        })
    return items


@app.context_processor
def inject_globals():
    ticker = _ticker()
    leagues = []
    for t in ticker:
        key = (t["sport"], t["league"])
        if key not in leagues:
            leagues.append(key)
    return {
        "dateline": datetime.now().strftime("%A, %B %d, %Y").replace(" 0", " "),
        "ticker": ticker,
        "ticker_leagues": leagues,
    }


@app.route("/art/<int:seed>.svg")
def art(seed: int):
    resp = Response(newsroom.pixel_art_svg(seed), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/")
def index():
    baseball_scores = baseball.get_recent_scores()
    viperball_scores = viperball.get_recent_scores()
    tennis_scores = tennis.get_recent_scores()
    articles = baseball.get_news(limit=5)
    wire = newsroom.build_wire(baseball_scores, viperball_scores, tennis_scores)
    return render_template(
        "index.html",
        articles=articles, wire=wire,
        baseball_scores=baseball_scores,
        viperball_scores=viperball_scores,
        tennis_scores=tennis_scores,
        baseball_configured=bool(os.environ.get("BASEBALL_DB")),
        viperball_configured=bool(os.environ.get("VIPERBALL_DB")),
        tennis_configured=bool(os.environ.get("TENNIS_DB")),
    )


@app.route("/news")
def news():
    return render_template("news.html", articles=baseball.get_news(limit=50))


@app.route("/news/<slate_date>/<voice_id>")
def article(slate_date: str, voice_id: str):
    a = baseball.get_article(slate_date, voice_id)
    if not a:
        abort(404)
    return render_template("article.html", a=a)


@app.route("/standings")
def standings():
    return render_template(
        "standings.html",
        baseball_standings=baseball.get_standings(),
        viperball_standings=viperball.get_standings(),
        tennis_standings=tennis.get_standings(),
    )


@app.route("/leaders")
def leaders():
    return render_template(
        "leaders.html",
        baseball_batting=baseball.get_batting_leaders(),
        baseball_pitching=baseball.get_pitching_leaders(),
        viperball_leaders=viperball.get_stat_leaders(),
        tennis_leaders=tennis.get_stat_leaders(),
    )


@app.route("/game/baseball/<int:game_id>")
def game_baseball(game_id: int):
    detail = baseball.get_game_detail(game_id)
    if not detail:
        abort(404)
    return render_template("game_baseball.html", **detail)


@app.route("/game/viperball/<save_key>/<int:week>/<path:matchup_key>")
def game_viperball(save_key: str, week: int, matchup_key: str):
    detail = viperball.get_game_detail(save_key, week, matchup_key)
    if not detail:
        abort(404)
    return render_template("game_viperball.html", **detail)


@app.route("/game/tennis/<source>/<int:dual_id>")
def game_tennis(source: str, dual_id: int):
    if source not in ("gtt", "ncaa"):
        abort(404)
    detail = tennis.get_game_detail(source, dual_id)
    if not detail:
        abort(404)
    return render_template("game_tennis.html", **detail)
