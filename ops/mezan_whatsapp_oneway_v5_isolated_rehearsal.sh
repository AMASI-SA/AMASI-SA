#!/usr/bin/env bash
set -euo pipefail

APP=/app
BRANCH=hotfix/prod-snap-meta-final
M=b020f3e918915a5873366ddcaab1db0244ef8e26
P=55995c02c2bdcf4ec63fe37ea3c14983244952c5
J=53e52767a1bf2b4efd4ec796e8c59de9425c8839
RELEASE_ID=rg5-54047ecc26066c6cf00503af8e8097fafc3b22dbb963aaf5fbba8eaee23eec47

cd "$APP"
echo '=== CHECK SOURCE AND PRESERVE /app ==='
test "$(git branch --show-current)" = "$BRANCH"
test "$(git rev-parse HEAD)" = "$M"
test -z "$(git status --porcelain --untracked-files=no)"
test "$(git ls-remote origin refs/heads/$BRANCH | awk '{print $1}')" = "$M"
BEFORE_HASH="$(git ls-files --others --exclude-standard -z | sha256sum | awk '{print $1}')"
BEFORE_COUNT="$(git ls-files --others --exclude-standard -z | tr -cd '\000' | wc -c)"
test "$BEFORE_COUNT" -eq 433
python3 -B scripts/production_release_guard.py status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("active") is False and not d.get("incomplete_lease_directory"); print("RELEASE_GUARD_AVAILABLE")'

ROOT="$(mktemp -d /tmp/mezan-oneway-v5-rehearsal-XXXXXXXX)"
echo "ISOLATED_REHEARSAL_ROOT=$ROOT"
echo '=== COPY REVIEWED SOURCE WITHOUT IGNORED FILES ==='
git clone --local --no-hardlinks --single-branch --branch "$BRANCH" "$APP" "$ROOT/repo" > "$ROOT/clone.log" 2>&1
cd "$ROOT/repo"
test "$(git rev-parse HEAD)" = "$M"
test -z "$(git status --porcelain --untracked-files=no)"
test ! -e backend/release_identity.json
test ! -e frontend/build
test ! -e frontend/.release/reproducible-build.json

echo '=== RUN ISOLATED GOVERNED V5 CLOUD BUILD ==='
if ! python3 -B scripts/emergent_deployment_adapter.py build > "$ROOT/build.log" 2>&1; then
  echo 'V5_ISOLATED_BUILD_FAILED_NO_APP_CHANGE'
  echo "BUILD_LOG=$ROOT/build.log"
  exit 2
fi

echo '=== VERIFY ISOLATED REVIEWED IDENTITY ==='
python3 -B scripts/verify_frontend_build.py --expected-git-sha "$P" --reviewed-intent-v5 > "$ROOT/frontend-verify.log" 2>&1
python3 -B scripts/emergent_deployment_adapter.py verify-packages > "$ROOT/packages-verify.log" 2>&1
export M P J RELEASE_ID
python3 -B -c 'import json,os; from pathlib import Path; j=lambda p:json.loads(Path(p).read_text()); intent=j("release/release-intent-v5.json"); identity=j("backend/release_identity.json"); meta=j("frontend/build/build-meta.json"); proof=j("frontend/.release/reproducible-build.json"); assert intent["schema_version"]==2 and intent["source_git_sha"]==os.environ["P"] and intent["source_base_git_sha"]==os.environ["J"]; assert identity==intent["runtime_identity"] and identity["release_id"]==os.environ["RELEASE_ID"]; assert meta["git_sha"]==os.environ["P"] and proof["git_sha"]==os.environ["P"]; print("V5_ISOLATED_IDENTITY_PASS")'
test "$(git rev-parse HEAD)" = "$M"
test -z "$(git status --porcelain --untracked-files=no)"

cd "$APP"
test "$(git rev-parse HEAD)" = "$M"
test -z "$(git status --porcelain --untracked-files=no)"
test "$(git ls-files --others --exclude-standard -z | sha256sum | awk '{print $1}')" = "$BEFORE_HASH"
test "$(git ls-files --others --exclude-standard -z | tr -cd '\000' | wc -c)" -eq 433
python3 -B scripts/production_release_guard.py status | python3 -c 'import json,sys; assert json.load(sys.stdin).get("active") is False'

echo 'ISOLATED_GOVERNED_BUILD_PASS'
echo "REHEARSAL_REPO=$ROOT/repo"
echo "RELEASE_ID=$RELEASE_ID"
echo 'APP_UNTRACKED_433_NAMES_PRESERVED'
echo 'NO_APP_ARTIFACT_SWAP_NO_DEPLOY_NO_RESTART_NO_LEASE'
