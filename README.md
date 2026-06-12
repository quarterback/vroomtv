# vroomtv — The Unassociated Press Sports Wire

A cross-sport scores hub in the style of a 1940s wire-service broadsheet.
It aggregates live results from three sports sims into one front page:
unified scores, standings, stat leaders, and game detail pages — pure
server-rendered HTML, no JavaScript.

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

The env vars point at `/data/*.db` on the app's volume. Upload a snapshot
of each sim's DB with `fly sftp shell` → `put viperball.db /data/viperball.db`
(same for the other two); refresh them whenever you want newer scores. Any
sport without a file uploaded shows a placeholder.

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
templates/        broadsheet UI (Jinja, no JS)
static/style.css  ink-on-newsprint palette, wire-grid layout
docs/             after-action report (build + verification history)
```

`docs/aar-vroomtv-sports-hub.md` has the full build history, the schema
audit against each sim, and what was verified against live data.
