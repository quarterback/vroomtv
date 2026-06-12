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
import urllib.request

log = logging.getLogger("vroomtv.sync")

FEEDS = [
    ("baseball", "BASEBALL_SYNC_URL", "BASEBALL_DB", "BASEBALL_SYNC_TOKEN"),
    ("viperball", "VIPERBALL_SYNC_URL", "VIPERBALL_DB", "VIPERBALL_SYNC_TOKEN"),
    ("tennis", "TENNIS_SYNC_URL", "TENNIS_DB", "TENNIS_SYNC_TOKEN"),
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
        if not token:
            results[sport] = f"skipped (set {token_env})"
            continue
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or ".", suffix=".tmp")
                with os.fdopen(fd, "wb") as out:
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
            os.replace(tmp, dest)  # atomic — readers never see a partial file
            results[sport] = f"ok ({os.path.getsize(dest):,} bytes)"
        except Exception as e:  # noqa: BLE001 — a dead sim must not kill the loop
            results[sport] = f"error: {e}"
            log.warning("sync %s failed: %s", sport, e)
    _last["at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    _last["results"] = results
    return results


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
