#!/usr/bin/env bash
# SAFE SOURCE FAST-FORWARD ONLY. No build, lease, deployment, restart, reset, or clean.
set -euo pipefail
cd /app
EXPECTED_OLD="19ea1404010b9c5e5135e5dbb4b40657157ff2b1"
EXPECTED_NEW="53e52767a1bf2b4efd4ec796e8c59de9425c8839"
BRANCH="hotfix/prod-snap-meta-final"
fail() { echo "SAFE_FF_STOP: $*" >&2; exit 2; }

test "$(git branch --show-current)" = "$BRANCH" || fail "wrong branch"
test "$(git rev-parse HEAD)" = "$EXPECTED_OLD" || fail "local HEAD changed"
test -z "$(git status --porcelain --untracked-files=no)" || fail "tracked files modified"
origin="$(git remote get-url origin)"
case "$origin" in
  https://github.com/AMASI-SA/AMASI-SA|https://github.com/AMASI-SA/AMASI-SA.git|git@github.com:AMASI-SA/AMASI-SA.git) ;;
  *) fail "unexpected Git remote" ;;
esac
STATUS="$(python3 -B scripts/production_release_guard.py status)"
export STATUS
python3 - <<'PY'
import json, os
v=json.loads(os.environ["STATUS"])
if v.get("active") is not False or v.get("incomplete_lease_directory"):
    raise SystemExit("SAFE_FF_STOP: active or incomplete production lease")
print("LEASE_CLEAR")
PY

git fetch --no-tags origin "$BRANCH"
test "$(git rev-parse --verify "FETCH_HEAD^{commit}")" = "$EXPECTED_NEW" || fail "remote production moved"
git merge-base --is-ancestor "$EXPECTED_OLD" "$EXPECTED_NEW" || fail "non-fast-forward ancestry"

export EXPECTED_OLD EXPECTED_NEW BRANCH
python3 - <<'PY'
import hashlib, json, os, pathlib, subprocess, sys
root=pathlib.Path.cwd()
expected_old=os.environ["EXPECTED_OLD"]
expected_new=os.environ["EXPECTED_NEW"]

def run(*args):
    return subprocess.check_output(["git", *args])
def names(*args):
    return tuple(sorted(filter(None,run(*args).split(b"\0"))))
def fail(reason):
    raise SystemExit("SAFE_FF_STOP: "+reason)
tracked_before=run("status","--porcelain","--untracked-files=no")
if tracked_before.strip():
    fail("tracked worktree changed before update")
untracked_before=names("ls-files","--others","--exclude-standard","-z")
untracked_digest=hashlib.sha256(b"\0".join(untracked_before)).hexdigest()
new_paths=names("diff","--name-only","-z","--diff-filter=ACRT",expected_old,expected_new,"--")
for raw in new_paths:
    candidate=raw.decode("utf-8","surrogateescape")
    # An incoming tracked destination must not replace any existing untracked,
    # ignored file, symlink, directory, or parent of an existing untracked file.
    if subprocess.run(["git","ls-files","--error-unmatch","--",candidate],
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
        continue
    path=root/candidate
    if os.path.lexists(path):
        fail("new tracked path collides with local file or directory: "+repr(candidate))
    for parent in path.parents:
        if parent==root: break
        if os.path.lexists(parent) and (parent.is_symlink() or not parent.is_dir()):
            fail("incoming tracked path has blocking local parent: "+repr(candidate))
generated=(
  "backend/release_identity.json",
  "frontend/build/build-meta.json",
  "frontend/.release/reproducible-build.json"
)
def digest(path):
    item=root/path
    if not item.is_file() or item.is_symlink(): fail("expected generated file missing/unsafe: "+path)
    return hashlib.sha256(item.read_bytes()).hexdigest()
before={p:digest(p) for p in generated}
print("SAFE_PRECHECK_OK",json.dumps({
 "incoming_tracked_destinations":len(new_paths),
 "untracked_file_count":len(untracked_before),
 "untracked_path_digest":untracked_digest[:12],
 "generated_files_preserved":len(before)
}))
subprocess.run(["git","merge","--ff-only",expected_new],check=True)
if run("rev-parse","HEAD").decode().strip()!=expected_new:
    fail("post-merge HEAD mismatch")
if run("status","--porcelain","--untracked-files=no").strip():
    fail("post-merge tracked worktree is dirty")
if untracked_before != names("ls-files","--others","--exclude-standard","-z"):
    fail("untracked names changed: stop before any publish")
if before!={p:digest(p) for p in generated}:
    fail("generated files were modified: stop")
print("SOURCE_FAST_FORWARD_PASS",expected_new)
print("UNTRACKED_PRESERVED",len(untracked_before))
print("GENERATED_PROOFS_UNTOUCHED_BUT_STALE")
print("NO_RELEASE_PREPARE_NO_REPUBLISH_NO_RESTART")
PY
