# AAR: The Rocky Mountain News — redesign, league coverage, and the sync saga

**Date:** 2026-06-12
**Scope:** Everything between the handoff of the original "Unassociated Press
Sports Wire" into this repo and the hub going fully self-feeding. Earlier
phases (initial build, adapter audit, first deploy config) are recorded in
`docs/aar-vroomtv-sports-hub.md`; this AAR covers the day the hub became the
Rocky Mountain News.

---

## What was built

### Redesign: 1940s broadsheet → modern sports front
The wire-service aesthetic was replaced wholesale on direction ("The Athletic
meets ESPN.com (in the Page 2 era) meets Sporting News… bring back the Rocky
Mountain News"). Concretely:

- **Scoreboard strip** on every page, modeled on a screenshot of ESPN's
  actual strip: league-label cells between card groups, status line per card,
  bold winners with a red marker, grayed losers, a left dropdown filter
  (by sport: Baseball / Viperball / Tennis), scroll arrow. ~25 lines of
  vanilla JS; everything else stays server-rendered.
- **Nameplate**: *Rocky Mountain News* in Boska 900 with a red SPORTS chip,
  black nav bar. Fonts from Fontshare per direction: Boska (nameplate),
  Zodiak (headlines, article body), Switzer (UI/data).
- **News**: the front page always leads with a story. Real articles come from
  the baseball gazette's `gazette_articles` cache (`/news`, article pages
  with drop caps). Until stories exist, `newsroom.py` writes placeholder
  headlines by parsing results (margin-scaled plural verbs: edge/beat/top/
  pound/rout) and picks a lead by drama (playoffs, then closeness × scoring).
- **Pixel art**: deterministic mirrored-sprite SVGs (`/art/<seed>.svg`,
  seeded from the game URL) stand in for photography on leads and thumbnails.
  The seed is the image; nothing is stored.
- Flavor copy cut throughout ("Court Correspondents" → "Tennis").
- Favicon: hand-drawn 3-polygon green mountain SVG + PNG fallback.

### League coverage: everything the sims actually have
- **Viperball college**: the sim never persists the season object (memory
  only) — only per-game `box_score` blobs survive. The adapter reconstructs
  scores, standings, leaders, and full game pages from those blobs, parsed
  once per DB change and cached on file mtime (a season is hundreds of
  ~200KB JSON blobs; per-request parsing would not survive contact).
- **Tennis college** was already covered (NCAA `seasons`/`duals`).
- **Baseball college / youth cup / World Cup** live in the same o27v2 DB
  (`college_games`, `youth_games`, `wc_games`) and now appear as score
  sections and ticker groups.

### Data pipeline: how the hub gets fed (three architectures in one day)
1. **fly-CLI sync script** (`scripts/sync-dbs.sh`) — correct, but dead on
   arrival: the operator doesn't use a CLI. Kept as an alternative.
2. **Volume + manual upload** — vroomtv's volume requirement broke
   dashboard-driven deploys ("needs volumes…"); Fly's dashboard cannot
   create volumes. Volume removed; the hub's DB copies live on ephemeral
   disk because…
3. **HTTP self-feeding (final)**: each sim exposes `/export/db` (WAL-safe
   `sqlite3 backup()` snapshot). The hub pulls every `SYNC_INTERVAL_MIN`
   (30) and ~5s after boot; `/sync` triggers a pull and reports per-sport
   results. `/upload/<sport>` and `/download/<sport>` push/serve copies.
   **Viperball self-restores**: it has no volume either, so on boot, if its
   DB file is missing (fresh rootfs after a deploy), it pulls the hub's
   latest snapshot — the two volume-less apps back each other up over HTTP.
   Interlock: a freshly-wiped viperball refuses to export an empty saves
   store, so it can never clobber the snapshot that restores it.

### Auth: secrets → secretless by default
The shared-secret handshake (sim `EXPORT_TOKEN` = hub `<SPORT>_SYNC_TOKEN`)
failed operationally **twice**: first because independently generated values
can't match, then because of a vocabulary collision (below). Final design:
**reads are open when no secret is configured** — the data is already public
on the sims' own sites — and setting the secrets locks everything back down.
The one write path (`/upload`) always requires a configured secret, because
an open write could poison the snapshots sims restore themselves from.

---

## Validation

- Every redesign page was screenshotted in headless Chromium against
  generated data from all three sims (including a simulated viperball
  college season persisted through the real `save_box_scores_bulk` path).
- The dropdown filter was exercised in the browser, not just rendered.
- The export routes were tested on all three sims locally: 404 without/with
  wrong secret, valid snapshots with it, 404-on-empty for viperball.
- The final secretless configuration was proven end-to-end before pushing:
  three sims with zero secrets, an empty hub, one `/sync` → front page fully
  populated (50 strip cards, college viperball included).
- Viperball's boot-restore was tested against a hub serving a real snapshot
  (file absent → restored, 16 saves intact; file present → no-op).
- **Not validated**: the production loop on Fly itself. As of this AAR the
  operator had not yet merged/redeployed the final secretless branches; the
  first three-`ok` `/sync` response in production is the remaining proof.

## What was NOT done

- No game-detail pages for baseball college / youth / World Cup (their box
  scores live in different tables); those score cards don't link anywhere.
- Tennis leaders remain singles match wins only — season play persists no
  per-match stat lines (fast-fidelity zeroes them in `lines_json`).
- News stories exist only for baseball (the gazette). The front page's
  placeholder headlines cover the gap for other leagues.
- Viperball in-progress college seasons still die with the sim's memory on
  restart — by sim design. Their box scores persist and the hub keeps them.
- A real volume for viperball (impossible without CLI; the HTTP self-restore
  stands in, with a ≤ sync-interval loss window on deploys).

## Lessons

- **"Token" was the wrong word.** Fly's dashboard has a *Tokens* page
  (Fly-generated API credentials, value not choosable) and a *Secrets* page
  (typed name+value env vars). Code and instructions said "token" for what
  Fly calls a secret's value; the operator was led to the wrong page for
  hours. Name things after what the operator's dashboard calls them.
- **Meet the operator where they are.** Three pipeline designs died because
  they assumed flyctl, machine consoles, or volume creation — none of which
  exist in a dashboard-only workflow. The design that survived is the one
  whose only verbs are "merge" and "redeploy".
- **Fly's launch flow generates its own config.** The original app was
  launched before the repo had a Dockerfile, and every subsequent deploy
  reused Fly's generated `flask run` plan, silently ignoring the repo's
  gunicorn/fly.toml. Deleting and relaunching the app picked the repo files
  up. Also: launch commits its own `fly.toml` back to main (PR #4), and a
  web-UI conflict resolution later shipped literal `<<<<<<<` markers inside
  `favicon.svg` — worth checking for after any dashboard-merged conflict.
- **Find where the data actually lives before designing.** Viperball college
  "standings" exist nowhere on disk — only per-game box scores do. The
  adapter design fell out of that fact, not the other way around.
