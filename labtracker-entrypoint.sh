#!/bin/bash
# Lab Tracker Odoo entrypoint. Safe to run on every boot:
#   1. write /etc/odoo/odoo.conf from env            4. upgrade lab_dashboard if the image ships a newer version
#   2. wait for Postgres, create/refresh db roles      5. start Odoo
#   3. create + initialise the database if missing     6. once Odoo answers, run init/provision.py (idempotent)
# Never prints a secret.
set -euo pipefail
log() { echo "[labtracker] $(date +%H:%M:%S) $*"; }

: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is required}"
: "${ODOO_MASTER_PASSWORD:?ODOO_MASTER_PASSWORD is required}"
: "${ODOO_DB:=labtracker}" "${DB_HOST:=db}" "${DB_PORT:=5432}" "${DB_USER:=odoo}"
: "${COMPANY_NAME:=Lab Tracker}" "${WORKERS:=2}" "${LOG_LEVEL:=info}" "${PROXY_MODE:=True}"
export ODOO_DB DB_HOST DB_PORT DB_USER
export ODOO_URL="http://localhost:8069" ODOO_PASSWORD="$ADMIN_PASSWORD"
INIT=/opt/labtracker/init
CONF=/etc/odoo/odoo.conf
MODULES="base,contacts,project,crm,maintenance,base_automation,lab_dashboard"

# 1. config -------------------------------------------------------------
umask 077
cat > "$CONF" <<CONF
[options]
admin_passwd = ${ODOO_MASTER_PASSWORD}
db_host = ${DB_HOST}
db_port = ${DB_PORT}
db_user = ${DB_USER}
db_password = ${POSTGRES_PASSWORD}
db_name = ${ODOO_DB}
dbfilter = ^${ODOO_DB}\$
list_db = False
proxy_mode = ${PROXY_MODE}
http_interface = 0.0.0.0
addons_path = /mnt/extra-addons
data_dir = /var/lib/odoo
workers = ${WORKERS}
max_cron_threads = 1
limit_memory_hard = 1677721600
limit_memory_soft = 1258291200
log_level = ${LOG_LEVEL}
log_handler = odoo.addons.rpc.controllers.xmlrpc:ERROR
CONF
umask 022

# 2. database server + roles --------------------------------------------
log "waiting for Postgres at ${DB_HOST}:${DB_PORT}"
python3 "$INIT/db_tool.py" wait
python3 "$INIT/db_tool.py" roles

# 3. first boot: create the database ------------------------------------
if [ "$(python3 "$INIT/db_tool.py" exists)" = "no" ]; then
  log "database '${ODOO_DB}' not found: creating it and installing ${MODULES} (takes a few minutes)"
  odoo -c "$CONF" -d "$ODOO_DB" -i "$MODULES" --stop-after-init --without-demo=all --no-http
  log "setting admin password and company name"
  odoo shell -c "$CONF" -d "$ODOO_DB" --no-http <<PY
import os
env['res.users'].browse(2).write({'password': os.environ['ADMIN_PASSWORD'], 'login': 'admin'})
env['res.company'].browse(1).write({'name': os.environ['COMPANY_NAME']})
env.cr.commit()
PY
  python3 "$INIT/db_tool.py" roles   # grants on the new database
  log "database ready"
else
  # 4. image updated with a newer lab_dashboard? upgrade the module before serving
  WANT=$(python3 -c "import ast,sys; print(ast.literal_eval(open('/mnt/extra-addons/lab_dashboard/__manifest__.py').read())['version'])")
  HAVE=$(python3 "$INIT/db_tool.py" module_version lab_dashboard)
  if [ -n "$HAVE" ] && [ "$HAVE" != "$WANT" ]; then
    log "lab_dashboard ${HAVE} -> ${WANT}: upgrading module"
    odoo -c "$CONF" -d "$ODOO_DB" -u lab_dashboard --stop-after-init --no-http
  fi
fi

# 6. provisioning runs in the background once Odoo answers ---------------
(
  if python3 "$INIT/provision.py"; then
    log "provisioning finished"
  else
    log "provisioning FAILED (see errors above). It will be retried on the next container start."
  fi
) &

# 5. Odoo in the foreground ----------------------------------------------
log "starting Odoo (db=${ODOO_DB}, workers=${WORKERS})"
exec odoo -c "$CONF" "$@"
