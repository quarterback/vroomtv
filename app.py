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


def _feed_auth_ok(sport: str, write: bool = False) -> bool:
    """Auth for per-sport feed routes. Valid tokens: the sport's own sync
    token (= that sim's EXPORT_TOKEN) or the hub-wide SYNC_TOKEN.

    Reads (download, sync) are open when no token is configured — the data
    is public on the sims' own sites anyway. Writes (upload) always require
    a configured token: an open write path could poison the snapshots the
    sims restore themselves from."""
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
        or request.args.get("token", "")
    valid = [t for t in (os.environ.get(f"{sport.upper()}_SYNC_TOKEN"),
                         os.environ.get("SYNC_TOKEN")) if t]
    if not valid:
        return not write
    return supplied in valid


@app.route("/upload/<sport>", methods=["POST", "PUT"])
def upload_db(sport: str):
    """Receive a sim DB pushed from elsewhere (e.g. a Fly web console on a
    machine whose disk is ephemeral). Same auth as /sync. Raw body:

        curl -X POST --data-binary @data/viperball.db \\
             -H "Authorization: Bearer $TOKEN" https://<hub>/upload/viperball
    """
    dest_env = {"baseball": "BASEBALL_DB", "viperball": "VIPERBALL_DB",
                "tennis": "TENNIS_DB"}.get(sport)
    if not dest_env or not _feed_auth_ok(sport, write=True):
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


@app.route("/download/<sport>")
def download_db(sport: str):
    """Serve the hub's copy of a sim DB back out — the restore half of
    /upload/<sport>, e.g. to seed a sim's fresh volume from a machine
    console:

        curl -H "Authorization: Bearer $TOKEN" \\
             https://<hub>/download/viperball -o /data/viperball.db
    """
    from flask import send_file
    src_env = {"baseball": "BASEBALL_DB", "viperball": "VIPERBALL_DB",
               "tennis": "TENNIS_DB"}.get(sport)
    if not src_env or not _feed_auth_ok(sport):
        abort(404)
    src = os.environ.get(src_env)
    if not src or not os.path.exists(src):
        abort(404)
    return send_file(src, mimetype="application/x-sqlite3",
                     as_attachment=True, download_name=os.path.basename(src))


@app.route("/sync", methods=["GET", "POST"])
def sync_now():
    """Manual pull of all feeds. Open unless SYNC_TOKEN is configured;
    with a token: /sync?token=<SYNC_TOKEN>."""
    token = os.environ.get("SYNC_TOKEN")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
        or request.args.get("token", "")
    if token and supplied != token:
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
@app.route("/art/<sport>/<int:seed>.svg")
def art(seed: int, sport: str = ""):
    resp = Response(newsroom.pixel_art_svg(seed, sport), mimetype="image/svg+xml")
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


def _pick(catalog: list[dict]) -> tuple:
    """Resolve ?sport=&league= against a [{sport, leagues:[{label,...}]}]
    catalog: ESPN-style — you always look at exactly one league."""
    sport = request.args.get("sport", "")
    entry = next((c for c in catalog if c["sport"] == sport),
                 catalog[0] if catalog else None)
    if not entry:
        return None, None
    league = request.args.get("league", "")
    sel = next((l for l in entry["leagues"] if l["label"] == league),
               entry["leagues"][0])
    return entry, sel


@app.route("/standings")
def standings():
    catalog = []
    bb = baseball.get_standings()
    if bb:
        catalog.append({"sport": "Baseball", "leagues": [
            {"label": "O27 League", "kind": "baseball", "tier": "Pro", "rows": bb}]})
    vb = viperball.get_standings()
    if vb:
        catalog.append({"sport": "Viperball", "leagues": [
            {"label": lg["league"], "kind": "viperball", "tier": lg["tier"],
             "teams": lg["teams"]} for lg in vb]})
    tn = tennis.get_standings()
    if tn:
        catalog.append({"sport": "Tennis", "leagues": [
            {"label": lg["league"], "kind": "tennis", "tier": lg["tier"],
             "teams": lg["teams"]} for lg in tn]})
    for c in catalog:
        c["leagues"].sort(key=lambda l: l["tier"] != "Pro")  # Pro first
    entry, sel = _pick(catalog)
    return render_template("standings.html", catalog=catalog, entry=entry, sel=sel)


@app.route("/leaders")
def leaders():
    catalog = []
    batting, pitching = baseball.get_batting_leaders(), baseball.get_pitching_leaders()
    if batting or pitching:
        catalog.append({"sport": "Baseball", "leagues": [
            {"label": "O27 League", "kind": "baseball", "tier": "Pro",
             "batting": batting, "pitching": pitching}]})
    vb = viperball.get_stat_leaders()
    if vb:
        catalog.append({"sport": "Viperball", "leagues": [
            {"label": lg["league"], "kind": "viperball", "tier": lg["tier"],
             "leaders": lg["leaders"]} for lg in vb]})
    tn = tennis.get_stat_leaders()
    if tn:
        catalog.append({"sport": "Tennis", "leagues": [
            {"label": lg["league"], "kind": "tennis", "tier": lg["tier"],
             "leaders": lg["leaders"]} for lg in tn]})
    for c in catalog:
        c["leagues"].sort(key=lambda l: l["tier"] != "Pro")
    entry, sel = _pick(catalog)
    return render_template("leaders.html", catalog=catalog, entry=entry, sel=sel)


def _duel(stats_a: dict, stats_b: dict, pairs: list) -> list[dict]:
    """ABC-style head-to-head bars: [{label, a, b, a_pct}] for stats
    present on both sides."""
    out = []
    for label, key in pairs:
        a, b = stats_a.get(key), stats_b.get(key)
        if a is None and b is None:
            continue
        a, b = float(a or 0), float(b or 0)
        total = a + b
        out.append({"label": label, "a": f"{a:g}", "b": f"{b:g}",
                    "a_pct": round(100 * a / total) if total else 50})
    return out


@app.route("/game/baseball/<int:game_id>")
def game_baseball(game_id: int):
    detail = baseball.get_game_detail(game_id)
    if not detail:
        abort(404)
    g = detail["game"]

    def _tot(team_id):
        side = [b for b in detail["batters"] if b["team_id"] == team_id]
        return {k: sum(b[col] for b in side) for k, col in
                (("runs", "runs"), ("hits", "hits"), ("hr", "hr"),
                 ("bb", "bb"), ("k", "k"))}
    detail["duel"] = _duel(
        _tot(g["away_team_id"]), _tot(g["home_team_id"]),
        [("Runs", "runs"), ("Hits", "hits"), ("Home runs", "hr"),
         ("Walks", "bb"), ("Strikeouts", "k")])
    rows = baseball.get_standings()
    divs = {t["division"] for t in rows
            if t["name"] in (g["home_name"], g["away_name"])}
    detail["ladder"] = [t for t in rows if t["division"] in divs]
    return render_template("game_baseball.html", **detail)


@app.route("/game/viperball/<save_key>/<int:week>/<path:matchup_key>")
def game_viperball(save_key: str, week: int, matchup_key: str):
    detail = viperball.get_game_detail(save_key, week, matchup_key)
    if not detail:
        abort(404)
    r = detail.get("result") or {}
    stats = r.get("stats", {})
    detail["duel"] = _duel(
        stats.get("away", {}), stats.get("home", {}),
        [("Total yards", "total_yards"), ("Rushing yards", "rushing_yards"),
         ("Kick pass yards", "kick_pass_yards"), ("Lateral yards", "lateral_yards"),
         ("Total plays", "total_plays"), ("Tackles", "tackles"),
         ("Fumbles", "fumbles")])
    scorers = {"home": [], "away": []}
    for side in ("home", "away"):
        for p in r.get("player_stats", {}).get(side, []):
            tds = p.get("tds", p.get("touchdowns", p.get("game_touchdowns", 0)))
            if tds:
                scorers[side].append({"name": p.get("name", ""), "tds": tds})
        scorers[side].sort(key=lambda s: -s["tds"])
    detail["scorers"] = scorers
    drives = r.get("drive_summary") or []
    total = sum(max(d.get("yards", 0), 3) for d in drives) or 1
    x, chart = 0.0, []
    for d in drives:
        w = 100 * max(d.get("yards", 0), 3) / total
        notes = []
        if d.get("bonus_drive"):
            notes.append("bonus drive")
        for t in d.get("timeouts") or []:
            notes.append(f"timeout ({t.get('team_name', t.get('team', ''))}, "
                         f"{str(t.get('category', '')).replace('_', ' ')})")
        chart.append({
            "x": round(x, 2), "w": round(max(w - 0.4, 0.3), 2),
            "team": d.get("team", "home"),
            "td": "touchdown" in str(d.get("result", "")),
            "bonus": bool(d.get("bonus_drive")),
            "title": f"Q{d.get('quarter', '?')} · {d.get('team', '')} · "
                     f"{d.get('plays', 0)} plays, {d.get('yards', 0)} yds — "
                     f"{str(d.get('result', '')).replace('_', ' ')}"
                     + (" · " + ", ".join(notes) if notes else ""),
        })
        x += w
    detail["drive_chart"] = chart
    detail["ladder"] = next(
        (lg["teams"] for lg in viperball.get_standings()
         if lg["save_key"] == save_key), [])
    return render_template("game_viperball.html", **detail)


@app.route("/game/tennis/<source>/<int:dual_id>")
def game_tennis(source: str, dual_id: int):
    if source not in ("gtt", "ncaa"):
        abort(404)
    detail = tennis.get_game_detail(source, dual_id)
    if not detail:
        abort(404)
    label = detail.get("league_name") if source == "gtt" else \
        f"{detail.get('division', '').upper()} {detail.get('gender', '').title()}"
    detail["ladder"] = next(
        (lg["teams"] for lg in tennis.get_standings()
         if lg["league"] == label), [])[:25]
    return render_template("game_tennis.html", **detail)
