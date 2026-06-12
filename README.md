# vroomtv — Rocky Mountain News (Sports)

A cross-sport sports section in a modern newspaper design (The Athletic ×
Sporting News, with an ESPN-style scoreboard strip). It aggregates live
results from three sports sims into one front page: scores, standings,
stat leaders, news stories, and game detail pages — server-rendered HTML
with a few lines of JS for the scoreboard's league filter.

| Sport | Sim | Live site |
| --- | --- | --- |
| ⚾ Baseball (O27 variant) | [quarterback/hybrid-baseball](https://github.com/quarterback/hybrid-baseball) | [superinnin.gs](https://superinnin.gs) |
| 🏉 Viperball | [quarterback/viperball](https://github.com/quarterback/viperball) | [viperball.xyz](https://viperball.xyz) |
| 🎾 Tennis (GTT + NCAA) | [quarterback/tennis-team-manager](https://github.com/quarterback/tennis-team-manager) | [pctennis.xyz](https://pctennis.xyz) |

## How it works

The hub never talks to the sims' servers and never imports their code. It
opens each sim's SQLite database **read-only** (`?mode=ro`) and renders
straight from there:

- One adapter per sport (`adapters/baseball.py`, `adapters/viperball.py`,
  `adapters/tennis.py`). Each knows its sim's schema and returns plain
  dicts; the Flask layer (`app.py`) never touches SQL.
- **Graceful degradation** — any sport whose DB is unconfigured or missing
  shows a wire-service placeholder instead of an error. The hub runs fine
  with zero, one, two, or three databases.
- **Multi-league throughout** — viperball renders every `pro_league` save
  in the DB, tennis renders every GTT league and every NCAA season. New
  leagues appear with zero code changes.

## Running

```bash
pip install -r requirements.txt

BASEBALL_DB=/path/to/hybrid-baseball/o27v2/o27v2.db \
VIPERBALL_DB=/path/to/viperball/data/viperball.db \
TENNIS_DB=/path/to/tennis-team-manager/tennis.db \
python manage.py runserver
```

Serves on port **5050** (`PORT` to override). All three env vars are
optional.

> Baseball note: if the sim uses its saves registry, the live DB is
> `o27v2/saves/<save-hash>.db` rather than `o27v2/o27v2.db` — point
> `BASEBALL_DB` at the actual file.

## Deploying

The repo ships a `Dockerfile` and `fly.toml` (mirroring the three sims,
which all run on Fly). From the repo root:

```bash
fly launch --copy-config --no-deploy   # first time: creates the app + volume
fly deploy
```

**Data flows in by itself.** Each sim exposes a token-protected
`/export/db` route, and the hub pulls all three feeds every
`SYNC_INTERVAL_MIN` minutes (default 30) into its volume — no CLI, no
manual uploads. One-time setup, all in the Fly dashboard (each app →
Secrets):

1. Pick one secret value. Set it as `EXPORT_TOKEN` on **hybrid-baseball**,
   **viperball**, and **tennis-team-manager**, and as `SYNC_TOKEN` on
   **vroomtv**.
2. Redeploy the three sims (their export routes ship in each repo) and the
   hub.

That's it. To force an immediate refresh, open
`https://<your-hub>/sync?token=<SYNC_TOKEN>` in a browser — it reports
per-sport results. Any unconfigured or unreachable sport shows a
placeholder, never an error.

(`scripts/sync-dbs.sh` remains as an alternative for fly-CLI users.)

This won't work on static/serverless hosts (Netlify, GitHub Pages, Vercel's
static mode): the hub is a long-running Flask server that reads SQLite files
from local disk.

## Routes

| Route | Page |
| --- | --- |
| `/` | Front page — recent scores across all sports |
| `/standings` | Standings, every league/season |
| `/leaders` | Stat leaders (batting & ERA, rushing, singles match wins) |
| `/game/baseball/<id>` | Box score + play-by-play |
| `/game/viperball/<save_key>/<week>/<matchup_key>` | Game summary |
| `/game/tennis/<gtt\|ncaa>/<id>` | Dual line scores |

## Repo layout

```
app.py            Flask routes (thin — rendering only)
manage.py         runserver entry point
adapters/         one read-only DB adapter per sport
newsroom.py       placeholder headlines parsed from results + pixel-art SVGs
templates/        Jinja UI: scoreboard strip, front page, news, box scores
static/style.css  the design system (Fontshare: Boska / Zodiak / Switzer)
docs/             after-action report (build + verification history)
```

`docs/aar-vroomtv-sports-hub.md` has the full build history, the schema
audit against each sim, and what was verified against live data.
