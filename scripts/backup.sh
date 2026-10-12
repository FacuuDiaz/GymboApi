#!/bin/sh
# Logical backup of the Compose database to ./backups/ (custom format, compressed).
#   ./scripts/backup.sh            -> backups/gymbo-YYYYmmdd-HHMMSS.dump
# Runs pg_dump inside the db container, so no PostgreSQL client is needed on the host.
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
file="backups/gymbo-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db sh -c 'pg_dump --format=custom --no-owner --username "$POSTGRES_USER" "$POSTGRES_DB"' > "$file"
chmod 600 "$file"
echo "Backup written to $file ($(wc -c < "$file") bytes). Store it encrypted and off this machine."
