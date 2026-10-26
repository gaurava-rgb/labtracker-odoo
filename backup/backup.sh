#!/bin/bash
# Nightly backup loop (runs in the "backup" service, postgres:16 image so pg_dump matches the server).
# Every day at BACKUP_TIME: pg_dump of the Odoo database + tar of the filestore into /backups,
# then delete anything older than BACKUP_KEEP_DAYS days. BACKUP_ON_START=1 also takes one right away
# if there is no dump from today yet. Restore with ./scripts/restore.sh <dump file>.
set -euo pipefail
: "${ODOO_DB:=labtracker}" "${BACKUP_TIME:=03:10}" "${BACKUP_KEEP_DAYS:=14}" "${BACKUP_ON_START:=1}"
log() { echo "[labtracker backup] $(date '+%F %T') $*"; }

run_backup() {
  local day; day=$(date +%F)
  local tmp="/backups/.${ODOO_DB}-${day}.dump.part"
  log "dumping database ${ODOO_DB}"
  if ! pg_dump -Fc -d "$ODOO_DB" -f "$tmp"; then
    rm -f "$tmp"; log "dump FAILED"; return 1
  fi
  mv "$tmp" "/backups/${ODOO_DB}-${day}.dump"
  if [ -d "/odoo-data/filestore/${ODOO_DB}" ]; then
    tar czf "/backups/.filestore-${day}.tgz.part" -C /odoo-data/filestore "${ODOO_DB}" \
      && mv "/backups/.filestore-${day}.tgz.part" "/backups/filestore-${day}.tgz"
  fi
  find /backups -maxdepth 1 -type f \( -name '*.dump' -o -name '*.tgz' \) -mtime "+${BACKUP_KEEP_DAYS}" -print -delete | sed 's/^/removed old: /' || true
  log "done: $(ls -1 /backups | grep -c -E '\.dump$') dumps kept"
}

seconds_until() {  # seconds until the next HH:MM
  local target now
  target=$(date -d "today ${1}" +%s); now=$(date +%s)
  [ "$target" -le "$now" ] && target=$(date -d "tomorrow ${1}" +%s)
  echo $((target - now))
}

until pg_isready -q; do sleep 5; done
if [ "${1:-}" = "once" ]; then run_backup; exit 0; fi   # used by scripts/backup-now.sh
if [ "$BACKUP_ON_START" = "1" ] && [ ! -f "/backups/${ODOO_DB}-$(date +%F).dump" ]; then
  # wait until Odoo has finished building and provisioning the database (first boot takes a few minutes)
  until [ "$(psql -d "$ODOO_DB" -Atc "select 1 from ir_config_parameter where key='labtracker.provisioned_version'" 2>/dev/null)" = "1" ]; do
    log "waiting for database ${ODOO_DB} to be ready"; sleep 30
  done
  run_backup || log "startup backup FAILED"
fi
while true; do
  s=$(seconds_until "$BACKUP_TIME"); log "next backup in $((s / 3600))h $(( (s % 3600) / 60 ))m"
  sleep "$s"
  run_backup || log "backup FAILED"
done
