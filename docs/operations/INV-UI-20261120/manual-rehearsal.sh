bash <<'MEZAN_REHEARSAL' 2>&1 | tee /tmp/mezan-inventory-rehearsal.log || printf '%s\n' 'STOP: rehearsal failed. Send the last log lines; do not publish.'
set -Eeuo pipefail
step='initialization'
trap 'printf "\nFAILED: step=%s line=%s exit=%s\n" "$step" "$LINENO" "$?" >&2' ERR
cd /app
release_sha='5019e957cd1bb578f7b7375099547a470e51cb05'

step='guard'
python scripts/production_release_guard.py status | python -c '
import json, sys
state = json.load(sys.stdin)
print(json.dumps(state, indent=2))
if state.get("active") is not False:
    raise SystemExit("STOP: active release; contact its owner.")
'

step='tracked_source'
git status --short --untracked-files=no
test -z "$(git status --porcelain --untracked-files=no)"
test "$(git branch --show-current)" = 'hotfix/prod-snap-meta-final'
test "$(git rev-parse HEAD)" = "$release_sha"

step='github_fetch'
GIT_TERMINAL_PROMPT=0 git fetch origin hotfix/prod-snap-meta-final
step='remote_source'
test "$(git rev-parse origin/hotfix/prod-snap-meta-final)" = "$release_sha"

step='clean_clone'
trial_dir="$(mktemp -d /tmp/mezan-inventory-v5.XXXXXX)"
printf 'REHEARSAL_DIR=%s\n' "$trial_dir"
git clone --local --no-hardlinks /app "$trial_dir"
cd "$trial_dir"
git checkout --detach "$release_sha"

step='governed_build'
REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
printf '\nREHEARSAL_PASSED: no lease created, no publication performed.\n'
MEZAN_REHEARSAL
