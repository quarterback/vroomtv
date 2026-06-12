"""Pulls each sim's database over HTTP from its live site.

Every sim exposes a token-protected /export/db that streams a consistent
SQLite snapshot. This module downloads each configured feed to the path its
adapter reads (BASEBALL_DB etc.), atomically, so a sync can run while pages
are being served.

Config (env):
  BASEBALL_SYNC_TOKEN   matches EXPORT_TOKEN on the baseball app
  VIPERBALL_SYNC_TOKEN  matches EXPORT_TOKEN on the viperball app
  TENNIS_SYNC_TOKEN     matches EXPORT_TOKEN on the tennis app
  SYNC_TOKEN            guards the manual /sync route; also the fallback
                        for any per-sport token left unset
  BASEBALL_SYNC_URL     e.g. https://superinnin.gs/export/db
  VIPERBALL_SYNC_URL    e.g. https://viperball.xyz/export/db
  TENNIS_SYNC_URL       e.g. https://pctennis.xyz/export/db
  SYNC_INTERVAL_MIN     auto-sync period; 0 or unset disables the timer
"""
from __future__ import annotations
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("vroomtv.sync")

FEEDS = [
    ("baseball", "BASEBALL_SYNC_URL", "BASEBALL_DB", "BASEBALL_SYNC_TOKEN"),
    ("viperball", "VIPERBALL_SYNC_URL", "VIPERBALL_DB", "VIPERBALL_SYNC_TOKEN"),
    ("tennis", "TENNIS_SYNC_URL", "TENNIS_DB", "TENNIS_SYNC_TOKEN"),
]

# Portal JSON exports — advanced stats the sims' stat sites already
# compute (wOBA / OPS+ for baseball, KenPom for viperball college).
# Optional: missing or 404 just means the basic DB-derived leaders show.
# Each entry: (key, env-base, default-suffix, where-it-lands-on-disk)
PORTALS = [
    ("baseball_leaders", "BASEBALL_PORTAL_URL", "/export/leaders.json",
     "baseball_leaders.json"),
    ("viperball_sessions", "VIPERBALL_PORTAL_URL", "/export/sessions.json",
     "viperball_sessions.json"),
    ("tennis_portal", "TENNIS_PORTAL_URL", "/export/data_portal.json",
     "tennis_portal.json"),
]

_last: dict = {"at": None, "results": {}}


def sync_all() -> dict:
    """Fetch every configured feed. Returns {sport: 'ok'|'skipped'|error}."""
    results = {}
    for sport, url_env, db_env, token_env in FEEDS:
        url, dest = os.environ.get(url_env), os.environ.get(db_env)
        token = os.environ.get(token_env) or os.environ.get("SYNC_TOKEN", "")
        if not url or not dest:
            results[sport] = "skipped (not configured)"
            continue
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            # Conditional fetch: sims fingerprint their source DB and answer
            # 304 when nothing changed, so quiet cycles cost ~no bandwidth.
            etag_path = dest + ".etag"
            if os.path.exists(dest):
                try:
                    with open(etag_path) as fh:
                        headers["If-None-Match"] = fh.read().strip()
                except OSError:
                    pass
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                etag = resp.headers.get("ETag", "")
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or ".", suffix=".tmp")
                with os.fdopen(fd, "wb") as out:
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
            os.replace(tmp, dest)  # atomic — readers never see a partial file
            if etag:
                with open(etag_path, "w") as fh:
                    fh.write(etag)
            results[sport] = f"ok ({os.path.getsize(dest):,} bytes)"
        except urllib.error.HTTPError as e:
            if e.code == 304:
                results[sport] = "unchanged"
            else:
                results[sport] = f"error: {e}"
                log.warning("sync %s failed: %s", sport, e)
        except Exception as e:  # noqa: BLE001 — a dead sim must not kill the loop
            results[sport] = f"error: {e}"
            log.warning("sync %s failed: %s", sport, e)
    _last["at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    _last["results"] = results
    # Portal JSON exports: optional, best-effort, never block a sport.
    for key, url_env, suffix, name in PORTALS:
        base = os.environ.get(url_env)
        if not base:
            continue
        sport = key.split("_")[0]
        dest = _portal_path(sport, name)
        if not dest:
            continue
        try:
            url = base.rstrip("/") + suffix
            with urllib.request.urlopen(url, timeout=30) as resp:
                blob = resp.read()
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(blob)
            results[key] = f"ok ({len(blob):,} bytes)"
        except Exception as e:
            log.info("portal %s skipped: %s", key, e)

    # Viperball: fan out one standings.json per active college session.
    vb_sessions_path = _portal_path("viperball", "viperball_sessions.json")
    base = os.environ.get("VIPERBALL_PORTAL_URL")
    if vb_sessions_path and os.path.exists(vb_sessions_path) and base:
        try:
            import glob as _glob
            import json
            with open(vb_sessions_path) as fh:
                sessions = json.load(fh).get("college", [])
            active = {s.get("session_id") for s in sessions if s.get("session_id")}
            ok = 0
            for sid in active:
                dest = _portal_path("viperball", f"vb_kp_{sid}.json")
                try:
                    url = f"{base.rstrip('/')}/export/college/{sid}/standings.json"
                    with urllib.request.urlopen(url, timeout=30) as r:
                        with open(dest, "wb") as fh:
                            fh.write(r.read())
                    ok += 1
                except Exception:
                    continue
            # Drop kenpom snapshots for sessions the sim no longer reports —
            # otherwise the hub keeps surfacing dead "College (xxxx)" tabs.
            kp_dir = os.path.dirname(vb_sessions_path) or "."
            pruned = 0
            for fp in _glob.glob(os.path.join(kp_dir, "vb_kp_*.json")):
                sid = os.path.basename(fp)[len("vb_kp_"):-len(".json")]
                if sid not in active:
                    try:
                        os.unlink(fp)
                        pruned += 1
                    except OSError:
                        pass
            if ok:
                results["viperball_kenpom"] = f"ok ({ok} session{'s' if ok != 1 else ''})"
            if pruned:
                results["viperball_kenpom_pruned"] = pruned
        except Exception as e:
            log.info("viperball kenpom fanout skipped: %s", e)

    if any(v.startswith("ok") for v in results.values()):
        # Warm off-thread: the rebuilds are heavy and the /sync response
        # (or the timer tick) shouldn't wait on them.
        threading.Thread(target=_warm_caches, daemon=True,
                         name="cache-warm").start()
    return results


def _portal_path(sport: str, name: str) -> str | None:
    """Stash portal JSON next to that sport's DB so cleanup is automatic."""
    db = os.environ.get({"baseball": "BASEBALL_DB", "viperball": "VIPERBALL_DB",
                         "tennis": "TENNIS_DB"}[sport])
    return os.path.join(os.path.dirname(db) or ".", name) if db else None


def _warm_caches() -> None:
    """Rebuild the heavy mtime-keyed adapter caches right after a sync so
    no visitor pays the cold-parse cost on a small shared-CPU machine."""
    try:
        from adapters import tennis, viperball
        vb = os.environ.get("VIPERBALL_DB")
        if vb and os.path.exists(vb):
            viperball._college_leagues(vb)
        tennis.get_stat_leaders()
    except Exception:
        log.warning("cache warm failed", exc_info=True)


def last_sync() -> dict:
    return _last


def start_timer() -> None:
    """Background auto-sync, if SYNC_INTERVAL_MIN is set. One initial sync
    runs shortly after boot so a fresh deploy fills itself."""
    try:
        minutes = float(os.environ.get("SYNC_INTERVAL_MIN", "0"))
    except ValueError:
        minutes = 0
    if minutes <= 0:
        return

    def loop():
        time.sleep(5)
        while True:
            sync_all()
            time.sleep(minutes * 60)

    threading.Thread(target=loop, daemon=True, name="db-sync").start()
