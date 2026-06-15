# AAR: Postseason brackets as a durable content type

**Date:** 2026-06-15
**Scope:** Give the Rocky a real postseason. None of the newspaper pages
rendered playoffs even though every sim now produces bracket data. Branch
`claude/peaceful-dijkstra-qtorad`.

---

## What was built

A single **playoff content type** plus one page that renders it for every
sport. The shape is sport-agnostic: each adapter emits the same normalized
bracket (or nothing), and `templates/playoffs.html` adapts to whatever it's
given — any number of rounds, best-of-N series or single games, with or
without seeds. New sports light up just by returning the shape; there is no
per-sport bracket code in the route or the template.

- `adapters/bracket.py` — **the content type** and its builders. A `bracket`
  is `{label, tier, season, champion, rounds[]}`; a round is
  `{name, series[]}`; a `series` is `{top, bot, best_of, winner}`; a `side` is
  `{name, abbrev, seed, wins, score}`. Helpers: `side`, `series`, `round_name`
  (names rounds by distance to the final, so a 2-round bracket reads
  Semifinals→Finals and a 4-round one First Round→…→Finals; `by_conf` names the
  conference rounds), `winner_by_wins` (best-of-N clinch), `winner_by_score`
  (single game), `champion_of` (winner of the final series).
- `adapters/zengm_common.py` — `playoff_bracket(league, label)` shared by both
  ZenGM engines (it's the same `playoffSeries` structure for basketball and the
  rink sports). The lean projection now **preserves the current season's
  `playoffSeries`** (trimmed to each side's `tid`/`seed`/`won` + `byConf`) and
  `numGamesPlayoffSeries`, which it previously dropped — without that the
  bracket data didn't survive the upload projection.
- `adapters/basketball.py`, `adapters/zengm_rink.py` — thin `playoffs(cfg)`
  wrappers over the shared builder.
- `adapters/baseball.py` — `get_playoffs()` from the o27v2 `playoff_series`
  table (one bracket per playoff league; series-kind round names; best-of-N).
- `adapters/viperball.py` — `get_playoffs()` from each pro league blob's
  `playoff_bracket` (single-game matchups; the game score is the tally).
- `adapters/tennis.py` — `get_playoffs()` from the non-`REG` GTT/NCAA dual
  rounds (ordered by `round_no`/`bpos`; single matches, dual points as tally).
- `app.py` — a `/playoffs` route that collects brackets across all sports,
  reusing the existing `_pick` + `page_chrome` sport/league picker.
- `templates/playoffs.html` + bracket CSS in `static/style.css`; nav link in
  `base.html`.

## Why

The user's framing was explicit and is the whole point of the design:
*"instead of a custom build of bracket/post-season rendering for each sport,
it's the same durable content type and then it's just used to adapt to whatever
bracket it needs based on size and detail/data."* So the work is a content type
+ one renderer, not five bracket views. It mirrors the rink-pack philosophy
already in the repo: build the shape, and it lights up the moment data is
present.

## Architecture decisions

- **One normalized shape, generic helpers.** Adapters never hand the template
  raw sim data; they build sides/series with `adapters/bracket.py` so the shape
  stays uniform. The template branches on nothing sport-specific.
- **`wins` vs `score` per side, chosen by `best_of`.** Series sports (ZenGM,
  baseball) show series wins (0–4); single-game brackets (viperball, tennis)
  show the game/dual points. The template picks which to display from
  `best_of`, so the same card adapts to both without sport awareness.
- **Round names derive from position, not hard-coding.** `round_name(idx,
  total, by_conf)` keeps any bracket size readable. Sports that record a real
  round label (baseball series-kind, viperball `round_name`, tennis `round`)
  override it; otherwise the generic name is used.
- **Preserve `playoffSeries` in the projection, leanly.** Box scores stay
  dropped (hub policy), but the bracket is tiny — current season only, three
  fields per side — so keeping it costs nothing and is the only way the
  postseason survives `project_file`.
- **Fail safe everywhere.** Every `get_playoffs()`/`playoffs()` returns `[]`
  when the DB/file is missing or has no postseason; `/playoffs` shows an empty
  note. No new hard dependency on data being present.

## Verified

- **Basketball, against a real BBGM NBA export** (the user's
  `BBGM_NBA_Real_2026_draft_lottery`, v72, 1310 games, projected to a 3.1 MB
  lean file): the bracket builds 4 rounds named First Round → Conference
  Semifinals → Conference Finals → Finals; champion **New York Knicks** (2-seed,
  beat Minnesota 4-1); Cleveland swept its opener 4-0; series winners/`best_of`
  all correct.
- **Flask render (test client):** `/playoffs` and `/playoffs?sport=Basketball`
  return 200 with the champion banner, "Conference Finals", and "First Round" in
  the body. No regression: `/`, `/scores`, `/standings`, `/leaders`, `/news`
  still 200.
- **Empty state:** with no league files configured, `baseball/viperball/tennis
  get_playoffs()` all return `[]` and `/playoffs` shows the "No postseason yet"
  note.

## What was NOT done / honest caveats

- **Baseball, viperball, and tennis brackets are built to their schemas but not
  yet verified against live data** — those sims' DBs aren't present in this
  sandbox (they sync over HTTP and were empty here). The queries follow the
  documented columns (`playoff_series`; pro-league `playoff_bracket`; the
  `round`/`round_no`/`bpos` dual columns) and fail safe, but the field mapping
  should be confirmed the first time each sport actually has a postseason
  uploaded — especially: viperball matchup `home`/`away` may be team *keys*
  rather than display names, and the tennis `round` text values (e.g. `QF`/`SF`)
  haven't been seen.
- **No screenshot.** No headless browser in the sandbox; verification is by
  rendered-HTML content, as with the basketball standings work.
- **Single-elimination assumption for the bye/`winner` edges** is light: a
  matchup with one side renders as a "Bye"; an undecided series shows no winner
  highlight. Play-in rounds (ZenGM `playIns`) are not surfaced — the main
  bracket only.
- **Standings unification (same session, separate commit).** The hockey-only
  `kind == 'hockey'` standings branch was replaced by a generic `kind ==
  'zengm'` branch (Conference → Division, adapter-supplied `cols`/`sort`), which
  both basketball and the rink sports now share. Mentioned here because it's on
  the same branch and was the prerequisite that made basketball standings render
  at all.

## Operator notes

Nothing new to deploy — `/playoffs` reads the same league files/DBs the other
pages already use. A sport's bracket appears automatically once its uploaded
data contains a postseason (ZenGM: export *with Box Scores* so `playoffSeries`
is present; the SQLite sims: once their playoff tables are populated).
