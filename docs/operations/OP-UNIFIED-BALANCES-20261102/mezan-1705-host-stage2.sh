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

# Only after stage 1 PASS and the release owner is ready to publish immediately.
inactive
reviewed_local
remote_matches
LOGS="$(mktemp -d /tmp/mezan-1705-lease-XXXXXXXX)"
echo "Private guard result files: $LOGS"
python3 -B scripts/production_release_guard.py prepare --actor 'owner-pr1705' > "$LOGS/prepare.json" || stop "Prepare failed; inspect actual status. Never clear another release."
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

