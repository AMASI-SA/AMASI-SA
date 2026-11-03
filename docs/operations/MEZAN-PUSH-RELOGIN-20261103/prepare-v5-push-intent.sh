#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/app
PROD_BRANCH=hotfix/prod-snap-meta-final
SOURCE_BRANCH=codex/operational-push-relogin-dedup-20261103
RELEASE_BRANCH=codex/mezan-v5-operational-push-dedup-intent-20261103
J=7d1be020b9559ea294b6ea3f7511f37ee5e6a847
A=49e4c3f69f3483f207ca55a848776cb66cdbdd70

cd "$ROOT"
echo '=== 1. SAFE HOST & SOURCE PREFLIGHT ==='
python3 -B scripts/production_release_guard.py status | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("active") is False, "STOP: a release lease is active"
assert not s.get("incomplete_lease_directory"), "STOP: incomplete lease detected"
print("RELEASE LOCK: CLEAR")
'
# Prevent a new release from proceeding until the previous Production J
# has been cloud-published and its runtime identity verified, not merely merged.
python3 - "$J" <<'PY'
import json,sys,urllib.request
want=sys.argv[1]
request=urllib.request.Request(
  "https://mezansalla.com/api/health",
  headers={"Accept":"application/json","Cache-Control":"no-cache","User-Agent":"MezanV5HostPreviousReleaseGuard/1.0"})
with urllib.request.urlopen(request,timeout=20) as response:
    payload=json.load(response)
release=payload.get("release") or {}
assert payload.get("ok") is True
assert release.get("verified_identity_available") is True
assert release.get("deployment_git_sha") == want, (
    "STOP: Cloud Publish/verify previous Production J first")
print("PREVIOUS_PRODUCTION_J_LIVE: VERIFIED")
PY
test -z "$(git status --porcelain --untracked-files=no)" || {
  echo 'STOP: tracked changes exist in /app; preserve this work before continuing.'
  exit 3
}
git config --get user.name >/dev/null
git config --get user.email >/dev/null

git fetch --no-tags origin \
  "refs/heads/$PROD_BRANCH:refs/remotes/origin/$PROD_BRANCH" \
  "refs/heads/$SOURCE_BRANCH:refs/remotes/origin/$SOURCE_BRANCH"
test "$(git rev-parse "origin/$PROD_BRANCH")" = "$J" || {
  echo 'STOP: production moved. Do not reuse this A/B identity.'
  exit 4
}
test "$(git rev-parse "origin/$SOURCE_BRANCH")" = "$A" || {
  echo 'STOP: reviewed source A changed.'
  exit 5
}
git merge-base --is-ancestor "$J" "$A"
if git show-ref --verify --quiet "refs/heads/$RELEASE_BRANCH"; then
  echo 'STOP: local B candidate branch already exists'; exit 6
fi
if test -n "$(git ls-remote --heads origin "refs/heads/$RELEASE_BRANCH")"; then
  echo 'STOP: remote B candidate already exists; inspect it before retrying'; exit 7
fi

echo '=== 2. ISOLATED SOURCE A WORKTREE ==='
WORKTREE="$(mktemp -d /tmp/mezan-ops-push-dedup-v5-XXXXXX)"
git worktree add --detach "$WORKTREE" "$A"
cd "$WORKTREE"
git switch -c "$RELEASE_BRANCH"
test "$(git rev-parse HEAD)" = "$A"
export PYTHONDONTWRITEBYTECODE=1
export REACT_APP_BACKEND_URL=https://mezansalla.com
export MEZAN_RELEASE_BOOTSTRAP_INTENT=1
unset MEZAN_RELEASE_USE_REVIEWED_INTENT || true
unset GITHUB_ACTIONS GITHUB_SHA GITHUB_WORKSPACE GITHUB_EVENT_NAME GITHUB_EVENT_PATH || true

echo '=== 3. REPRODUCIBLE FRONTEND A/B BUILD ==='
python3 -B scripts/frontend_release_toolchain.py ensure
python3 -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend install --frozen-lockfile --non-interactive
python3 -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend build:release
python3 -B scripts/verify_frontend_build.py --expected-git-sha "$A"

echo '=== 4. FREEZE INTENT ONLY B ==='
FROZEN="$(mktemp /tmp/mezan-ops-push-dedup-intent-XXXXXXXX.json)"
python3 -B scripts/emergent_deployment_adapter.py freeze-intent \
  --source-git-sha "$A" --source-base-git-sha "$J" \
  --branch "$PROD_BRANCH" --output "$FROZEN" > /dev/null
cp "$FROZEN" release/release-intent-v5.json
python3 - "$A" "$J" "$PROD_BRANCH" <<'PY'
import json,sys
with open('release/release-intent-v5.json') as f:
    intent=json.load(f)
a,j,branch=sys.argv[1:]
assert intent['protocol_version']==5
assert intent['source_git_sha']==a
assert intent['source_base_git_sha']==j
assert intent['branch']==branch
assert intent['runtime_identity']['source_git_sha']==a
print('FROZEN INTENT:', intent['runtime_identity']['release_id'])
PY

test "$(git diff --name-only)" = 'release/release-intent-v5.json'
git diff --check
git add -- release/release-intent-v5.json
test "$(git diff --cached --name-only)" = 'release/release-intent-v5.json'
git commit -m 'release(v5): freeze idempotent operational push notification intent'
B="$(git rev-parse HEAD)"
test "$(git rev-parse HEAD^)" = "$A"

echo '=== 5. VERIFY REVIEWED INTENT B ==='
unset MEZAN_RELEASE_BOOTSTRAP_INTENT || true
LOG="$(mktemp /tmp/mezan-ops-push-dedup-build-XXXXXXXX.log)"
if python3 -B scripts/emergent_deployment_adapter.py build > "$LOG" 2>&1; then
  echo 'Governed build: PASS'
else
  echo 'Governed build: FAILED'; tail -n 55 "$LOG"; exit 8
fi
if python3 -B scripts/emergent_deployment_adapter.py verify-packages > "$LOG" 2>&1; then
  echo 'Package boundaries: PASS'
else
  tail -n 55 "$LOG"; exit 9
fi
if python3 -B scripts/verify_frontend_build.py \
  --expected-git-sha "$A" --reviewed-intent-v5 > "$LOG" 2>&1; then
  echo 'Frontend reviewed intent: PASS'
else
  tail -n 55 "$LOG"; exit 10
fi
python3 - "$A" <<'PY'
import json,sys
with open('release/release-intent-v5.json') as f:
    intent=json.load(f)
with open('backend/release_identity.json') as f:
    identity=json.load(f)
assert identity == intent['runtime_identity']
assert identity['source_git_sha'] == sys.argv[1]
print('Backend release identity: PASS')
PY
test -z "$(git status --porcelain --untracked-files=no)"

echo '=== 6. PUSH ONLY THE B CANDIDATE ==='
test "$(git ls-remote origin "refs/heads/$PROD_BRANCH" | cut -f1)" = "$J"
test "$(git ls-remote origin "refs/heads/$SOURCE_BRANCH" | cut -f1)" = "$A"
test -z "$(git ls-remote --heads origin "refs/heads/$RELEASE_BRANCH")"
git push origin "HEAD:refs/heads/$RELEASE_BRANCH"
python3 - "$A" "$B" "$J" "$RELEASE_BRANCH" <<'PY'
import json,sys
with open('release/release-intent-v5.json') as f:
    intent=json.load(f)
a,b,j,branch=sys.argv[1:]
print('\n=== REVIEWED V5 INTENT B READY ===')
print('SOURCE_A='+a)
print('INTENT_B='+b)
print('PRODUCTION_BASE_J='+j)
print('CANDIDATE_BRANCH='+branch)
print('RELEASE_ID='+intent['runtime_identity']['release_id'])
print('PRODUCTION_UNCHANGED=YES')
print('CLOUD_PUBLISH_NOT_STARTED=YES')
PY
