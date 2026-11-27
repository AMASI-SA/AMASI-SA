#!/usr/bin/env bash
# TikTok multi-advertiser / Mezan v5: guarded Emergent handoff.
# Never publishes or modifies financial records by itself.
set -euo pipefail
cd /app || { echo 'BLOCKED: /app unavailable'; exit 10; }
BRANCH='hotfix/prod-snap-meta-final'
BASE_J='19ea1404010b9c5e5135e5dbb4b40657157ff2b1'
SOURCE_A='ee2f2eec85f9765f16850977024066be6b64253c'
MODE='prepare'
if [ "$#" -gt 0 ]; then MODE="$1"; fi

if [ "$MODE" = 'verify' ]; then
  python3 -B scripts/production_release_guard.py verify --url https://mezansalla.com
  python3 -B scripts/production_release_guard.py status
  exit 0
fi
if [ "$MODE" != 'prepare' ]; then
  echo 'Usage: bash /tmp/mezan_tiktok_v5_guarded_deploy.sh prepare|verify'
  exit 64
fi

echo '=== LOCAL SOURCE ==='
git rev-parse HEAD
if [ "$(git rev-parse HEAD)" != "$BASE_J" ]; then
  echo 'BLOCKED: /app is not the expected production base J; no reset will be attempted.'
  exit 11
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo 'BLOCKED: local tracked changes exist; preserve and review them first.'
  exit 12
fi
echo '=== RELEASE GUARD ==='
python3 -B scripts/production_release_guard.py status > /tmp/tiktok-v5-guard.json
python3 - <<'PY'
import json
from pathlib import Path
status = json.loads(Path('/tmp/tiktok-v5-guard.json').read_text())
print('ACTIVE:', status.get('active'))
if status.get('active') is not False:
    raise SystemExit('BLOCKED: a release lease is active or ambiguous')
PY

echo '=== FETCH REVIEWED PRODUCTION RELEASE ==='
git fetch --no-tags origin "$BRANCH"
TARGET="$(git rev-parse FETCH_HEAD)"
echo "GITHUB_TARGET=$TARGET"
if [ "$TARGET" = "$BASE_J" ]; then
  echo 'BLOCKED: TikTok release source A and reviewed intent B are not yet merged in production GitHub.'
  echo 'Do not use Manage Publishing yet.'
  exit 13
fi
git cat-file -e "$SOURCE_A^{commit}" || { echo 'BLOCKED: exact reviewed source A absent'; exit 14; }
git merge-base --is-ancestor "$BASE_J" "$TARGET" || { echo 'BLOCKED: non-fast-forward target'; exit 15; }
git merge-base --is-ancestor "$SOURCE_A" "$TARGET" || { echo 'BLOCKED: target missing exact TikTok source A'; exit 16; }
export TARGET BASE_J SOURCE_A BRANCH
python3 - <<'PY'
import json
import os
import subprocess
src, target = os.environ['SOURCE_A'], os.environ['TARGET']
changed = subprocess.check_output(
    ['git', 'diff', '--name-only', src, target, '--'], text=True
).splitlines()
if changed != ['release/release-intent-v5.json']:
    raise SystemExit('BLOCKED: reviewed B is not an intent-only change: ' + str(changed[:10]))
raw = subprocess.check_output(['git', 'show', f'{target}:release/release-intent-v5.json'])
intent = json.loads(raw)
expect = {
    'schema_version': 2,
    'protocol_version': 5,
    'source_git_sha': src,
    'source_base_git_sha': os.environ['BASE_J'],
    'branch': os.environ['BRANCH'],
}
for key, value in expect.items():
    if intent.get(key) != value:
        raise SystemExit('BLOCKED: reviewed intent mismatch on ' + key)
print('REVIEWED_INTENT_B_STRUCTURE_PASS')
PY

echo '=== FAST-FORWARD + FROZEN BUILD ==='
git merge --ff-only "$TARGET"
test "$(git rev-parse HEAD)" = "$TARGET"
python3 -B scripts/emergent_deployment_adapter.py build

echo '=== PREPARE AND PREPUBLISH ==='
python3 -B scripts/production_release_guard.py prepare --actor tiktok-multi-advertiser-20261127
python3 -B scripts/production_release_guard.py prepublish
echo 'PREPUBLISH_PASS: In the SAME Emergent project choose Manage Publishing -> Re-publish changes exactly once.'
echo 'After Deployment Succeeded: bash /tmp/mezan_tiktok_v5_guarded_deploy.sh verify'
