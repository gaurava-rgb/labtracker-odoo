#!/bin/bash
# Runs ONCE when the primary's data folder is first created (docker-entrypoint-initdb.d).
# Creates the replication and read-only roles and lets the standby connect from the compose network.
# The odoo container re-applies passwords and grants on every boot (init/db_tool.py roles), so this is only the seed.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v rep="$REPLICATOR_PASSWORD" -v ro="$DASH_RO_PASSWORD" <<'SQL'
CREATE ROLE replicator REPLICATION LOGIN PASSWORD :'rep';
CREATE ROLE dash_ro LOGIN PASSWORD :'ro' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE dash_ro SET default_transaction_read_only = on;
ALTER ROLE dash_ro SET statement_timeout = '15s';
SQL
# "samenet" = only hosts on the same subnet as the server, i.e. containers on this compose network
echo 'host replication replicator samenet scram-sha-256' >> "$PGDATA/pg_hba.conf"
echo "[labtracker db] roles replicator + dash_ro created, replication allowed from the compose network"
