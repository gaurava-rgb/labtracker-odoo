#!/bin/bash
# Standby entrypoint. First start (empty data folder): copy the primary with pg_basebackup -R, which writes
# standby.signal + primary_conninfo so Postgres starts as a streaming hot standby. Later starts: just run Postgres.
# To rebuild the copy: ./scripts/rebuild-replica.sh (wipes the folder, this script does the rest).
set -euo pipefail
: "${PGDATA:=/var/lib/postgresql/data}" "${PRIMARY_HOST:=db}" "${REPLICA_SLOT:=labtracker_replica}"
: "${REPLICATOR_PASSWORD:?}" "${POSTGRES_PASSWORD:?}" "${POSTGRES_USER:=odoo}"
log() { echo "[labtracker replica] $(date +%H:%M:%S) $*"; }

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"; chown postgres:postgres "$PGDATA"; chmod 700 "$PGDATA"
  log "empty data folder: waiting for the primary and the replicator role"
  until PGPASSWORD="$REPLICATOR_PASSWORD" psql -h "$PRIMARY_HOST" -U replicator -d postgres -Atc 'select 1' >/dev/null 2>&1; do
    sleep 3
  done
  # a stale slot from an earlier copy would make pg_basebackup -C fail
  PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$PRIMARY_HOST" -U "$POSTGRES_USER" -d postgres -Atc \
    "select pg_drop_replication_slot('$REPLICA_SLOT') where exists (select 1 from pg_replication_slots where slot_name='$REPLICA_SLOT')" >/dev/null
  log "copying the primary with pg_basebackup (slot $REPLICA_SLOT)"
  PGPASSWORD="$REPLICATOR_PASSWORD" gosu postgres pg_basebackup -h "$PRIMARY_HOST" -U replicator -D "$PGDATA" \
      -R -X stream -C -S "$REPLICA_SLOT" --checkpoint=fast
  log "copy done; starting as hot standby"
elif [ ! -f "$PGDATA/standby.signal" ]; then
  log "WARNING: data folder is not a standby (no standby.signal). Run ./scripts/rebuild-replica.sh"
fi
exec docker-entrypoint.sh postgres
