#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; echo "STOP at line $LINENO (exit $rc). DO NOT CLOUD PUBLISH."; exit "$rc"' ERR

cd /app

PROD="hotfix/prod-snap-meta-final"
J="9c550813afa5314dc498c20644033478aefbf80d"
A="29f25c07b410161f04c4792e1844d2b9d4bbebea"
B="6c4c216c42a3cd65f246e31425e40749b266b4c8"
M="7d1be020b9559ea294b6ea3f7511f37ee5e6a847"
TREE="4a9f4d03531ad502733e169cf5bcf194a8f218f1"
RID="rg5-b3ee097a92d53974c278005cdf322c01be4b03d669f687326e2c7d61e0bd38ad"
export PYTHONDONTWRITEBYTECODE=1

echo "=== 1/5: HOST AND RELEASE LOCK ==="
test "$(git branch --show-current)" = "$PROD"
test -z "$(git status --porcelain --untracked-files=no)"
CURRENT="$(git rev-parse HEAD)"
if test "$CURRENT" != "$J" && test "$CURRENT" != "$M"; then
  echo "STOP: /app must be at reviewed J or M, actual=$CURRENT"
  exit 2
fi
python3 -B scripts/production_release_guard.py status | python3 -c '
import json,sys
x=json.load(sys.stdin)
assert x.get("active") is False and not x.get("incomplete_lease_directory"), "Active/incomplete release lease: STOP"
print("LEASE CLEAR")'

echo "=== 2/5: PIN AND FAST-FORWARD PRODUCTION ==="
git fetch --no-tags origin "refs/heads/$PROD:refs/remotes/origin/$PROD"
test "$(git rev-parse "origin/$PROD")" = "$M"
test "$(git rev-parse "$M^1")" = "$J"
test "$(git rev-parse "$M^2")" = "$B"
test "$(git rev-parse "$B^")" = "$A"
test "$(git rev-parse "$M^{tree}")" = "$TREE"
git merge --ff-only "origin/$PROD"
test "$(git rev-parse HEAD)" = "$M"
test -z "$(git status --porcelain --untracked-files=no)"

python3 - "$A" "$J" "$RID" <<'PY'
import json,sys
a,j,rid=sys.argv[1:]
with open("release/release-intent-v5.json",encoding="utf-8") as f:
    i=json.load(f)
assert i["protocol_version"] == 5
assert i["source_git_sha"] == a
assert i["source_base_git_sha"] == j
assert i["runtime_identity"]["source_git_sha"] == a
assert i["runtime_identity"]["release_id"] == rid
print("REVIEWED V5 RELEASE INTENT: PASS",rid)
PY

echo "=== 3/5: BUILD EXACT REVIEWED EMERGENT PACKAGE ==="
python3 -B scripts/emergent_deployment_adapter.py build

echo "=== 4/5: PREPARE GUARDED PRODUCTION RELEASE ==="
python3 -B scripts/production_release_guard.py prepare --actor "owner"

echo "=== 5/5: EXACT PREPUBLISH ACCEPTANCE ==="
CHECK="$(mktemp /tmp/mezan-v5-prepublish-XXXXXXXX.json)"
python3 -B scripts/production_release_guard.py prepublish > "$CHECK"
python3 - "$CHECK" "$M" "$A" "$RID" <<'PY'
import json,sys
path,m,a,rid=sys.argv[1:]
with open(path,encoding="utf-8") as f:
    x=json.load(f)
assert x.get("ready_to_publish") is True
assert x.get("deployment_git_sha") == m
assert x.get("source_git_sha") == a
assert x.get("release_id") == rid
assert x.get("protocol_version") == 5
print("READY_TO_CLOUD_PUBLISH: TRUE")
print("DEPLOYMENT_GIT_SHA:",m)
print("SOURCE_A:",a)
print("RELEASE_ID:",rid)
PY
echo "=== PREPUBLISH PASS — CLOUD PUBLISH HAS NOT STARTED ==="
echo "Manual next action: use Emergent Cloud Publish only for this reviewed deployment."
echo "After Cloud Publish, run once in /app:"
echo "python3 -B scripts/production_release_guard.py verify --url https://mezansalla.com"
