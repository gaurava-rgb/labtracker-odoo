#!/usr/bin/env bash
# Shows whether the read-only copy (db_replica) is streaming from the main database.
set -euo pipefail; . "$(dirname "$0")/common.sh"
echo "primary sees standby (want 1):  $(docker compose exec -T db psql -U odoo -d postgres -Atc "select count(*) from pg_stat_replication where state='streaming'")"
echo "replica in recovery (want t):   $(docker compose exec -T db_replica psql -U odoo -d postgres -Atc 'select pg_is_in_recovery()')"
echo "tasks on primary / replica:     $(docker compose exec -T db psql -U odoo -d "$ODOO_DB" -Atc 'select count(*) from project_task' 2>/dev/null || echo '-') / $(docker compose exec -T db_replica psql -U odoo -d "$ODOO_DB" -Atc 'select count(*) from project_task' 2>/dev/null || echo '-')"
echo "dashboard source (want replica): $(docker compose exec -T odoo python3 /opt/labtracker/init/odoo_api.py lab.dashboard get_data '[]' | grep -o '"source": "[a-z]*"')"
