#!/usr/bin/env bash
# Takes a backup right now into ./backups (database dump + filestore), without waiting for the nightly run.
set -euo pipefail; . "$(dirname "$0")/common.sh"
mkdir -p backups
docker compose run --rm --no-deps backup once
ls -la backups | tail -n +2
