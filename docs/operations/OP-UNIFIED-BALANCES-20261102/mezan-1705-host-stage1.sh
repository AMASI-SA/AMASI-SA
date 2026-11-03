#!/usr/bin/env bash
# PR #1705 only. Run from a trusted Emergent terminal; never substitute a PR test-merge SHA.
set -Eeuo pipefail
umask 077
stop() { echo "STOP: $*" >&2; exit 1; }
[[ $# == 1 && "$1" =~ ^[0-9a-f]{40}$ ]] || stop "Pass the ACTUAL post-merge SHA as the only argument."
M=$1
J=7d1be020b9559ea294b6ea3f7511f37ee5e6a847
H=28f7875f1dade085c6a2ec5eb1b159ca8305c338
A=0275986594f8759a0a7b6810b9e5c2c6f8aed9c6
A_TREE=2bba8e3bdea3215266ec419be7f1fb237b47a37a
B=4de891930a9856c45b7d1579eb97cdf2f87fbf39
TREE=f2ca0af8322b05032834f1fde9bf7128c7575f9f
RID=rg5-6199c663b5a4e369d55dd9fbc99b8c2cee096da0c36114f90604c7e58ffc695f
INTENT_BLOB=468ecaa5b7d694de317560b9932d037f36b22747
BRANCH=hotfix/prod-snap-meta-final
INTENT=release/release-intent-v5.json
[[ "$B" =~ ^[0-9a-f]{40}$ && "$TREE" =~ ^[0-9a-f]{40}$ && "$INTENT_BLOB" =~ ^[0-9a-f]{40}$ && "$RID" =~ ^rg5-[0-9a-f]{64}$ ]] || stop "B3 pins missing or invalid; this is not a runnable release"
[[ "$M" != "$J" && "$M" != "$H" && "$M" != "$A" && "$M" != "$B" ]] || stop "M must be the reviewed merge commit."
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
  [[ "$(git rev-list --parents -n 1 "$M")" == "$M $H $B" ]] || stop "M is not the exact H+B3 GitHub merge"
  [[ "$(git rev-list --parents -n 1 "$B")" == "$B $A" ]] || stop "B is not the intent-only child of A"
  [[ "$(git rev-parse "$M^{tree}")" == "$TREE" && "$(git rev-parse "$B^{tree}")" == "$TREE" ]] || stop "M/B tree mismatch"
  [[ "$(git rev-list --parents -n 1 "$A")" == "$A $H" ]] || stop "A3 parent differs from the reviewed H"
  [[ "$(git rev-parse "$A^{tree}")" == "$A_TREE" ]] || stop "A3 tree mismatch"
  git merge-base --is-ancestor "$J" "$H" || stop "J is not an ancestor of H"
  git merge-base --is-ancestor "$H" "$A" || stop "H is not an ancestor of A3"
  local ref hist
  for ref in "$J" "$H" "$A"; do
    [[ "$(git rev-parse "$ref:$INTENT")" == f492e99086903fd980472b8597fe6b14499aefe8 ]] || stop "Inherited intent differs at $ref"
  done
  for ref in "$H" "$A"; do
    hist="$(git rev-list --full-history "$J..$ref" -- "$INTENT")" || stop "Cannot verify intent history"
    [[ -z "$hist" ]] || stop "Unexpected intent edit/revert before B3"
  done
  [[ "$(git diff --name-only "$A" "$B")" == "$INTENT" ]] || stop "A to B changed more than the intent"
  [[ "$(git rev-parse "$M:$INTENT")" == "$INTENT_BLOB" ]] || stop "Intent blob mismatch"
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
[[ "$local_head" == "$J" || "$local_head" == "$H" || "$local_head" == "$M" ]] || stop "Unexpected host HEAD. Preserve and reconcile it before proceeding."
remote_matches
git fetch --no-tags origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
[[ "$(git rev-parse "origin/$BRANCH")" == "$M" ]] || stop "Remote changed during fetch"
reviewed_graph
inactive
tracked_clean
remote_matches
[[ "$(git rev-parse HEAD)" == "$local_head" ]] || stop "Host HEAD changed during preflight"
# Inspect only paths the fast-forward would add/replace, including ignored files.
# Unlike status --untracked-files=no, this also catches ignored/symlink collisions.
python3 -B - "$local_head" "$M" <<'CHECK_PATHS'
import os, stat, subprocess, sys
from pathlib import Path
def entries(rev):
    raw=subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", rev])
    return {os.fsdecode(p) for p in raw.split(b"\0") if p}
old,new=map(entries,sys.argv[1:])
for name in sorted(new-old):
    p=Path(name)
    if os.path.lexists(p):
        sys.exit(f"STOP: target path already exists outside old tracked files: {name!r}")
    for parent in p.parents:
        if parent==Path("."): break
        if os.path.lexists(parent) and not stat.S_ISDIR(os.lstat(parent).st_mode) and str(parent) not in old:
            sys.exit(f"STOP: untracked/ignored file or symlink obstructs path: {str(parent)!r}")
print("Incoming tracked paths have no untracked or ignored collisions")
CHECK_PATHS
inactive
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
LOGS="$(mktemp -d /tmp/mezan-1705-materialization-XXXXXXXX)"
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

