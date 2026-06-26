# AAR: The hub "loading forever" — a bundled-league OOM loop

**Date:** 2026-06-26
**Scope:** Production vroomtv was stuck on an endless spinner — no page ever
finished. Root-caused to an out-of-memory crash loop from the recently bundled
NHL/NBA league files; fixed by removing + pausing those feeds and making the
ZenGM loader memory-safe under concurrency. Branch
`claude/vroomtv-loading-issue-0g0ysk`.

---

## Symptom

"The vroomtv site isn't working anymore, it's still loading." The page never
errored and never rendered — it just hung. Locally with no databases the app
was fine, which is what made it look benign; the failure only shows up with the
*bundled* league files present (which is exactly the production image) and under
*concurrent* first-hits (which is exactly a real browser opening the page).

## Root cause

Three things lined up, all introduced by the recent league-bundling commits
(#28–#32):

1. **Big files, auto-enabled.** `data/nhl.json.gz` is 8.3 MB on disk but
   **63 MB decompressed**, bundled *with box scores retained*; `data/nba.json.gz`
   is 22 MB decompressed. `zengm_feeds.use_bundled_defaults()` points each feed's
   env var at its committed `data/<key>` file at import, so both leagues are live
   with zero config. Every page view loads *every* enabled league through the
   scoreboard ticker (`inject_globals` → `_ticker` → `enabled()`).

2. **The parse was outside the lock.** `zengm_common.load()` is mtime-cached, but
   the expensive `json.load` of the raw export ran *outside* `_lock` — the lock
   only guarded the cache dict read/write. So a cold worker doesn't single-flight
   the parse; N threads each parse the same file at once.

3. **1 worker × 8 threads on a 512 MB machine.** The production `CMD` is
   `gunicorn --workers 1 --threads 8`; the fly.io VM is `memory_mb = 512`. A
   single NHL parse peaks ~285 MB RSS (the transient raw dict before projection).
   On a cold worker, the browser's first burst of requests had all 8 threads
   parsing NHL+NBA simultaneously → **>1.8 GB** → fly OOM-kills the worker before
   any response is written → restart → next request burst → OOM again. That loop
   *is* the infinite spinner.

The reason it never reproduced in a casual local check: a single sequential
request survives (one ~285 MB parse fits in 512 MB and then caches). You only
see the blow-up with the bundled files **and** concurrency.

## What was changed

- **Removed the data entirely.** Deleted `data/nhl.json.gz` and
  `data/nba.json.gz` (they were the only tracked files in `data/`; added
  `data/.gitkeep` so the directory still ships in the image, since runtime paths
  like `/app/data` expect it).
- **Paused both feeds.** Added `"paused": True` to the `nhl` and `nba` rows in
  `zengm_feeds.FEEDS`, and made `enabled()`, `use_bundled_defaults()`, and the
  box-score opt-in loop all skip paused feeds. A paused feed never loads into the
  hub even if its file or `$<env>` reappears — belt and suspenders, and
  reversible: flip the flag to bring a league back.
- **Single-flighted the parse** (defense-in-depth) in `zengm_common.load()` by
  holding `_lock` across the parse with a double-checked cache read. At most one
  parse runs at a time, so peak memory stays ~one parse and late arrivals fall
  straight through to the cache hit. This protects any *uploaded* large league
  (the men's/women's college exports are the obvious future risk) from
  re-triggering the same loop, even though NHL/NBA are gone.

## Why pause rather than just delete

The owner's three messages escalated: "get rid of the nhl data" → "same for nba"
→ "pause both of those data feeds so they don't come into the [hub]." Deleting
the files alone fixes *today* (no bundled path, so `use_bundled_defaults` won't
wire them). But the explicit ask was to *pause* — i.e. guarantee they can't come
back by accident (a stray upload, a re-added file, an env var). The `paused` flag
is that guarantee and keeps the registry row as documentation that the league
exists, so re-enabling later is a one-line change instead of a re-add.

## Validation

- **Concurrency repro + fix, measured.** 8 concurrent cold `load()` calls for
  NHL+NBA peaked at **1855 MB** before the fix and **289 MB** after (machine is
  512 MB). Cold warmup time also dropped 31.9 s → 2.4 s (no GIL-contended
  parallel parsing of the same file).
- **Single-parse fit.** One NHL parse peaks 284 MB, steady-state resident after
  both leagues cached was ~227 MB — confirming a serialized parse fits 512 MB
  with gunicorn/flask overhead, so the single-flight approach is sufficient
  on its own; removing the files is the owner's call, not a memory necessity.
- **Feeds gone.** `zengm_feeds.enabled()` → `[]`, `sports()` → `[]`, neither
  `NHL_LEAGUE_FILE` nor `NBA_LEAGUE_FILE` set by the bundled-defaults pass,
  `BOX_KEEP_ENVS` empty.
- **Pages serve.** `app` imports clean; `/`, `/scores`, `/standings`,
  `/playoffs`, `/leaders`, `/news` all return 200 in **milliseconds** (the
  ~2.6 s cold parse is gone), no errors in the server log. Also exercised the
  production shape (gunicorn 1 worker / 8 threads) with an 8-request cold burst:
  all 200.

## What was NOT done / honest caveats

- **No production deploy or live screenshot.** Verification is local — the
  concurrency repro, the gunicorn burst, and the rendered-page checks. The fly.io
  OOM itself is inferred from the 512 MB limit vs. the measured 1.8 GB peak, not
  observed in the live container (no access to fly logs from here). The inference
  is strong but it is an inference.
- **Other leagues untouched.** PWHL, WNBA, and men's/women's college basketball
  remain registry placeholders with no bundled data (they rely on uploads). They
  were never the problem and aren't changed — but note the college exports are
  large, so the single-flight fix is what keeps *them* safe if uploaded; the
  underlying parse is still ~hundreds of MB and a future very-large upload on a
  512 MB box could still be tight even single-flighted. If that ever bites,
  options are: project-on-upload to scores-only (already supported via
  `project_file`), bump the VM memory, or `--preload` so the parse is shared
  copy-on-write across (future) workers.
- **The SQLite sims** (baseball, viperball, tennis) and the **soccer** scrape
  cache are unrelated and untouched.

## Operator notes

- Nothing to configure to deploy — the next image simply won't contain the NHL/
  NBA files and won't enable those feeds.
- **To bring a league back:** drop its export at `data/<key>.json[.gz]` *and*
  remove `"paused": True` from its `FEEDS` row. If it's a big league, prefer
  committing a scores-only projection (or set `"box"` off) to keep the resident
  footprint small.
- The lesson worth keeping: **anything auto-loaded at import on the 512 MB box
  has to be sized against a cold, concurrent first-hit, not a warm single
  request.** The loader is now single-flighted, so the live constraint is the
  *peak of one parse*, not N of them.
