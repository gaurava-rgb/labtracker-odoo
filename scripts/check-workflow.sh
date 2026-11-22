#!/usr/bin/env bash
# Verify the real Odoo workflow and structured blocking without committing test data.
set -euo pipefail
. "$(dirname "$0")/common.sh"
docker compose exec -T odoo odoo shell -c /etc/odoo/odoo.conf -d "$ODOO_DB" --no-http --log-level=error < tools/check_workflow_orm.py
