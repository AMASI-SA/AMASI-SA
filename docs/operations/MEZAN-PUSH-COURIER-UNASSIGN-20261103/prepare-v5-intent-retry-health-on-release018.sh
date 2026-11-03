#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/app
PROD_BRANCH=hotfix/prod-snap-meta-final
SOURCE_BRANCH=codex/operations-notifications-courier-unassign-no-return-release-20261103
RELEASE_BRANCH=codex/mezan-v5-push-courier-unassign-after018-intent-20261103
J=e01d4d3920fc5bf4a7280757ca2030c30b4be9c8
A=e19a9987a503365ccc6bf6fadb35c22288075323

cd "$ROOT"
echo '=== 1. SAFE HOST & SOURCE PREFLIGHT ==='
python3 -B scripts/production_release_guard.py status | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("active") is False, "STOP: a release lease is active"
assert not s.get("incomplete_lease_directory"), "STOP: incomplete lease detected"
print("RELEASE LOCK: CLEAR")
'
# Check that the latest already-published release 018 is live. Protocol v5
# public /api/health intentionally does NOT expose deployment_git_sha.
# Match the reviewed source SHA and release ID, never a missing public field.
test "$(git branch --show-current)" = "$PROD_BRANCH" || {
  echo 'STOP: host checkout is on another branch'; exit 2
}
test "$(git rev-parse HEAD)" = "$J" || {
  echo 'STOP: local /app HEAD differs from the verified release 018'; exit 2
}
python3 - <<'PY'
"""Bounded, read-only retries for transient public health GET errors only."""
import http.client
import json
import socket
import time
import urllib.error
import urllib.request

EXPECTED_SOURCE="0275986594f8759a0a7b6810b9e5c2c6f8aed9c6"
EXPECTED_RELEASE="rg5-6199c663b5a4e369d55dd9fbc99b8c2cee096da0c36114f90604c7e58ffc695f"
URL="https://mezansalla.com/api/health"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("STOP: unexpected public-health redirect")

opener=urllib.request.build_opener(NoRedirect)
timeouts=(15,25,35)
for attempt, timeout in enumerate(timeouts, 1):
    print(f"HEALTH_READ_ATTEMPT {attempt}/{len(timeouts)}", flush=True)
    try:
        request=urllib.request.Request(URL,headers={
            "Accept":"application/json",
            "Cache-Control":"no-cache, no-store, max-age=0",
            "Pragma":"no-cache",
            "User-Agent":"MezanV5PushCourierRelease018/2.0",
        })
        with opener.open(request,timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError("STOP: unexpected health HTTP response")
            payload=response.read(262145)
        if len(payload)>262144:
            raise RuntimeError("STOP: unexpected public-health response size")
        data=json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code not in (429,500,502,503,504):
            raise SystemExit(f"STOP: public health returned nonretryable HTTP {exc.code}")
        cause=f"HTTP {exc.code}"
    except (TimeoutError, socket.timeout, urllib.error.URLError,
            http.client.RemoteDisconnected, ConnectionError, OSError) as exc:
        cause=type(exc).__name__
    else:
        # A successful HTTP response is a strict identity gate: a different
        # release is NOT retried or silently treated as a transient error.
        if not isinstance(data,dict) or data.get("ok") is not True:
            raise SystemExit("STOP: public health payload is not OK")
        identity=data.get("release")
        if not isinstance(identity,dict):
            raise SystemExit("STOP: public release identity is missing")
        for key in ("verified_identity_available","backend_runtime_source_verified",
                    "release_control_source_bound","frontend_build_verified",
                    "critical_file_hashes_match"):
            if identity.get(key) is not True:
                raise SystemExit("STOP: public health identity flag failed: "+key)
        for key,expected in (
            ("protocol_version",5),
            ("source_git_sha",EXPECTED_SOURCE),
            ("git_sha",EXPECTED_SOURCE),
            ("release_id",EXPECTED_RELEASE),
            ("branch","hotfix/prod-snap-meta-final"),
        ):
            if identity.get(key)!=expected:
                raise SystemExit("STOP: public release identity mismatch: "+key)
        print("LATEST_PUBLISHED_RELEASE_018: VERIFIED",flush=True)
        break
    print(f"HEALTH_TRANSIENT_READ_FAILURE {attempt}/{len(timeouts)}: {cause}",flush=True)
    if attempt==len(timeouts):
        raise SystemExit("STOP: public health unavailable after bounded GET retries; no B was created")
    time.sleep(2*attempt)
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

# Guard source-scope independently from reviewed CI; no shipping or finance diffs.
ACTUAL_CHANGED="$(git diff --name-only "$J" "$A" | sort)"
EXPECTED_CHANGED="$(printf '%s\n' \
  backend/operational_instruction_notifications.py \
  backend/operational_push.py \
  backend/store_delivery_reassignment_routes.py \
  backend/tests/test_operational_push.py \
  backend/tests/test_store_delivery_unassignment.py | sort)"
test "$ACTUAL_CHANGED" = "$EXPECTED_CHANGED" || {
  echo 'STOP: unexpected source A paths'; exit 6
}
git rev-parse "$A^" | grep -Fx "$J"

echo '=== 2. ISOLATED SOURCE A WORKTREE ==='
WORKTREE="$(mktemp -d /tmp/mezan-ops-push-unassign-after018-v5-XXXXXX)"
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
FROZEN="$(mktemp /tmp/mezan-ops-push-unassign-after018-intent-XXXXXXXX.json)"
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
git commit -m 'release(v5): freeze alert dedup and unassign without physical-return attestation'
B="$(git rev-parse HEAD)"
test "$(git rev-parse HEAD^)" = "$A"

echo '=== 5. VERIFY REVIEWED INTENT B ==='
unset MEZAN_RELEASE_BOOTSTRAP_INTENT || true
LOG="$(mktemp /tmp/mezan-ops-push-unassign-after018-build-XXXXXXXX.log)"
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
