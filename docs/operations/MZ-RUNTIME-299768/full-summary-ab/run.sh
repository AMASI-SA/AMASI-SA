#!/usr/bin/env bash
# Usage: run.sh REPOSITORY FROZEN_REFERENCE MONGOD PYTHON OUT_JSON [orders] [block_sequence]
set -euo pipefail
repo=$(realpath "$1")
ref=$(realpath "$2")
mongod=$(realpath "$3")
python=$(realpath -s "$4")
output=$(realpath -m "$5")
size=${6:-512}
blocks=${7:-ABBA}
script=$(realpath "$(dirname "$0")/probe.py")
base=a56dbacd36678566eec3b5a76406db7582c3e7f2
candidate=d5531f3745e17dcb81dd50ba70239e5bc54ff5fe
test "$(git -C "$ref" rev-parse HEAD)" = 05b159dc4183f14adc7c370bfbd8014d8858e6b8
test -z "$(git -C "$ref" status --porcelain -- backend/tests/test_dashboard_b_baseline_mongo.py)"
tempdir=$(mktemp -d)
pid=
cleanup() {
  if test -n "$pid"; then kill "$pid" 2>/dev/null || true; wait "$pid" || true; fi
  rm -rf "$tempdir"
}
trap cleanup EXIT
mkdir -p "$tempdir"/{A,B,db,cgroup}
for arm in A B; do
  sha=$base; if test "$arm" = B; then sha=$candidate; fi
  git -C "$repo" archive "$sha" backend | tar -x -C "$tempdir/$arm"
done
# Fixture-only cgroup for stable admission on this shared host; real per-process resource measurements remain active.
echo 2147483648 > "$tempdir/cgroup/memory.max"
echo 134217728 > "$tempdir/cgroup/memory.current"
export MEZAN_CGROUP_ROOT="$tempdir/cgroup" PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 JWT_SECRET=synthetic-benchmark-only
port=$("$python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
"$mongod" --bind_ip 127.0.0.1 --port "$port" --dbpath "$tempdir/db" --nounixsocket --wiredTigerCacheSizeGB 0.25 --logpath "$tempdir/mongo.log" >/dev/null &
pid=$!
for ((i=0; i<${#blocks}; i++)); do
  arm=${blocks:i:1}
  cd "$tempdir/$arm/backend"
  PYTHONPATH=".:$ref/backend/tests" "$python" "$script" "$port" "$arm" "$i" "$size" > "$tempdir/$i.json"
done
"$python" "$script" collect "$tempdir" "$blocks" "$size" "$base" "$candidate" > "$output"
