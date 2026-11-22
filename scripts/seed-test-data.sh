#!/usr/bin/env bash
# Add synthetic test records and three role-specific users. No image rebuild required.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 tools/test_data.py seed "$@"
