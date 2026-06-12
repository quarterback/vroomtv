# AAR: Unassociated Press Sports Hub (vroomtv)

**Date:** 2026-06-12  
**Scope:** New standalone read-only sports aggregator hub, built as a 4th repo (`quarterback/vroomtv`), staged here in `hybrid-baseball` for handoff

---

## What was built

A Flask web app — "The Unassociated Press Sports Wire" — that reads live data from all three existing sports sims (hybrid-baseball, viperball, tennis-team-manager) and presents unified scores, standings, stat leaders, and game detail pages in a 1940s broadsheet aesthetic.

Files live in `vroomtv/` in this branch and need to be moved to `quarterback/vroomtv`.

## Why

User wanted an ESPN-style aggregator across all three sims. Feasibility was assessed first (direct DB reads = no coupling to sim web servers, no schema changes to any sim), then built. The push path from the remote execution environment to `vroomtv` hit a session permissions wall (MCP tools scoped to the original three repos only), so the code was staged here for handoff.

## Architecture decisions

- **Direct SQLite reads** — hub opens each sim's `.db` file read-only (`?mode=ro`). No HTTP calls, no dependency on sim servers being up.
- **Three adapters** (`adapters/baseball.py`, `adapters/viperball.py`, `adapters/tennis.py`) — each knows its own schema and returns plain dicts. App layer never touches SQL directly.
- **Viperball JSON blobs** — viperball stores everything as JSON in `saves.data`. The adapter parses the `pro_league` blob as a plain dict (structure is stable: `standings`, `results`, `player_season_stats`, `current_week`). No viperball Python classes imported.
- **Multi-league throughout** — viperball iterates all `pro_league` saves by `save_key`; tennis iterates all `gtt_leagues` and `seasons` rows. Adding a new viperball league or tennis season requires zero code changes.
- **Graceful degradation** — any unconfigured or missing DB shows a placeholder, not an error page.
- **1940s wire-service UI** — broadsheet fonts (Playfair Display, IM Fell English, Special Elite), ink-on-newsprint palette, three-column wire grid on the scores page. Pure server-rendered HTML, no JS.

## What was NOT done

- No live testing against real DB files (no DBs present in this environment with data)
- No deployment config (Dockerfile, fly.toml, Procfile) — add when ready to host
- Tennis adapter assumes GTT `gtt_duals.winner` column holds a franchise `id` matching `gtt_franchises.id` — verify this is correct before first run; if `winner` is 0/1 (home/away flag) the standings logic needs adjustment
- Viperball `standings` blob structure assumes `streak` and `streak_type` are separate keys — verify against a live save

## Handoff steps for next agent

1. Move `vroomtv/` contents to root of `quarterback/vroomtv` repo
2. Delete `vroomtv/` from this branch in `hybrid-baseball`
3. Point env vars at real DB files and run `python manage.py runserver`
4. Verify scores page shows data for each configured sport
5. Click through to a game detail page for each sport
6. Check that a second viperball league (if you have one) appears as a separate section

---

## Post-handoff verification & fixes (2026-06-12)

The app was moved to this repo and tested against freshly generated DBs from
all three sims (8-team baseball season slice, NVL + Eurasian League viperball
saves, one GTT league + one NCAA D1 women season). Every route returned 200
with real data; both viperball leagues appeared as separate sections.

**Bugs found and fixed in `adapters/tennis.py`:**

- All queries filtered `status='complete'`, but both tennis season modes only
  ever write `'scheduled'` / `'final'` — no tennis data would ever have shown.
- GTT standings treated `gtt_duals.winner` as a franchise id. It is a 0/1
  home/away flag (0 = home won) — exactly the risk flagged above. Standings
  now mirror the sim's own logic (`app/gtt_seasonmode.py`), including the
  regular-season-only (`round='REG'`) scope.
- NCAA standings had the flag inverted (counted `winner=1` as a home win;
  0 means home won).
- `get_stat_leaders` read the `matches`/`match_stats` tables, which only the
  one-off CLI sims populate — season play stores per-match data in
  `lines_json`, and fast-fidelity duals zero their stat blocks. Leaders are
  now singles match wins aggregated from `lines_json` (GTT MS*/WS* slots via
  `gtt_players` pid lookup, NCAA S* slots by name).

**Other:**

- The viperball `streak`/`streak_type` worry above was unfounded — the save
  blob (`engine/db.py: serialize_pro_league_season`) keeps them as separate
  keys, matching the adapter.
- The baseball adapter was audited against `o27v2/db.py` schemas — correct
  as written. Note the o27v2 saves registry may place the live DB under
  `o27v2/saves/<save>.db` rather than `o27v2/o27v2.db`; point `BASEBALL_DB`
  at the actual file.
- Default port moved 6000 → 5050: browsers block 6000 as an unsafe port
  (X11), and 5000 collides with macOS AirPlay.

Still not done: deployment config (Dockerfile / fly.toml / Procfile).
