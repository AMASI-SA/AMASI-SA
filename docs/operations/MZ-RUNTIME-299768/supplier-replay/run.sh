#!/usr/bin/env bash
# run.sh repo commit mongod python output-json
set -euo pipefail
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
git -C "$1" archive "$2" backend scripts/benchmark_dashboard_contention.py | tar -x -C "$root"
export PYTHONPATH="$root/scripts:$root/backend${EXTRA_TEST_DEPENDENCIES:+:$EXTRA_TEST_DEPENDENCIES}" PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 JWT_SECRET=synthetic-worker-replay-only
"$4" "$(dirname "$0")/probe.py" "$3" "$(git -C "$1" rev-parse "$2")" > "$5"
