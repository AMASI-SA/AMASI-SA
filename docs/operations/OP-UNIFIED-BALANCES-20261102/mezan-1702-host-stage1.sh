#!/usr/bin/env bash
# PR #1702 only. Run from a trusted Emergent terminal; never substitute a PR test-merge SHA.
set -Eeuo pipefail
umask 077
stop() { echo "STOP: $*" >&2; exit 1; }
[[ $# == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || stop "Pass the ACTUAL post-merge SHA as the only argument."
M=$1
J=7d1be020b9559ea294b6ea3f7511f37ee5e6a847
A=3a13f952b2909ac8e6eac2d0f7cc3a59f879e767
B=4b7eed3b40aae718a6652486010342c9d3701261
TREE=d0072df2a551403e09c4f5daee61936bfd15b0fe
RID=rg5-379d3096ad212f750c8f36d0bf0506a8b37556873f5d75728f3ebff0f92bc50b
BRANCH=hotfix/prod-snap-meta-final
INTENT=release/release-intent-v5.json
[[ "$M" != "$J" && "$M" != "$A" && "$M" != "$B" ]] || stop "M must be the reviewed merge commit."
cd /app
[[ "$(git rev-parse --show-toplevel)" == /app ]] || stop "Unexpected worktree"
[[ "$(git symbolic-ref --short -q HEAD)" == "$BRANCH" ]] || stop "Unexpected branch; do not switch branches automatically"
tracked_clean() {
  local status_output
  status_output="$(git status --porcelain --untracked-files=no)" || stop "Unable to inspect tracked state"
  [[ -z "$status_output" ]] || stop "Tracked changes present"
}
inactive() {
  python3 -B scripts/production_release_guard.py status | python3 -c '
import json,sys
s=json.load(sys.stdin)
if s.get("active") is not False or s.get("incomplete_lease_directory"):
    sys.exit("STOP: active or incomplete lease; do not clear it")
print("guard inactive, no incomplete lease")
'
}
remote_matches() {
  local ref
  ref="$(git ls-remote --exit-code --refs origin "refs/heads/$BRANCH")" || stop "Cannot establish remote branch"
  [[ "$ref" == "$(printf '%s\trefs/heads/%s' "$M" "$BRANCH")" ]] || stop "Remote production head differs from expected M"
}
reviewed_graph() {
  [[ "$(git rev-list --parents -n 1 "$M")" == "$M $J $B" ]] || stop "M is not the exact J+B GitHub merge"
  [[ "$(git rev-list --parents -n 1 "$B")" == "$B $A" ]] || stop "B is not the intent-only child of A"
  [[ "$(git rev-parse "$M^{tree}")" == "$TREE" && "$(git rev-parse "$B^{tree}")" == "$TREE" ]] || stop "M/B tree mismatch"
  git merge-base --is-ancestor "$J" "$A" || stop "J not ancestor of A"
  [[ "$(git diff --name-only "$A" "$B")" == "$INTENT" ]] || stop "A to B changed more than the intent"
  [[ "$(git rev-parse "$M:$INTENT")" == 5011ad276af952897aa990f9dde9ab330eb91095 ]] || stop "Intent blob mismatch"
}
reviewed_local() {
  [[ "$(git rev-parse HEAD)" == "$M" ]] || stop "Host HEAD differs from M"
  [[ "$(git symbolic-ref --short -q HEAD)" == "$BRANCH" ]] || stop "Host branch changed"
  tracked_clean
  reviewed_graph
  python3 -B - "$A" "$J" "$RID" "$BRANCH" <<'PY'
import json,sys
a,j,r,b=sys.argv[1:]
with open("release/release-intent-v5.json", encoding="utf-8") as f: intent=json.load(f)
identity=intent.get("runtime_identity") or {}
if not (intent.get("protocol_version")==5 and intent.get("schema_version")==2 and intent.get("source_git_sha")==a
        and intent.get("source_base_git_sha")==j and intent.get("branch")==b and identity.get("source_git_sha")==a
        and identity.get("source_base_git_sha")==j and identity.get("release_id")==r and identity.get("branch")==b):
    sys.exit("STOP: reviewed identity differs")
PY
}

# First guarded dependency is the lease status under the currently checked-out host code.
inactive
tracked_clean
local_head="$(git rev-parse HEAD)"
[[ "$local_head" == "$J" || "$local_head" == "$M" ]] || stop "Unexpected host HEAD. Preserve and reconcile it before proceeding."
remote_matches
git fetch --no-tags origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
[[ "$(git rev-parse "origin/$BRANCH")" == "$M" ]] || stop "Remote changed during fetch"
reviewed_graph
inactive
tracked_clean
remote_matches
# No checkout, reset, cleaning untracked files or force. Git must preserve even ignored colliding paths.
git merge --ff-only --no-edit --no-overwrite-ignore "$M"
reviewed_local
inactive
remote_matches
for var in GITHUB_ACTIONS GITHUB_SHA GITHUB_WORKSPACE MEZAN_RELEASE_BOOTSTRAP_INTENT; do
  [[ -z "$(printenv "$var" || true)" ]] || stop "Unexpected host build environment: $var"
done
export PYTHONDONTWRITEBYTECODE=1
export REACT_APP_BACKEND_URL=https://mezansalla.com
LOGS="$(mktemp -d /tmp/mezan-1702-materialization-XXXXXXXX)"
echo "Governed build and verification logs: $LOGS"
# The official adapter replaces only its documented generated/dependency outputs.
python3 -B scripts/emergent_deployment_adapter.py build >"$LOGS/build.log" 2>&1 || stop "Governed build failed; inspect local build.log"
python3 -B scripts/emergent_deployment_adapter.py verify-packages >"$LOGS/packages.log" 2>&1 || stop "Package validation failed"
python3 -B scripts/frontend_release_toolchain.py exec -- python scripts/verify_frontend_build.py \
  --expected-git-sha "$A" --reviewed-intent-v5 >"$LOGS/frontend.log" 2>&1 || stop "Frontend proof mismatch"
python3 -B - "$RID" "$A" <<'PY'
import json,sys
with open("release/release-intent-v5.json", encoding="utf-8") as f: intent=json.load(f)
with open("backend/release_identity.json", encoding="utf-8") as f: runtime=json.load(f)
if runtime!=intent["runtime_identity"] or runtime.get("release_id")!=sys.argv[1] or runtime.get("source_git_sha")!=sys.argv[2]:
    sys.exit("STOP: materialized backend identity mismatch")
PY
reviewed_local
remote_matches
inactive
printf 'STAGE1_PASS source=%s deployment=%s tree=%s release=%s\nNo lease or publish performed.\n' "$A" "$M" "$TREE" "$RID"

