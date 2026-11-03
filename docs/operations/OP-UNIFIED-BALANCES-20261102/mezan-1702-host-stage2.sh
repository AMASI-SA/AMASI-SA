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

# Only after stage 1 PASS and the release owner is ready to publish immediately.
inactive
reviewed_local
remote_matches
LOGS="$(mktemp -d /tmp/mezan-1702-lease-XXXXXXXX)"
echo "Private guard result files: $LOGS"
python3 -B scripts/production_release_guard.py prepare --actor 'mezan-pr-1702' > "$LOGS/prepare.json" || stop "Prepare failed; inspect actual status. Never clear another release."
python3 - "$A" "$J" "$M" "$RID" "$BRANCH" "$LOGS/prepare.json" <<'PY'
import json,sys
a,j,m,r,b,p=sys.argv[1:]
with open(p,encoding="utf-8") as f: s=json.load(f)
if not(s.get("protocol_version")==5 and s.get("source_git_sha")==a and s.get("source_base_git_sha")==j and
       s.get("deployment_git_sha")==m and s.get("release_id")==r and s.get("branch")==b):
    sys.exit("STOP: prepared lease does not match; do not publish or automatically abort")
PY
python3 -B scripts/production_release_guard.py prepublish > "$LOGS/prepublish.json" || stop "Prepublish failed; do not publish or automatically abort."
python3 - "$A" "$M" "$RID" "$LOGS/prepublish.json" <<'PY'
import json,sys
a,m,r,p=sys.argv[1:]
with open(p,encoding="utf-8") as f: s=json.load(f)
if not(s.get("ready_to_publish") is True and s.get("protocol_version")==5 and s.get("source_git_sha")==a and
       s.get("deployment_git_sha")==m and s.get("release_id")==r):
    sys.exit("STOP: unexpected prepublish response; publish is not authorized by this script")
print(json.dumps({k:s[k] for k in ("ready_to_publish","source_git_sha","deployment_git_sha","release_id")},indent=2))
PY
echo "STAGE2_PASS: matching guard is ready. Cloud Publish was NOT performed."

