#!/usr/bin/env bash
# Remove only records owned by this test fixture, including its users.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 tools/test_data.py clean "$@"
