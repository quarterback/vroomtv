# AAR: Peak Fantasy Sports (PFS) Pick'em Game

**Date:** 2026-06-13  
**Scope:** New pick'em competition game (`/picks`) living inside vroomtv as a Flask Blueprint  
**PR:** [quarterback/vroomtv#20](https://github.com/quarterback/vroomtv/pull/20)

---

## What was built

A cross-sport pick'em competition game — **Peak Fantasy Sports (PFS)** — mounted at `/picks`
inside the vroomtv hub. Players pick winners of baseball, viperball, and tennis matchups
from a hidden-result slate, spending Zoras to enter and competing weekly against 2,000
AI participants for points on a leaderboard.

The game lives entirely in `picks/` (Flask Blueprint) with its own writable `picks.db`
separate from all three read-only sim DBs. vroomtv's existing sport adapters are reused
as-is; the game only pulls completed game results from them.

## Why

Tennis was the one sim with no gambling/fantasy layer. Rather than building a fourth
in-app sportsbook (which would require implementing full sports-book infrastructure in a
new codebase), this takes a simpler path: a pick'em game that covers all three sports
from a single wallet. Baseball already has CapSpace; viperball already has DraftyQueenz.
PFS is a separate, advertise-ready product that sits above all three — a competitor app,
not an extension of any individual sim.

The "downwind" slate mechanic sidesteps the hardest problem in cross-sim scheduling:
the sims don't run on real-world time, so "today's games" isn't a meaningful query.
Instead, the slate is built from recently-completed games (results hidden in the UI until
the Commissioner settles), decoupling the pick'em cycle from sim advancement entirely.

## Architecture

```
picks/
  db.py           — writable SQLite (picks.db); schema, helpers
  slate.py        — pull completed games from adapters, assign point values, manage wallet
  participants.py — generate and persist 2,000 named AI participants
  settlement.py   — score human picks, simulate AI picks, publish leaderboard
  routes.py       — Flask Blueprint at /picks; all routes including Commissioner UI
templates/picks/
  base.html       — standalone PFS shell (dark mode, gold brand; does NOT extend RMN base)
  index.html, leaderboard.html, me.html, commissioner.html
```

**Data model (`picks.db`):**
- `participants` — human (1 row, username "Commissioner") + 2,000 AI with skill levels
- `human_wallet` — Zora balance per week_key (keyed to ISO calendar week: `2026-W24`)
- `weekly_slate` — one row per game; `winner` stored but never exposed until `settled=1`
- `picks` — human picks (recorded live) + AI picks (bulk-inserted at settlement)
- `weekly_leaderboard` — aggregated from picks after each settlement

**Key mechanic details:**
- 1,000 Zoras per week; 20 per pick → max 50 picks/week
- Point values random in [7,499 – 11,205], non-round, assigned when slate is built
- Correct pick earns the announced points; wrong pick earns nothing (Zoras already spent)
- Week key = `datetime.now().isocalendar()` → `{year}-W{week:02d}` (real calendar week)

## AI participants

2,001 total (1 human + 2,000 AI). Names procedurally generated from
adjective × noun × suffix combinations seeded deterministically (`_gen_name(seed)`).
Three skill tiers on creation:
- **High** (20%): `skill_level` 0.62–0.72 → picks ~15 games/week, ~66% correct
- **Medium** (50%): 0.46–0.58 → picks ~30 games/week, ~52% correct
- **Low** (30%): 0.35–0.45 → picks ~45 games/week, ~40% correct

Pick count and correct/incorrect outcome are both derived from `skill_level` at
settlement time. No pre-simulation needed — AI picks are bulk-inserted as if made
earlier, then the leaderboard is rebuilt in a single aggregation pass.

On a two-game test slate, settlement generated 4,000 AI picks in under a second.

## Commissioner UI

All game management is done through proper GUI pages (`/picks/commissioner`), not
admin URLs. Three action cards:
- **Refresh Slate** — pulls new completed games from all three sport adapters, skips any
  game already in any prior week's slate (prevents duplicates across weeks)
- **Settle Week** — scores open human picks, bulk-inserts AI picks, rebuilds leaderboard;
  disabled button state when no unsettled games exist
- **Reset Week** — restores human Zoras to 1,000 for the current week; guarded by a
  confirmation modal

## What was verified

- Blueprint registers and all four routes (`/`, `/leaderboard`, `/me`, `/commissioner`)
  return 200 in Flask test client
- Pick submission correctly deducts Zoras (1000 → 980 after first pick)
- Duplicate pick correctly rejected
- Winner field is None in `get_slate()` output until `settled=1`
- Settlement on 2-game test slate: scored 2 human picks, generated 4,000 AI picks,
  leaderboard populated with all 2,001 participants
- Human who picked correctly on both test games ranked #1

## What was NOT done / known gaps

- **No live sim DB testing** — adapters were read in code but not tested with real DB
  files in this environment. The adapter queries are unchanged from vroomtv's existing
  code (which was verified against live DBs in the original vroomtv AAR), so this is
  low-risk, but do a real run with actual data before merging.
- **Tennis NCAAAdapter** — `get_recent_scores()` returns `home_name`/`away_name` as raw
  school name strings (no abbreviations for NCAA). Slate shows full school names for
  NCAA tennis matchups, which may be long. Acceptable for now.
- **Viperball matchup_key uniqueness** — the game_id uses
  `vb_{save_key}_{week}_{matchup_key}`. If `matchup_key` isn't unique within a save+week
  (e.g., two games between the same pair), the second would silently be skipped via
  `INSERT OR IGNORE`. Verify with a live viperball DB.
- **No Fly deploy config** for the new `PICKS_DB` volume path or `SECRET_KEY` secret.
  Both need to be added to `fly.toml` and the Fly dashboard before deploying.
- **No weekly auto-reset** — the Commissioner manually refreshes/settles/resets via the
  GUI. A cron job or scheduled route could automate this later.
- **Leaderboard ties** — currently broken by `picks_correct DESC`; two participants with
  the same points and correct count get arbitrary ordering. Non-critical for now.

## Deploy checklist (when ready)

1. Add `PICKS_DB` as a Fly secret pointing to a persistent volume path (e.g.
   `/data/picks.db`)
2. Add `SECRET_KEY` as a Fly secret (Flask flash sessions require it)
3. Deploy, then hit `/picks/commissioner` → Refresh Slate to build the first slate
4. Make picks, then Settle Week to generate AI competition and see the leaderboard
