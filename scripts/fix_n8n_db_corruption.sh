#!/usr/bin/env bash
# Repair the two n8n SQLite issues seen repeatedly on the Hostinger VPS.
#
# 1. `database.sqlite` sometimes ends up owned by root (e.g. after being
#    touched by a root shell) while n8n runs as the unprivileged `node`
#    user (uid 1000) — every startup then hits `SQLITE_READONLY` on its
#    first migration and n8n crash-loops forever.
# 2. `workflow_history.nodes` rows can get written with BLOB storage
#    affinity instead of TEXT (seen after DB corruption incidents). n8n's
#    SQLite driver then hands back a raw Buffer instead of parsed JSON,
#    so every node in the workflow resolves to `undefined`, and
#    `NodeTypes.getByNameAndVersion` throws `Cannot read properties of
#    undefined (reading 'endsWith')` — every active workflow fails to
#    activate and retries forever with exponential backoff.
#
# Usage: ./fix_n8n_db_corruption.sh [N8N_CONTAINER] [N8N_VOLUME_DATA_DIR]
set -euo pipefail

N8N_CONTAINER="${1:-n8n}"
N8N_DATA_DIR="${2:-/var/lib/docker/volumes/n8n_data/_data}"
DB_FILE="$N8N_DATA_DIR/database.sqlite"

if [[ ! -f "$DB_FILE" ]]; then
  echo "ERROR: $DB_FILE not found. Pass the correct volume data dir as arg 2."
  exit 1
fi

echo "=== Diagnosing $N8N_CONTAINER ==="
docker inspect "$N8N_CONTAINER" --format 'RestartCount: {{.RestartCount}}  Status: {{.State.Status}}'

OWNER_UID=$(stat -c '%u' "$N8N_DATA_DIR/config" 2>/dev/null || stat -c '%u' "$N8N_DATA_DIR")
CURRENT_DB_UID=$(stat -c '%u' "$DB_FILE")
BLOB_NODE_ROWS=$(sqlite3 "$DB_FILE" "SELECT count(*) FROM workflow_history WHERE typeof(nodes) = 'blob';")

echo "database.sqlite owner uid=$CURRENT_DB_UID (expected uid=$OWNER_UID, matching the rest of the n8n volume)"
echo "workflow_history rows with corrupted (BLOB) nodes column: $BLOB_NODE_ROWS"

if [[ "$CURRENT_DB_UID" == "$OWNER_UID" && "$BLOB_NODE_ROWS" == "0" ]]; then
  echo "Nothing to fix."
  exit 0
fi

echo "=== Stopping $N8N_CONTAINER and backing up database.sqlite ==="
docker stop "$N8N_CONTAINER"
BACKUP="$DB_FILE.pre-corruption-fix-$(date +%Y%m%d-%H%M%S)"
cp -p "$DB_FILE" "$BACKUP"
echo "Backup saved to $BACKUP"

if [[ "$BLOB_NODE_ROWS" != "0" ]]; then
  echo "=== Repairing $BLOB_NODE_ROWS corrupted workflow_history.nodes row(s) ==="
  sqlite3 "$DB_FILE" "UPDATE workflow_history SET nodes = CAST(nodes AS TEXT) WHERE typeof(nodes) = 'blob';"
fi

echo "=== Fixing database.sqlite ownership (uid $CURRENT_DB_UID -> $OWNER_UID) ==="
chown "$OWNER_UID:$OWNER_UID" "$DB_FILE"

echo "=== Starting $N8N_CONTAINER ==="
docker start "$N8N_CONTAINER"
sleep 20
docker inspect "$N8N_CONTAINER" --format 'RestartCount: {{.RestartCount}}  Status: {{.State.Status}}'

echo "=== Recent activation failures (should be empty) ==="
docker logs "$N8N_CONTAINER" --since 20s 2>&1 | grep -i 'did fail with error' || echo "(none)"

BACKUP_KEEP="${N8N_BACKUP_KEEP:-3}"
echo "=== Pruning repair backups (keep newest $BACKUP_KEEP) ==="
mapfile -t OLD_BACKUPS < <(
  ls -1t "$DB_FILE".pre-* 2>/dev/null | awk -v keep="$BACKUP_KEEP" 'NR > keep'
)
if ((${#OLD_BACKUPS[@]})); then
  rm -f -- "${OLD_BACKUPS[@]}"
  echo "Removed ${#OLD_BACKUPS[@]} old repair backup(s)."
else
  echo "(nothing to prune)"
fi

echo "Done."
