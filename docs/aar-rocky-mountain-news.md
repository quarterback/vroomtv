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

---

## Phase 2 — coverage, design, and portal syndication

### Coverage: every league the sims actually produce
- **Baseball college / youth cup / World Cup** appear as their own ticker
  groups and score sections (`baseball.get_extra_scores` reads the
  `college_games` / `youth_games` / `wc_games` tables in the same o27v2
  DB). Score cards only — those box scores live in different tables, so
  click-through isn't wired yet.
- **Viperball college** is reconstructed from `box_score` blobs (the sim
  doesn't persist the season object; only per-game blobs survive), parsed
  once per sync and cached on the DB file's mtime. Each game gets a full
  detail page.
- **Tennis** continues to cover GTT (pro) and all six NCAA divisions
  (D1/D2/D3 Men/Women). NCAA is now sourced through the data portal
  (below) when available.

### League hierarchy in the navigation
Standings and leaders pages now group leagues by tier inside each
sport's dropdown — `Pro Tennis` / `College Tennis`, `Pro Viperball` /
`College Viperball`, with `International` reserved for when those data
paths exist. The ticker's "Top Events" dropdown filters by sport
(Baseball / Viperball / Tennis), not by individual league, matching
ESPN's pattern.

### Performance: conditional sync + per-request caching
- **ETag + 304** on every sim's `/export/db` (fingerprint = DB mtime + WAL
  sidecar mtime/size). The hub sends `If-None-Match` from a persisted
  per-sport ETag and reports `unchanged` when a sim's database hasn't
  moved. Quiet 30-minute cycles drop from ~160 MB to a few hundred bytes.
- **Cache warming** runs after every sync that actually changed something,
  so the heavy parses (tennis leaders aggregate from 7,300+ duals'
  `lines_json`; viperball college from hundreds of box-score blobs) happen
  out of band instead of on whoever loads the next page. ~50ms full-page
  renders against production data.

### Match-page infographics (ABC AFL match summary as the template)
- **Head-to-head stat bars** — two-color proportional rails for the team
  duel, server-rendered SVG, no JS.
- **Drive momentum chart** — every viperball drive as a sized block
  (yards), two lanes (away top, home bottom), scoring drives solid,
  bonus drives outlined in gold, hover for play count / result / timeout
  reason.
- **Scorers** and the **ladder** with both teams highlighted, baseball
  divisions or viperball league.
- Surfacing more of what the box-score blob actually carries: weather
  (game temperature + condition), officiating crew with overturned-call
  count, both teams' offense/defense style matchup, rivalry-game tag.

### Pixel art: sport-aware
`/art/<sport>/<seed>.svg` draws a diamond (baseball), a court with net
and ball (tennis), or gridiron stripes (viperball) over a seeded noise
field. Game URL is the seed; the seed is the image; nothing is stored.
Abstract mirrored sprite remains the fallback when sport is unknown.

### Portal syndication: pulling computed stats from the sims' stat sites
The breakthrough that lets the Rocky show real sabermetrics instead of
re-deriving them. Each sim now exposes a JSON endpoint that returns the
same numbers its own stats pages compute, and the hub pulls them as part
of the sync.
- **Baseball** — `/export/leaders.json` returns wOBA / OBP / SLG / K% /
  BB% for batting and ERA / WHIP / K/9 / BB/9 for pitching, with the
  same qualification floors `/leaders` uses.
- **Viperball** — `/export/sessions.json` + per-session
  `/export/college/<sid>/standings.json` return the KenPom-style adjusted
  efficiency margin, tempo, and luck rows from `kenpom.html`.
- **Tennis** — `/export/data_portal.json` returns the full data-portal
  view per (division × gender): power-index rankings with movement, STR
  player leaders with reliability, conference standings leaders,
  recent / upcoming duals, top junior prospects.
- The hub's adapters use portal JSON when present and fall back to
  DB-derived basics when not, so the system degrades gracefully one
  feed at a time. Portals are configured via `*_PORTAL_URL` env vars
  baked into `fly.toml` — **public site addresses, not secrets**, so no
  dashboard work is required to enable them.

## Operational lessons (phase 2)

- **Public URLs are config, not secrets.** I drifted back to instructing
  "set `*_PORTAL_URL` as a secret on vroomtv"; the operator caught it
  immediately. Those are just `https://superinnin.gs` etc. — they live
  in `fly.toml` and ship with the merge. Anything that's a *value the
  operator generates* belongs in Secrets. Anything that's a *URL or
  identifier the world already knows* belongs in `[env]` in the repo.
- **The git proxy is branch-scoped.** Pushes from this session only land
  on `claude/amazing-curie-2jsqdk`. When the operator opened a second
  branch on tennis-team-manager (the `codex/` data portal) and asked me
  to extend it, the correct move was to *wait for it to merge into main*
  and then rebase, not to try to push the branch directly. Don't paper
  over the proxy — let the operator be the gatekeeper.
- **Stale background processes accumulate.** `pkill -f manage.py` ate the
  baseball sim that was running for an integration test more than once.
  `pgrep -af 'manage[.]py'` (with the bracket to avoid self-match) plus
  unique ports per test instance is the only way to keep them straight.
- **Fly excess-capacity != broken.** When the operator pasted alarming-
  looking "autostopping machine" logs from tennis, the issue was a
  `auto_stop_machines = 'suspend'` carried over from launch — fixed by
  matching the other sims' always-on config. Reading the existing config
  on each sim before assuming defaults would have saved a round-trip.

## What was NOT done (phase 2)

- **Stat-portal data for live game pages.** The portal pulls inform the
  *aggregate* leaders and standings; individual match pages still read
  the raw DB. Worth doing if there's a per-game advanced stat (e.g.
  viperball's adjusted efficiency for a single contest) you want to
  display. (Backlog.)

## Closed in phase 3

- **Game-detail pages for baseball college / youth / World Cup.** Score
  cards on the front page and ticker now link to
  `/game/baseball/<tier>/<id>` (`college` / `youth` / `wc`). The adapter
  reads the appropriate per-game stat tables (`college_batter_stats`,
  `game_wc_pitcher_stats`, etc.) and normalizes their column names to
  the pro layout so the box-score template is reused unchanged.
- **Tennis match-page infographics.** Match pages now show duel bars
  for lines won / sets won / games won, aggregated from each line's set
  scores (no extra data needed from the sim — already in `lines_json`).
- **Score-worm chart.** Viperball got a five-line change to stamp
  `home_score_after` / `away_score_after` onto each drive summary; the
  hub draws a relative-lead curve as inline SVG over a midline.
  Falls back to inferring points from drive results when the sim
  hasn't been redeployed yet, so it works against old box scores.
- **News for non-baseball sports.** The wire generator now writes
  one-sentence ledes alongside the headlines (margin-scaled drama
  beats: "slim margins", "comfortable cushion", "runaway", plus
  playoff/week tag prefixes). The /news page renders a per-sport wire
  section under any real gazette articles, with sport-shaped pixel art
  thumbnails.

## Validation (phase 2)

- Match pages screenshotted in headless Chromium against your actual CVL
  box scores from `viperball.xyz` — duel bars, drive chart, scorers,
  ladder all rendering with real data.
- Portal sync verified end-to-end: empty hub disk, one `/sync` call,
  KenPom-flavored standings and wOBA-flavored leaders both appeared.
- ETag conditional sync confirmed: first pull downloads, second responds
  `304 → unchanged`, a write to the sim flips it back to a full pull,
  then `unchanged` again.
- Sport-aware art route serves 200 for all three sports and the abstract
  fallback for unknown sports.
- Tennis branch coordination: rebased onto main after the operator
  merged `codex/improve-design-to-enhance-data-visibility`; `/export/data_portal.json`
  built against the rebased state and verified with `py_compile`.
- **Still not validated**: the full production loop with all three
  portals lit up. Last known production state had viperball college data
  flowing via the raw DB; portal flips on with this branch's merge.
