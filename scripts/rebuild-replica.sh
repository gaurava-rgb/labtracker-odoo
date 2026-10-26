#!/usr/bin/env bash
# Throws away the read-only copy and copies the main database again. Use after a restore or if check-replica.sh fails.
set -euo pipefail; . "$(dirname "$0")/common.sh"
docker compose stop db_replica
docker compose run --rm --no-deps --entrypoint sh db_replica -c 'rm -rf /var/lib/postgresql/data/* /var/lib/postgresql/data/.[!.]* 2>/dev/null; echo "replica data folder emptied"'
docker compose up -d db_replica
echo "Replica is copying the primary. Check in a minute with: ./scripts/check-replica.sh"
