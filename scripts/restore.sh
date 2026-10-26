#!/usr/bin/env bash
# Restores the database (and the filestore, if a matching filestore-DATE.tgz exists) from ./backups.
#   ./scripts/restore.sh backups/labtracker-2026-10-25.dump
# Odoo is stopped during the restore, the read-only copy is rebuilt afterwards.
set -euo pipefail; . "$(dirname "$0")/common.sh"
DUMP=${1:?usage: $0 backups/<name>-<date>.dump}
[ -f "$DUMP" ] || { echo "file not found: $DUMP"; exit 1; }
DAY=$(basename "$DUMP" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' || true)
echo "This replaces database '$ODOO_DB' with $DUMP. Type yes to continue:"; read -r ok; [ "$ok" = yes ] || exit 1
docker compose stop odoo db_replica
docker compose exec -T db psql -U odoo -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$ODOO_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$ODOO_DB\" OWNER odoo"
docker compose exec -T db pg_restore -U odoo -d "$ODOO_DB" --no-owner --role=odoo < "$DUMP"
if [ -n "$DAY" ] && [ -f "backups/filestore-$DAY.tgz" ]; then
  docker compose run --rm --no-deps --entrypoint sh odoo -c "rm -rf /var/lib/odoo/filestore/$ODOO_DB" >/dev/null
  docker compose run --rm --no-deps --entrypoint sh -v "$PWD/backups:/backups:ro" odoo -c "mkdir -p /var/lib/odoo/filestore && tar xzf /backups/filestore-$DAY.tgz -C /var/lib/odoo/filestore" >/dev/null
  echo "filestore restored from backups/filestore-$DAY.tgz"
fi
"$(dirname "$0")/rebuild-replica.sh"
docker compose up -d odoo
echo "Restore done. Odoo is starting; it re-applies the Lab setup on boot."
