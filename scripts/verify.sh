#!/usr/bin/env bash
# Checks the running stack: apps, Lab rules, groups, fields, dashboard source, LAB- numbering.
set -euo pipefail; . "$(dirname "$0")/common.sh"
docker compose exec -T odoo python3 /opt/labtracker/init/verify.py "$@"
