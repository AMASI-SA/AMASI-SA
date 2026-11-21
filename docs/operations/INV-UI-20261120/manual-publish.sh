bash <<'MEZAN_RELEASE' 2>&1 | tee /tmp/mezan-inventory-release.log || printf '%s\n' 'STOP: preparation failed; do not publish. Preserve the log.'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'printf "STOP: line %s, exit %s; do not publish.\n" "$LINENO" "$?" >&2' ERR
cd /app

release_sha='5019e957cd1bb578f7b7375099547a470e51cb05'

guard_idle() {
  python scripts/production_release_guard.py status | python -c '
import json, sys
state = json.load(sys.stdin)
print(json.dumps(state, indent=2))
if state.get("active") is not False:
    raise SystemExit("STOP: active release; contact its owner. Do not clear it.")
'
}

guard_idle
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  printf '%s\n' 'STOP: tracked files have local changes. Preserve them before continuing.' >&2
  exit 1
fi
git fetch origin hotfix/prod-snap-meta-final
if [ "$(git rev-parse origin/hotfix/prod-snap-meta-final)" != "$release_sha" ]; then
  printf '%s\n' 'STOP: Production Git advanced. This release must be reviewed again.' >&2
  exit 1
fi
git switch hotfix/prod-snap-meta-final
git merge --ff-only "$release_sha"
test "$(git rev-parse HEAD)" = "$release_sha"

trial_dir="$(mktemp -d /tmp/mezan-inventory-v5.XXXXXX)"
git clone --local --no-hardlinks /app "$trial_dir"
(
  cd "$trial_dir"
  git checkout --detach "$release_sha"
  REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
)

guard_idle
test "$(git rev-parse HEAD)" = "$release_sha"
REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
python scripts/production_release_guard.py prepare --actor inventory-ui-manual-20261121
python scripts/production_release_guard.py prepublish
printf '%s\n' 'PREPUBLISH PASSED. Manually choose Re-publish changes now.'
printf '%s\n' 'After a NEW Deployment Succeeded, run the separate verify commands.'
MEZAN_RELEASE
