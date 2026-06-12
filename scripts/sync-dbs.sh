#!/usr/bin/env bash
# Sync the three sims' live databases from their Fly apps onto the vroomtv
# volume. Run from any machine where `fly` is logged in:
#
#   ./scripts/sync-dbs.sh
#
# Re-run whenever you want fresher scores — the hub reads the files
# per-request, so no restart is needed. A sim that's unreachable is skipped
# (its section of the site just shows no games).
set -uo pipefail

HUB="${HUB_APP:-vroomtv}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PUTS=""

echo "==> baseball: resolving active save on hybrid-baseball"
SID="$(fly ssh console -q -a hybrid-baseball -C "cat /data/saves/registry.json" 2>/dev/null \
      | python3 -c "import json,sys; print(json.load(sys.stdin)['active_id'] or '')" 2>/dev/null)"
if [ -n "$SID" ] && fly ssh sftp get "/data/saves/save_${SID}.db" "$WORK/o27v2.db" -a hybrid-baseball; then
  PUTS="$PUTS o27v2.db"
else
  echo "    skipped (no active save or app unreachable)"
fi

echo "==> viperball"
if fly ssh sftp get /app/data/viperball.db "$WORK/viperball.db" -a viperball; then
  PUTS="$PUTS viperball.db"
else
  echo "    skipped (no saves yet or app unreachable)"
fi

echo "==> tennis"
if fly ssh sftp get /data/tennis.db "$WORK/tennis.db" -a tennis-team-manager; then
  PUTS="$PUTS tennis.db"
else
  echo "    skipped (no seasons yet or app unreachable)"
fi

if [ -z "$PUTS" ]; then
  echo "nothing to upload"
  exit 1
fi

echo "==> uploading to $HUB:/data:$PUTS"
fly ssh console -q -a "$HUB" -C "rm -f $(for f in $PUTS; do printf '/data/%s ' "$f"; done)"
for f in $PUTS; do
  echo "put $WORK/$f /data/$f"
done | fly ssh sftp shell -a "$HUB"

echo "done — refresh the site"
