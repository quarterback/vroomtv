"""Rocky Mountain News (Sports) — cross-sport scores and news hub."""
from __future__ import annotations
import os
from datetime import datetime
from urllib.parse import quote
from flask import Flask, Response, render_template, abort, jsonify, request
from adapters import baseball, viperball, tennis
import newsroom
import sync

app = Flask(__name__)
sync.start_timer()


@app.route("/upload/<sport>", methods=["POST", "PUT"])
def upload_db(sport: str):
    """Receive a sim DB pushed from elsewhere (e.g. a Fly web console on a
    machine whose disk is ephemeral). Same auth as /sync. Raw body:

        curl -X POST --data-binary @data/viperball.db \\
             -H "Authorization: Bearer $TOKEN" https://<hub>/upload/viperball
    """
    dest_env = {"baseball": "BASEBALL_DB", "viperball": "VIPERBALL_DB",
                "tennis": "TENNIS_DB"}.get(sport)
    token = os.environ.get("SYNC_TOKEN")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
        or request.args.get("token", "")
    if not dest_env or not token or supplied != token:
        abort(404)
    dest = os.environ.get(dest_env)
    if not dest:
        abort(404)
    import tempfile
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or ".", suffix=".tmp")
    size = 0
    with os.fdopen(fd, "wb") as out:
        while chunk := request.stream.read(1 << 20):
            out.write(chunk)
            size += len(chunk)
    if size == 0:
        os.unlink(tmp)
        return jsonify({"error": "empty body"}), 400
    os.replace(tmp, dest)
    return jsonify({sport: f"ok ({size:,} bytes)"})


@app.route("/sync", methods=["GET", "POST"])
def sync_now():
    """Manual pull of all feeds. Browser-friendly: /sync?token=<SYNC_TOKEN>."""
    token = os.environ.get("SYNC_TOKEN")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
        or request.args.get("token", "")
    if not token or supplied != token:
        abort(404)
    return jsonify({"results": sync.sync_all(), "last": sync.last_sync()["at"]})


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
    for g in baseball.get_extra_scores(limit_per_league=per_sport):
        items.append({
            "sport": "Baseball", "league": g["league"],
            "away": g["away_name"][:3].upper(), "home": g["home_name"][:3].upper(),
            "away_score": g["away_score"], "home_score": g["home_score"],
            "note": (g.get("note") or "Final").title(),
            "url": None,
        })
    for g in viperball.get_recent_scores(limit_per_league=per_sport):
        items.append({
            "sport": "Viperball", "league": g["league"],
            "away": g["away_name"][:3].upper(), "home": g["home_name"][:3].upper(),
            "away_score": g["away_score"], "home_score": g["home_score"],
            "note": f"Wk {g['week']}",
            "url": f"/game/viperball/{quote(g['save_key'])}/{g['week']}/{quote(g['matchup_key'])}",
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
        baseball_extra=baseball.get_extra_scores(),
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
