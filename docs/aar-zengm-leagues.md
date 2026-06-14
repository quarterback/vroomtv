# AAR: ZenGM Hockey (PWHL) as a fourth sport

**Date:** 2026-06-14
**Scope:** Surface a ZenGM Hockey league on the hub alongside the three
existing sims. Branch `claude/peaceful-dijkstra-qtorad`. Basketball to follow
using the same machinery.

---

## What was built

Hockey results from **ZenGM** (Hockey GM — the user's PWHL 2026 league) now
appear across the Rocky: scoreboard strip, full scores page, standings,
stat leaders, per-game box scores, and mechanical wire recaps. It rides the
same adapter contract as baseball/viperball/tennis, so `app.py` and the
templates treat it as just another sport.

- `adapters/zengm_common.py` — loads a ZenGM **League File** (the game's
  `Tools > Export League` JSON) and caches it keyed on file mtime, the same
  trick the SQLite adapters use so a ~7 MB file parses once per upload, not
  once per request. Plus shared helpers: `gameAttributes` access (handles
  both the plain-dict and history-list encodings), current season, team
  identity, a short league label, and goal-clock formatting.
- `adapters/hockey.py` — the adapter surface over the JSON: `get_recent_scores`,
  `get_standings`, `get_leader_boards` (skaters + goalies), `get_game_detail`
  (box score + scoring summary), `get_news` (empty — no gazette).
- `templates/game_hockey.html` — box score: skaters (G/A/PTS/+/-/PIM/S/HIT/BLK),
  goalies (SV/GA/SV%/TOI), period-by-period scoring summary, head-to-head bars,
  division ladder.
- `app.py` — Hockey registered in `_ticker`, the scores/standings/leaders
  catalogs, the news wire, a `/game/hockey/<gid>` route, and the
  `/upload`+`/download` sport→env maps.
- `newsroom.py` — hockey wire recaps + a rink pixel-art scene.
- `templates/standings.html` — a `kind == 'hockey'` branch (W/L/OTL/PTS/Strk).
- `templates/index.html` — a front-page Hockey scores section.

## Why

The user plays GM Games / ZenGM and wanted those leagues on the site. Two
options were weighed:

1. **Publish ZenGM *results* to the hub** — chosen. Publishing the data/output
   of leagues you played is not redistributing or deploying the game, the same
   as the countless dynasty writeups people post.
2. **Fork ZenGM and host flavored games** — rejected: ZenGM's license permits
   only view / edit / run-locally-privately / share-source and explicitly
   forbids hosting a publicly accessible version or competing with the official
   sites. The hub is public, so this is a non-starter.

## Architecture decisions

- **Read the JSON directly; no SQLite intermediate.** The other sims sync a
  SQLite snapshot over HTTP from a `/export/db` endpoint. ZenGM has no such
  endpoint and the export is already a complete, clean JSON, so converting it
  to SQLite would mean a schema + converter + queries to maintain for no gain.
  The adapter reads the JSON, mtime-cached. The app layer only calls adapter
  functions, so it's agnostic to JSON-vs-SQLite storage.
- **Ingestion by upload, not pull.** Because there's no live endpoint, hockey
  reuses the existing token-gated `/upload/<sport>` (raw atomic write — works
  for JSON as-is). New env var `HOCKEY_LEAGUE_FILE`. `sync.py` is untouched;
  hockey is not in its pull loop.
- **Conventions pinned against the real export** (not assumed):
  - `game.teams[0]` is **home**, `[1]` is away — verified by reconstructing
    Boston's home record from `games[]` and matching `seasons[].wonHome/lostHome`
    (26-3-3 both ways).
  - `game.playoffs` flags the postseason directly (no need to cross-reference
    `playoffSeries`).
  - Hockey **points = 2·W + OTL + T**.
  - A goal's `clock` is **minutes remaining** in the period (0–`quarterLength`,
    here 20), rendered M:SS. `quarter` is the period; period 4 = OT.
  - Skater goals/assists are split ev/pp/sh and summed; **goalies are the rows
    with `gpGoalie > 0`** (SV%, GAA from `sv`/`ga`/`gMin`).
  - League **tab label** uses the division name when there's a single
    conference (PWHL's "PWHL") because the conference name is too long.
- **Graceful degradation** — every adapter function returns empty when
  `HOCKEY_LEAGUE_FILE` is unset/missing/unparseable; `/game/hockey/<gid>` 404s.

## Verified

Against the real PWHL 2026 export (v72, 268 games, box scores included):

- Adapter smoke test: recent scores carry correct winners and home/away;
  standings match saved records (Boston 42-16-6 = 90 pts); all nine leader
  boards populate (Poulin 21-33-54 atop Points); a box score yields
  38 skaters / 2 goalies / 4 goals with a correctly formatted scoring summary.
- Flask route render (test client): `/`, `/scores`, `/standings`, `/leaders`,
  `/news`, `/game/hockey/268`, `/art/hockey/*.svg` all 200 with expected content.
- Upload path: `/upload/hockey` rejects without a token (404), accepts with one
  (writes 6.8 MB), and pages serve hockey afterward.
- Unconfigured: index 200, hockey absent, `/game/hockey/<gid>` 404.

(`flask` was not present in the sandbox — installed `--no-deps` to run the
render/upload tests; the system `blinker` blocks a normal `pip install flask`.)

## What was NOT done

- **Deploy config.** `HOCKEY_LEAGUE_FILE` (a path) and a token (`HOCKEY_SYNC_TOKEN`,
  or the shared `SYNC_TOKEN`) must be set wherever the hub runs. No
  Dockerfile/`.replit`/fly config was touched — left for the deploy owner.
- **Automation.** ZenGM export is a manual in-game click, so the workflow is:
  export with Box Scores → `curl -X PUT … /upload/hockey` (or drop the file at
  `HOCKEY_LEAGUE_FILE`) → re-do after each session. No auto-pull is possible.
- **Basketball** — follow-on. Same three pieces reusing `zengm_common`, with
  basketball stat columns/boards, once a Basketball GM league file arrives.
- **Soccer** — ZenGM has no soccer title; the user runs a separate Python sim
  for that. Note: ZenGM hockey is what people reskin as soccer, so a
  hockey-engine soccer league would arrive as hockey-shaped data and could
  ride this adapter with a soccer label later.

## Operator workflow

1. In Hockey GM: `Tools > Export League` with **Box Scores checked** (it's
   optional and easy to omit, but `games` is the only source of per-game scores
   and box-score lines — without it you get standings + season leaders only).
2. `curl -X PUT -H "Authorization: Bearer $HOCKEY_SYNC_TOKEN" --data-binary @league.json https://<hub>/upload/hockey`
3. Re-export + re-upload after each play session.

---

## Multi-league expansion + Basketball (2026-06-14)

The single-file-per-sport wiring was generalized to a **feed registry**, because
each sport needs several leagues (like the other sims) — Hockey = NHL + PWHL,
pro Basketball = NBA + WNBA, men's/women's college basketball as their own sport
tabs, softball on its own. The site's existing `?sport=&league=` catalog already
supports a per-sport league dropdown, so this is a data-layer change.

**Architecture.** `adapters/zengm_feeds.py` holds a `FEEDS` list — each entry is
`{key, sport, league, env, engine}`, one ZenGM League File per league. `engine`
(`rink` | `basketball`) selects the adapter module, the standings `kind`, and the
box-score template. Both engine adapters (`zengm_rink.py`, `basketball.py`) now
expose the **same feed-cfg surface** — `recent_scores(cfg)`, `standings(cfg)`
(returns catalog-ready league dicts incl. `kind`), `leader_boards(cfg)`,
`game_detail(cfg, gid)` (duel + ladder baked in), `league_label(cfg)` — so
`app.py` loops feeds grouped by sport with no per-sport special-casing. One game
route serves all of them: `/game/zg/<key>/<gid>`. Upload is per league key
(`/upload/pwhl`, `/upload/nba`, …); the feed `key` is the URL slug. The
feed-configured league name (NHL/PWHL) is authoritative for the dropdown label,
not the in-file name.

- `hockey.py` → `zengm_rink.py`; `game_hockey.html` → `game_zgmh.html` (dynamic
  `{{ game.sport_label }}`). New `adapters/basketball.py` + `game_basketball.html`
  (MIN/FG/3P/FT/REB/AST/STL/BLK/TOV/PF/PTS); basketball reuses the `baseball`
  standings kind (W/L/Pct/GB by conference→division). `newsroom.build_wire` takes
  one `zengm_scores` list; added a basketball court pixel-art scene + rink-reskin
  aliases.

**Verified** against real exports: PWHL (hockey) + NBA (BBGM, 1231 games) +
Women's NCAA D1 (BBGM, 5573 games). All pages render; the Hockey dropdown shows
NHL + PWHL; basketball standings split East/West; SGA 32.5 PPG (NBA), Ashley
Carter 26.1 PPG (W-NCAA); generic game route + unknown-key 404 confirmed; the
three existing sims still render.

**⚠️ Memory caveat (open).** The mtime cache holds each parsed league file in
RAM. Small leagues are cheap (PWHL ~7 MB; NBA 72 MB → ~300 MB), but the Women's
NCAA D1 file (351 teams **with box scores**) is **266 MB on disk → ~1.3 GB
resident** when loaded — even just to read standings, because the whole file is
parsed. With men's college too, a small hub would OOM. Options to resolve:
(a) export the big college leagues **without Box Scores** (standings + season
leaders only — far smaller), (b) host on a larger instance, or (c) add a
SQLite-conversion path for large feeds so queries don't hold the whole league in
memory. Pro leagues (~30 teams) are fine as-is. Decision pending with the user.

### Current league keys / env vars

`nhl`→`NHL_LEAGUE_FILE`, `pwhl`→`PWHL_LEAGUE_FILE`, `nba`→`NBA_LEAGUE_FILE`,
`wnba`→`WNBA_LEAGUE_FILE`, `cbb-men`→`CBB_MEN_LEAGUE_FILE`,
`cbb-women`→`CBB_WOMEN_LEAGUE_FILE`, `box-lacrosse`→`BOX_LACROSSE_LEAGUE_FILE`,
`indoor-soccer`→`INDOOR_SOCCER_LEAGUE_FILE`, `floorball`→`FLOORBALL_LEAGUE_FILE`.
Upload: `PUT /upload/<key>` with `Bearer $<KEY>_SYNC_TOKEN` (or the shared
`SYNC_TOKEN`). Softball (ZGMB engine) still pending a played export.
