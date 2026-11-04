#!/usr/bin/env bash
set -euo pipefail
# run.sh REPO SHA MONGOD PYTHON RESULT_JSON [extra_smoke.py]
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT
git -C "$1" archive "$2" backend scripts/benchmark_dashboard_contention.py | tar -x -C "$temp"
export PYTHONPATH="$temp/scripts:$temp/backend:$temp/backend/tests" PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 JWT_SECRET=local-mobile-smoke-only
"$4" "$(dirname "$0")/${6:-probe.py}" "$3" "$(git -C "$1" rev-parse "$2")" > "$5"
