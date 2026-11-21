bash <<'MEZAN_RELEASE' 2>&1 | tee /tmp/mezan-legacy-location-release.log || printf '%s\n' 'STOP: do not publish; preserve the last error lines.'
set -Eeuo pipefail
step='initialization'
trap 'printf "\nFAILED: step=%s line=%s exit=%s\n" "$step" "$LINENO" "$?" >&2' ERR
cd /app
release_sha='c7d97f3cdf1ebcd99ed8a86975c463d9729b9d73'

guard_idle() {
  python scripts/production_release_guard.py status | python -c '
import json, sys
state = json.load(sys.stdin)
print(json.dumps(state, indent=2))
if state.get("active") is not False:
    raise SystemExit("STOP: active release; contact its owner.")
'
}

step='guard'
guard_idle
step='tracked_source'
git status --short --untracked-files=no
test -z "$(git status --porcelain --untracked-files=no)"
step='github_fetch'
GIT_TERMINAL_PROMPT=0 git fetch origin hotfix/prod-snap-meta-final
test "$(git rev-parse origin/hotfix/prod-snap-meta-final)" = "$release_sha"
git switch hotfix/prod-snap-meta-final
git merge --ff-only "$release_sha"
test "$(git rev-parse HEAD)" = "$release_sha"

step='clean_clone_rehearsal'
trial_dir="$(mktemp -d /tmp/mezan-legacy-location-v5.XXXXXX)"
git clone --local --no-hardlinks /app "$trial_dir"
(
  cd "$trial_dir"
  git checkout --detach "$release_sha"
  REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
)

step='shared_materialization'
guard_idle
test "$(git rev-parse HEAD)" = "$release_sha"
REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
step='prepare'
guard_idle
test "$(git rev-parse HEAD)" = "$release_sha"
python scripts/production_release_guard.py prepare --actor inventory-legacy-manual-20261121
step='prepublish'
python scripts/production_release_guard.py prepublish
printf '\nPREPUBLISH_PASSED: choose Re-publish changes now.\n'
MEZAN_RELEASE
