#!/bin/sh
# Restores a backup into a NEW database, never over an existing one.
#   ./scripts/restore.sh backups/gymbo-....dump [target_db]
# Default target: <POSTGRES_DB>_restore. Inspect it, then follow README
# ("Restaurar sobre la base principal") if you really want to switch to it.
set -eu
cd "$(dirname "$0")/.."
file="${1:?Usage: scripts/restore.sh <backup.dump> [target_db]}"
[ -f "$file" ] || { echo "Backup not found: $file" >&2; exit 1; }
target="${2:-}"
db_name=$(docker compose exec -T db sh -c 'printf %s "$POSTGRES_DB"')
target="${target:-${db_name}_restore}"
case "$target" in *[!A-Za-z0-9_]*) echo "Invalid database name: $target" >&2; exit 1 ;; esac

exists=$(docker compose exec -T db sh -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$target'\" --username \"\$POSTGRES_USER\" postgres")
if [ "$exists" = "1" ]; then
    echo "Database '$target' already exists. Refusing to overwrite it; pick another name or drop it yourself." >&2
    exit 1
fi
docker compose exec -T db sh -c "createdb --username \"\$POSTGRES_USER\" --owner \"\$APP_DB_USER\" \"$target\""
docker compose exec -T db sh -c "pg_restore --no-owner --role \"\$APP_DB_USER\" --exit-on-error --username \"\$POSTGRES_USER\" --dbname \"$target\"" < "$file"
echo "Restored $file into database '$target'."
