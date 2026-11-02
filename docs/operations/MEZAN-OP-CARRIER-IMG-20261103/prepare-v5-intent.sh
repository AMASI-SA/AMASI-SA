#!/usr/bin/env bash
set -Eeuo pipefail
cd /app

PROD=hotfix/prod-snap-meta-final
SOURCE_BRANCH=codex/mezan-operational-photo-and-courier-unassign-v5-20261103
INTENT_BRANCH=codex/mezan-operational-courier-intent-v5-20261103
J=9c550813afa5314dc498c20644033478aefbf80d
A=29f25c07b410161f04c4792e1844d2b9d4bbebea
export PYTHONDONTWRITEBYTECODE=1

echo '=== 1. RELEASE LEASE AND SOURCE SAFETY ==='
python3 -B scripts/production_release_guard.py status | python3 -c '
import json,sys
s=json.load(sys.stdin)
if s.get("active") is not False or s.get("incomplete_lease_directory"):
    sys.exit("STOP: An existing release lease needs its owner")
print("Release lease: CLEAR")'
test "$(git branch --show-current)" = "$PROD"
test -z "$(git status --porcelain --untracked-files=no)"
git config --get user.name >/dev/null
git config --get user.email >/dev/null

git fetch --no-tags origin \
  "refs/heads/$PROD:refs/remotes/origin/$PROD" \
  "refs/heads/$SOURCE_BRANCH:refs/remotes/origin/$SOURCE_BRANCH"
test "$(git rev-parse "origin/$PROD")" = "$J"
test "$(git rev-parse "origin/$SOURCE_BRANCH")" = "$A"
git merge-base --is-ancestor "$(git rev-parse HEAD)" "$J"
git merge --ff-only "origin/$PROD"
test "$(git rev-parse HEAD)" = "$J"
test "$(git rev-parse "$A^")" = "$J"

if git show-ref --verify --quiet "refs/heads/$INTENT_BRANCH"; then
  echo 'STOP: Local intent branch already exists'; exit 2
fi
if test -n "$(git ls-remote --heads origin "refs/heads/$INTENT_BRANCH")"; then
  echo 'STOP: Remote intent branch already exists'; exit 2
fi

echo '=== 2. ISOLATED SOURCE A WORKTREE ==='
WT="$(mktemp -d /tmp/mezan-operational-v5-XXXXXXXX)"
git worktree add --detach "$WT" "$A"
cd "$WT"
git switch -c "$INTENT_BRANCH"

export REACT_APP_BACKEND_URL=https://mezansalla.com
export MEZAN_RELEASE_BOOTSTRAP_INTENT=1
unset MEZAN_RELEASE_USE_REVIEWED_INTENT
unset GITHUB_ACTIONS GITHUB_SHA GITHUB_WORKSPACE GITHUB_EVENT_NAME GITHUB_EVENT_PATH

echo '=== 3. GOVERNED REPRODUCIBLE FRONTEND BUILD ==='
python3 -B scripts/frontend_release_toolchain.py ensure
python3 -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend install --frozen-lockfile --non-interactive
python3 -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend build:release
python3 -B scripts/verify_frontend_build.py --expected-git-sha "$A"

echo '=== 4. FREEZE SEPARATE INTENT B ==='
OUT="$(mktemp /tmp/mezan-operational-intent-XXXXXXXX.json)"
python3 -B scripts/emergent_deployment_adapter.py freeze-intent \
  --source-git-sha "$A" --source-base-git-sha "$J" \
  --branch "$PROD" --output "$OUT" >/dev/null
cp "$OUT" release/release-intent-v5.json
python3 - "$A" "$J" <<'PY'
import json,sys
source,base=sys.argv[1:]
with open("release/release-intent-v5.json",encoding="utf-8") as f:
    intent=json.load(f)
assert intent["protocol_version"] == 5
assert intent["source_git_sha"] == source
assert intent["source_base_git_sha"] == base
assert intent["runtime_identity"]["source_git_sha"] == source
print("Intent freeze: PASS")
print("Release ID:",intent["runtime_identity"]["release_id"])
PY

test "$(git diff --name-only)" = release/release-intent-v5.json
git diff --check
git add -- release/release-intent-v5.json
test "$(git diff --cached --name-only)" = release/release-intent-v5.json
git commit -m 'release(v5): reviewed Mezan operational photos and courier transfer intent'
B="$(git rev-parse HEAD)"
test "$(git rev-parse HEAD^)" = "$A"
test "$(git ls-remote origin "refs/heads/$PROD" | cut -f1)" = "$J"
test "$(git ls-remote origin "refs/heads/$SOURCE_BRANCH" | cut -f1)" = "$A"
git push origin "HEAD:refs/heads/$INTENT_BRANCH"
echo '=== CANDIDATE B PUSHED - PRODUCTION NOT PUBLISHED ==='
echo "Source A: $A"
echo "Intent B: $B"
echo "Candidate: $INTENT_BRANCH"
echo 'Production Publish: NOT STARTED'
